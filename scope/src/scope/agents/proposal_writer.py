"""Proposal Writer: the approved scope -> client-ready proposal sections in Markdown.

Numbers (price, hours, change-request quotes) are rendered by code from the estimate, never
written by the model, so the client never sees an arithmetic slip.
"""

from __future__ import annotations

from pydantic import BaseModel

from scope.agents.base import dumps, request, requirement_brief
from scope.config import AgentName
from scope.domain.models import ProposalSectionName, ScopeSnapshot
from scope.llm.session import LLMSession

AGENT: AgentName = "proposal_writer"
LLM_SECTIONS: tuple[ProposalSectionName, ...] = (
    "summary",
    "scope",
    "exclusions",
    "timeline",
    "assumptions",
    "open_questions",
    "change_process",
)


class ProposalDraft(BaseModel):
    summary: str
    scope: str
    exclusions: str
    timeline: str
    assumptions: str
    open_questions: str
    change_process: str


class SectionDraft(BaseModel):
    content: str


def _facts(snapshot: ScopeSnapshot, client: str) -> dict[str, object]:
    est = snapshot.estimate
    return {
        "client": client,
        "requirements": requirement_brief(snapshot.requirements),
        "scope_items": [
            {
                "id": i.id,
                "title": i.title,
                "description": i.description,
                "kind": i.kind,
                "expected_hours": i.expected_hours,
                "out_of_scope_change": any(
                    "out_of_scope" in r.flags
                    for r in snapshot.requirements
                    if r.id in i.requirement_ids
                ),
            }
            for i in snapshot.items
        ],
        "exclusions": snapshot.exclusions,
        "assumptions": [a.statement for a in snapshot.assumptions],
        "open_questions": [q.question for q in snapshot.questions],
        "total_expected_hours": est.expected_hours if est else None,
        "hours_range": [est.low_hours, est.high_hours] if est else None,
    }


async def write(
    session: LLMSession, snapshot: ScopeSnapshot, *, client: str, context: str
) -> dict[ProposalSectionName, str]:
    payload = dumps({"task": "Write the proposal sections.", "facts": _facts(snapshot, client)})
    out = (
        await session.parse(request(AGENT, session.settings, context, payload), ProposalDraft)
    ).output
    return {name: getattr(out, name) for name in LLM_SECTIONS}


async def rewrite_section(
    session: LLMSession,
    snapshot: ScopeSnapshot,
    *,
    client: str,
    context: str,
    section: ProposalSectionName,
    current: str,
    instruction: str | None,
) -> str:
    payload = dumps(
        {
            "task": f"Rewrite only the '{section}' section.",
            "current_text": current,
            "reviewer_instruction": instruction or "Improve clarity and accuracy.",
            "facts": _facts(snapshot, client),
        }
    )
    return (
        await session.parse(request(AGENT, session.settings, context, payload), SectionDraft)
    ).output.content
