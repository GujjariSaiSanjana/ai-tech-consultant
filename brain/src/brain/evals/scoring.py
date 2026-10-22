"""Pass/score rules per case. Pure functions so they are unit-tested and auditable."""

from __future__ import annotations

from dataclasses import dataclass

from brain.evals.cases import EvalCase
from brain.llm.text import stem, terms
from brain.models import AskResponse, FeasibilityResponse


@dataclass(frozen=True, slots=True)
class Verdict:
    passed: bool
    score: float


def reference_recall(reference: str, answer: str) -> float:
    wanted = {stem(t) for t in terms(reference)}
    if not wanted:
        return 1.0
    got = {stem(t) for t in terms(answer)}
    return len(wanted & got) / len(wanted)


def judge_feasibility(case: EvalCase, response: FeasibilityResponse) -> Verdict:
    label_ok = case.expected_label is None or response.kind == case.expected_label
    cited = {c.article_id for c in response.citations}
    gold_ok = not case.gold_article_ids or bool(set(case.gold_article_ids) & cited)
    checks = [float(label_ok)] + ([float(gold_ok)] if case.gold_article_ids else [])
    return Verdict(label_ok and gold_ok, round(sum(checks) / len(checks), 4))


def judge(case: EvalCase, response: AskResponse) -> Verdict:
    cited = {c.article_id for c in response.citations}
    removed = set(case.setup.remove_article_ids)
    if removed & cited:
        return Verdict(False, 0.0)  # a removed article leaking into an answer always fails

    if case.expect == "any":
        return Verdict(True, 1.0)
    if case.expect == "no_answer":
        ok = response.status == "no_answer" and response.gap_ticket_id is not None
        return Verdict(ok, 1.0 if ok else 0.0)

    if response.status != "answered":
        return Verdict(False, 0.0)
    checks: list[float] = []
    if case.gold_article_ids:
        gold = set(case.gold_article_ids)
        if "image_only" in case.tags:
            checks.append(float(any(c.image_url and c.article_id in gold for c in response.citations)))
        else:
            checks.append(float(bool(gold & cited)))
    if case.expected_label is not None:
        checks.append(float(response.label == case.expected_label))
    passed = all(c == 1.0 for c in checks) if checks else True
    if case.reference:
        checks.append(reference_recall(case.reference, response.answer))
    return Verdict(passed, round(sum(checks) / len(checks), 4) if checks else 1.0)
