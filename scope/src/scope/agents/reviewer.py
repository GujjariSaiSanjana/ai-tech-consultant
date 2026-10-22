"""Reviewer (evaluator in an evaluator-optimizer loop): finds missed requirements, inconsistent
hours and missing assumptions. Deterministic checks run first and are passed to the model, so it
spends its attention on judgement rather than arithmetic."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Literal

from pydantic import BaseModel

from scope.agents.base import dumps, request, requirement_brief
from scope.config import AgentName
from scope.domain.models import Assumption, Feasibility, Requirement, ReviewNote, ScopeItem
from scope.llm.session import LLMSession

AGENT: AgentName = "reviewer"


class ReviewNoteDraft(BaseModel):
    severity: Literal["must_fix", "should_fix", "info"]
    kind: Literal["missed_requirement", "inconsistent_hours", "missing_assumption", "other"]
    target: str | None
    message: str


class ReviewOutput(BaseModel):
    verdict: Literal["approve", "revise"]
    notes: list[ReviewNoteDraft]


async def review(
    session: LLMSession,
    *,
    context: str,
    requirements: Sequence[Requirement],
    feasibility: Sequence[Feasibility],
    items: Sequence[ScopeItem],
    assumptions: Sequence[Assumption],
    code_notes: Sequence[ReviewNote],
) -> tuple[bool, list[ReviewNote]]:
    payload = dumps(
        {
            "task": "Review this scope and estimate before it goes to a human for approval.",
            "requirements": requirement_brief(requirements),
            "feasibility": [
                {"requirement_id": f.requirement_id, "kind": f.kind, "setting_path": f.setting_path}
                for f in feasibility
            ],
            "scope_items": [
                i.model_dump(mode="json") | {"expected_hours": i.expected_hours} for i in items
            ],
            "assumptions": [a.statement for a in assumptions],
            "automated_check_findings": [n.model_dump(mode="json") for n in code_notes],
        }
    )
    out = (
        await session.parse(request(AGENT, session.settings, context, payload), ReviewOutput)
    ).output
    notes = [
        ReviewNote(
            severity=n.severity, kind=n.kind, target=n.target, message=n.message, source="llm"
        )
        for n in out.notes
    ]
    return out.verdict == "approve", notes
