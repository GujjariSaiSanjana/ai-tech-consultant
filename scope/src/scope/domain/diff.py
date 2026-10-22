"""Turn Reconciler findings into the next requirement list and a ChangeLog. Pure functions."""

from __future__ import annotations

from collections.abc import Sequence

from scope.domain.ids import change_request_id, normalize, requirement_id
from scope.domain.models import (
    ChangeLog,
    ChangeRequest,
    Modification,
    ReconcileFinding,
    Requirement,
    ScopeItem,
)
from scope.domain.pert import hours_by_requirement

OUT_OF_SCOPE_FLAG = "out_of_scope"
EXPANDED_FLAG = "expanded_scope"


def _from_finding(f: ReconcileFinding, rid: str, flags: list[str]) -> Requirement:
    return Requirement(
        id=rid,
        title=f.title,
        statement=f.statement,
        category=f.category,
        priority=f.priority,
        evidence=list(f.evidence),
        flags=flags,
    )


def apply_findings(
    prior: Sequence[Requirement],
    findings: Sequence[ReconcileFinding],
    *,
    from_version: int,
    to_version: int,
) -> tuple[list[Requirement], ChangeLog]:
    """Apply findings to the prior approved requirements.

    Later findings about the same prior ID override earlier ones (meetings are chronological) and
    produce a warning. Prior requirements that no finding mentions are carried forward unchanged.
    """
    by_id = {r.id: r for r in prior}
    log = ChangeLog(from_version=from_version, to_version=to_version)
    decided: dict[str, ReconcileFinding] = {}
    new_reqs: dict[str, tuple[Requirement, bool]] = {}

    for f in findings:
        if f.matches != "new" and f.matches not in by_id:
            log.warnings.append(f"finding referenced unknown id {f.matches!r}; treated as new")
            f = f.model_copy(update={"matches": "new"})
        if f.matches == "new":
            if f.change == "removed":
                log.warnings.append(f"removal of an unknown requirement ignored: {f.title!r}")
                continue
            rid = requirement_id(f.statement)
            if rid in by_id:  # identical text to an existing requirement: nothing changed
                decided.setdefault(
                    rid, f.model_copy(update={"matches": rid, "change": "unchanged"})
                )
                continue
            oos = f.scope_status == "out_of_scope"
            flags = [OUT_OF_SCOPE_FLAG] if oos else []
            if rid in new_reqs:
                existing, _ = new_reqs[rid]
                existing.evidence.extend(f.evidence)
            else:
                new_reqs[rid] = (_from_finding(f, rid, flags), oos)
            continue
        if f.change == "added":
            log.warnings.append(
                f"{f.matches} was marked 'added' but matches a prior item; treated as modified"
            )
            f = f.model_copy(update={"change": "modified"})
        if f.matches in decided and decided[f.matches].change != f.change:
            log.warnings.append(
                f"{f.matches}: '{decided[f.matches].change}' overridden by later '{f.change}'"
            )
        decided[f.matches] = f

    next_reqs: list[Requirement] = []
    for req in prior:
        d = decided.get(req.id)
        if d is None or d.change == "unchanged":
            log.unchanged_ids.append(req.id)
            next_reqs.append(req)
        elif d.change == "removed":
            log.removed.append(req)
        else:
            oos = d.scope_status == "out_of_scope"
            if normalize(d.statement) == normalize(req.statement) and not oos:
                log.unchanged_ids.append(req.id)
                next_reqs.append(req)
                continue
            after = _from_finding(d, req.id, [EXPANDED_FLAG] if oos else [])
            after.evidence = [*req.evidence, *d.evidence]
            log.modified.append(
                Modification(
                    id=req.id,
                    before=req,
                    after=after,
                    rationale=d.rationale,
                    scope_status=d.scope_status,
                )
            )
            next_reqs.append(after)

    for req, oos in new_reqs.values():
        (log.out_of_scope if oos else log.added).append(req)
        next_reqs.append(req)
    return next_reqs, log


def affected_requirement_ids(log: ChangeLog) -> set[str]:
    """Requirements whose estimate must be redone after a follow-up meeting."""
    return (
        {r.id for r in log.added} | {r.id for r in log.out_of_scope} | {m.id for m in log.modified}
    )


def split_items(
    prior_items: Sequence[ScopeItem], log: ChangeLog
) -> tuple[list[ScopeItem], set[str]]:
    """Keep prior items untouched by the change; return them plus the requirement IDs to re-scope.

    An item is touched when any of its requirements was modified or removed. Its surviving
    requirements are re-estimated together with the added and out-of-scope ones.
    """
    changed = {m.id for m in log.modified}
    removed = {r.id for r in log.removed}
    kept: list[ScopeItem] = []
    rescope = affected_requirement_ids(log)
    for item in prior_items:
        ids = set(item.requirement_ids)
        if ids & (changed | removed):
            rescope |= ids - removed
        else:
            kept.append(item)
    return kept, rescope


def finalize_changes(
    log: ChangeLog, prior_items: Sequence[ScopeItem], next_items: Sequence[ScopeItem]
) -> ChangeLog:
    """Compute per-requirement hour deltas and change requests from the two estimates.

    A change request is raised for each out-of-scope new request: it is quoted separately and is
    not part of the agreed price. Modified and added requirements within the agreed scope are
    re-estimated in place and show their hour delta. A modification the client framed as an
    expansion is flagged ``expanded_scope`` and listed with its delta for the human to judge.
    """
    before = hours_by_requirement(prior_items)
    after = hours_by_requirement(next_items)
    deltas: dict[str, float] = {}
    for rid in sorted(affected_requirement_ids(log) | {r.id for r in log.removed}):
        deltas[rid] = round(after.get(rid, (0.0, 0.0))[0] - before.get(rid, (0.0, 0.0))[0], 2)
    requests: list[ChangeRequest] = []
    for req in log.out_of_scope:
        h, c = after.get(req.id, (0.0, 0.0))
        requests.append(_cr([req.id], h, c, f"{req.title}: {req.statement}"))
    return log.model_copy(
        update={"delta_hours_by_requirement": deltas, "change_requests": requests}
    )


def base_items(items: Sequence[ScopeItem], requirements: Sequence[Requirement]) -> list[ScopeItem]:
    """Items in the agreed price: everything except items made only of out-of-scope requests."""
    oos = {r.id for r in requirements if OUT_OF_SCOPE_FLAG in r.flags}
    return [i for i in items if not (i.requirement_ids and set(i.requirement_ids) <= oos)]


def _cr(ids: list[str], hours: float, cost: float, rationale: str) -> ChangeRequest:
    return ChangeRequest(
        id=change_request_id(ids),
        requirement_ids=ids,
        delta_hours=round(hours, 2),
        price_usd=round(cost, 2),
        rationale=rationale,
    )
