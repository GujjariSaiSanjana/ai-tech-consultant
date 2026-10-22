"""Pure scoring functions. Each returns (score in 0..1, detail dict for the run log)."""

from __future__ import annotations

import itertools
from collections.abc import Sequence
from typing import Any

from scope.domain.checks import KIND_RANK
from scope.domain.models import ChangeLog, Kind, Requirement, ScopeSnapshot
from scope.domain.transcript import Transcript, verify_evidence
from scope.evals.gold import Gold, GoldItem, align, matches

Detail = dict[str, Any]


def f1(precision: float, recall: float) -> float:
    return 0.0 if precision + recall == 0 else 2 * precision * recall / (precision + recall)


def requirement_f1(
    gold: Sequence[GoldItem], negatives: Sequence[GoldItem], reqs: Sequence[Requirement]
) -> tuple[float, Detail]:
    aligned = align(gold, reqs)
    recall = len(aligned) / len(gold) if gold else 1.0
    precision = len(aligned) / len(reqs) if reqs else 0.0
    false_traps = [n.statement for n in negatives if any(matches(n, r) for r in reqs)]
    missed = [g.gold_id for i, g in enumerate(gold) if i not in aligned]
    return f1(precision, recall), {
        "recall": round(recall, 3),
        "precision": round(precision, 3),
        "extracted": len(reqs),
        "missed": missed,
        "matched_not_requirements": false_traps,
    }


def recall_subset(
    gold: Gold, ids: Sequence[str], reqs: Sequence[Requirement]
) -> tuple[float, Detail]:
    items = [gold.by_id(i) for i in ids]
    aligned = align(items, reqs)
    return len(aligned) / len(items), {"found": [items[i].gold_id for i in aligned]}


def absent(negatives: Sequence[GoldItem], reqs: Sequence[Requirement]) -> tuple[float, Detail]:
    hits = [n.statement for n in negatives if any(matches(n, r) for r in reqs)]
    return (0.0 if hits else 1.0), {"present": hits}


def feasibility_accuracy(
    gold: Gold, ids: Sequence[str] | None, snap: ScopeSnapshot
) -> tuple[float, Detail]:
    items = [g for g in gold.requirements if ids is None or g.gold_id in ids]
    aligned = align(items, snap.requirements)
    feas = {f.requirement_id: f for f in snap.feasibility}
    rows = []
    for i, req in aligned.items():
        got = feas[req.id].kind if req.id in feas else None
        rows.append((items[i].gold_id, items[i].kind, got))
    correct = sum(1 for _, want, got in rows if want == got)
    # Unmatched gold items count as wrong: a requirement never found was never classified.
    return correct / len(items), {"rows": rows, "unmatched": len(items) - len(aligned)}


def question_coverage(expected: Sequence[str], snap: ScopeSnapshot) -> tuple[float, Detail]:
    asked = {q.category for q in snap.questions}
    hit = [c for c in expected if c in asked]
    return len(hit) / len(expected), {"covered": hit, "missing": sorted(set(expected) - asked)}


def estimate_sanity(snap: ScopeSnapshot, setting_max_hours: float) -> tuple[float, Detail]:
    tasks = [t for i in snap.items for t in i.tasks]
    checks: dict[str, bool] = {
        "model_gave_o_le_m_le_p": all(
            "hours_reordered" not in t.flags and "negative_hours_clamped" not in t.flags
            for t in tasks
        ),
        "every_requirement_covered": {r.id for r in snap.requirements}
        <= {rid for i in snap.items for rid in i.requirement_ids},
        "settings_within_cap": all(
            i.expected_hours <= setting_max_hours for i in snap.items if i.kind == "setting"
        ),
    }
    settings = [i.expected_hours for i in snap.items if i.kind == "setting"]
    custom = [i.expected_hours for i in snap.items if i.kind == "custom_dev"]
    checks["settings_cheaper_than_custom"] = not (settings and custom) or max(settings) < min(
        custom
    )
    ranks: dict[Kind, list[float]] = {}
    for i in snap.items:
        ranks.setdefault(i.kind, []).append(i.expected_hours)
    means = sorted(((KIND_RANK[k], sum(v) / len(v)) for k, v in ranks.items() if k != "unknown"))
    checks["mean_hours_increase_with_kind_cost"] = all(
        a[1] <= b[1] for a, b in itertools.pairwise(means)
    )
    checks["positive_price"] = bool(snap.estimate and snap.estimate.price_usd > 0)
    return sum(checks.values()) / len(checks), {"checks": checks}


def schema_validity(snap: ScopeSnapshot, transcripts: Sequence[Transcript]) -> tuple[float, Detail]:
    ScopeSnapshot.model_validate(snap.model_dump(mode="json"))
    ids = [r.id for r in snap.requirements]
    evidence = [e for r in snap.requirements for e in r.evidence]
    _, unverified = verify_evidence(evidence, transcripts)
    checks = {
        "ids_unique": len(ids) == len(set(ids)),
        "ids_code_format": all(i.startswith("REQ-") and len(i) == 12 for i in ids),
        "all_evidence_verifiable": not unverified,
        "every_requirement_has_feasibility": set(ids)
        == {f.requirement_id for f in snap.feasibility},
        "estimate_present": snap.estimate is not None,
    }
    return sum(checks.values()) / len(checks), {"checks": checks}


def _gold_key(gold: Gold, req: Requirement) -> str | None:
    for g in gold.requirements:
        if matches(g, req):
            return g.gold_id
    return None


def diff_f1(gold: Gold, v1: ScopeSnapshot, log: ChangeLog) -> tuple[float, Detail]:
    """Set-F1 over labelled changes. Prior requirements are mapped to gold IDs by keywords, so
    'modified'/'removed' compare by ID; new items are matched by keywords."""
    assert gold.follow_up is not None
    fu = gold.follow_up
    want: set[tuple[str, str]] = {(m.gold_id, "modified") for m in fu.modified}
    want |= {(r.gold_id, "removed") for r in fu.removed}
    want |= {(f"added:{i}", "added") for i in range(len(fu.added))}
    want |= {(f"oos:{i}", "out_of_scope") for i in range(len(fu.out_of_scope))}
    to_gold = {r.id: _gold_key(gold, r) for r in v1.requirements}
    got: set[tuple[str, str]] = set()
    for m in log.modified:
        got.add((to_gold.get(m.id) or m.id, "modified"))
    for r in log.removed:
        got.add((to_gold.get(r.id) or r.id, "removed"))
    for label, items, reqs in (
        ("added", fu.added, log.added),
        ("oos", fu.out_of_scope, log.out_of_scope),
    ):
        aligned = align(items, reqs)
        kind = "added" if label == "added" else "out_of_scope"
        got |= {(f"{label}:{i}", kind) for i in aligned}
        got |= {(r.id, kind) for r in reqs if r not in aligned.values()}
    tp = len(want & got)
    precision = tp / len(got) if got else 0.0
    recall = tp / len(want) if want else 1.0
    return f1(precision, recall), {
        "precision": round(precision, 3),
        "recall": round(recall, 3),
        "missed": sorted(map(str, want - got)),
        "extra": len(got - want),
    }


def change_requests_quoted(gold: Gold, log: ChangeLog) -> tuple[float, Detail]:
    assert gold.follow_up is not None
    quoted = [
        r
        for r in log.out_of_scope
        if any(
            r.id in cr.requirement_ids and cr.delta_hours > 0 and cr.price_usd > 0
            for cr in log.change_requests
        )
    ]
    aligned = align(gold.follow_up.out_of_scope, quoted)
    return len(aligned) / len(gold.follow_up.out_of_scope), {"quoted": len(quoted)}


def unchanged_preserved(
    gold: Gold, ids: Sequence[str], v1: ScopeSnapshot, log: ChangeLog
) -> tuple[float, Detail]:
    touched = {m.id for m in log.modified} | {r.id for r in log.removed}
    rows = {}
    for gid in ids:
        aligned = align([gold.by_id(gid)], v1.requirements)
        rows[gid] = bool(aligned) and aligned[0].id not in touched
    return sum(rows.values()) / len(rows), {"preserved": rows}
