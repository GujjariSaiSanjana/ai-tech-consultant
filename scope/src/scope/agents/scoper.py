"""Scoper + Estimator: requirements and feasibility -> scope items with three-point task hours.

The model proposes items, tasks and O/M/P hours. Code assigns IDs, repairs and flags bad triplets,
computes PERT expected hours and sigma, and prices tasks from the rate card.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from pydantic import BaseModel

from scope.agents.base import dumps, request, requirement_brief
from scope.agents.questioner import AssumptionDraft
from scope.config import AgentName
from scope.domain.ids import assumption_id, scope_item_id, task_id
from scope.domain.models import (
    Assumption,
    Feasibility,
    Kind,
    OpenQuestion,
    Requirement,
    ReviewNote,
    Role,
    ScopeItem,
)
from scope.domain.pert import build_task
from scope.llm.session import LLMSession

AGENT: AgentName = "scoper"


class TaskDraft(BaseModel):
    title: str
    role: Role
    optimistic_hours: float
    likely_hours: float
    pessimistic_hours: float
    rationale: str


class ScopeItemDraft(BaseModel):
    title: str
    description: str
    requirement_ids: list[str]
    kind: Kind
    tasks: list[TaskDraft]


class ScopeDraft(BaseModel):
    items: list[ScopeItemDraft]
    exclusions: list[str]
    assumptions: list[AssumptionDraft]


def build_items(
    drafts: Sequence[ScopeItemDraft], rate_card: Mapping[Role, float]
) -> list[ScopeItem]:
    items: list[ScopeItem] = []
    for d in drafts:
        iid = scope_item_id(d.title, d.requirement_ids)
        tasks = [
            build_task(
                task_id=task_id(iid, t.title, t.role),
                title=t.title,
                role=t.role,
                o=t.optimistic_hours,
                m=t.likely_hours,
                p=t.pessimistic_hours,
                rate_usd=rate_card[t.role],
            )
            for t in d.tasks
        ]
        items.append(
            ScopeItem(
                id=iid,
                title=d.title,
                description=d.description,
                requirement_ids=list(dict.fromkeys(d.requirement_ids)),
                kind=d.kind,
                tasks=tasks,
            )
        )
    return items


async def estimate(
    session: LLMSession,
    *,
    context: str,
    requirements: Sequence[Requirement],
    feasibility: Sequence[Feasibility],
    questions: Sequence[OpenQuestion],
    assumptions: Sequence[Assumption],
    review_notes: Sequence[ReviewNote] = (),
    kept_items: Sequence[ScopeItem] = (),
) -> tuple[list[ScopeItem], list[str], list[Assumption]]:
    """Estimate ``requirements``. ``kept_items`` are already-agreed items (follow-up meetings) shown
    for consistency; they are not re-estimated and must not be repeated."""
    s = session.settings
    payload = dumps(
        {
            "task": "Group the requirements into scope items and estimate their tasks.",
            "requirements": requirement_brief(requirements),
            "feasibility": [f.model_dump(mode="json", exclude={"citations"}) for f in feasibility],
            "open_questions": [q.question for q in questions],
            "assumptions": [a.statement for a in assumptions],
            "roles": sorted(s.rate_card),
            "setting_max_hours": s.setting_max_hours,
            "reviewer_notes": [n.model_dump(mode="json") for n in review_notes],
            "already_agreed_items": [
                {"title": i.title, "kind": i.kind, "expected_hours": i.expected_hours}
                for i in kept_items
            ],
        }
    )
    out = (await session.parse(request(AGENT, s, context, payload), ScopeDraft)).output
    extra = [
        Assumption(
            id=assumption_id(a.statement),
            category=a.category,
            statement=a.statement,
            risk_if_wrong=a.risk_if_wrong,
        )
        for a in out.assumptions
    ]
    return build_items(out.items, s.rate_card), out.exclusions, extra
