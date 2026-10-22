"""Reconciler (follow-up meetings only): relate a new meeting to the prior approved requirements.

Each finding declares ``matches: REQ-...`` or ``new``; code turns findings into the next
requirement list and a ChangeLog (``scope.domain.diff``), so IDs stay stable across versions.
"""

from __future__ import annotations

from collections.abc import Sequence

from pydantic import BaseModel

from scope.agents.base import dumps, grounded, request, requirement_brief
from scope.config import AgentName
from scope.domain.models import DiscardedMention, ReconcileFinding, Requirement
from scope.domain.transcript import Transcript, render
from scope.llm.session import LLMSession

AGENT: AgentName = "reconciler"


class ReconcileOutput(BaseModel):
    findings: list[ReconcileFinding]


async def reconcile(
    session: LLMSession,
    prior: Sequence[Requirement],
    transcripts: Sequence[Transcript],
    follow_up_ids: Sequence[str],
) -> tuple[list[ReconcileFinding], list[DiscardedMention]]:
    """``transcripts`` holds every meeting so far (same cached prefix as the other agents);
    findings must be evidenced in the ``follow_up_ids`` meetings."""
    payload = dumps(
        {
            "task": "Relate the follow-up meeting to the approved requirements.",
            "follow_up_meeting_ids": list(follow_up_ids),
            "approved_requirements": requirement_brief(prior),
        }
    )
    follow_up = [t for t in transcripts if t.meeting_id in set(follow_up_ids)]
    out = (
        await session.parse(
            request(AGENT, session.settings, render(transcripts), payload), ReconcileOutput
        )
    ).output
    kept: list[ReconcileFinding] = []
    discarded: list[DiscardedMention] = []
    for f in out.findings:
        if f.change == "unchanged" and not f.evidence:
            kept.append(f)
            continue
        evidence, reason = grounded(f.evidence, follow_up)
        if reason:
            discarded.append(DiscardedMention(quote=f.statement, reason=reason))
            continue
        kept.append(f.model_copy(update={"evidence": evidence}))
    return kept, discarded
