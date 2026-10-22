"""Requirements Extractor: transcript(s) -> Requirement[] with verbatim, verified evidence."""

from __future__ import annotations

import asyncio
from collections.abc import Sequence

from pydantic import BaseModel

from scope.agents.base import dumps, grounded, request
from scope.config import AgentName
from scope.domain.ids import requirement_id
from scope.domain.models import (
    DiscardedMention,
    Evidence,
    Priority,
    Requirement,
    RequirementCategory,
)
from scope.domain.transcript import Transcript, estimate_tokens, render, segment
from scope.llm.session import LLMSession

AGENT: AgentName = "extractor"


class ExtractedRequirement(BaseModel):
    title: str
    statement: str
    category: RequirementCategory
    priority: Priority
    evidence: list[Evidence]


class ExtractionOutput(BaseModel):
    requirements: list[ExtractedRequirement]
    discarded: list[DiscardedMention]


async def _extract_once(session: LLMSession, transcripts: Sequence[Transcript]) -> ExtractionOutput:
    payload = dumps(
        {
            "task": "Extract every requirement from the meetings above.",
            "meeting_ids": [t.meeting_id for t in transcripts],
        }
    )
    req = request(AGENT, session.settings, render(transcripts), payload)
    return (await session.parse(req, ExtractionOutput)).output


async def extract(
    session: LLMSession, transcripts: Sequence[Transcript]
) -> tuple[list[Requirement], list[DiscardedMention]]:
    """Extract, verify quotes, drop ungrounded items, assign stable IDs, merge duplicates.

    Transcripts above ``long_transcript_tokens`` are split into segments that are each sent whole
    (segment-then-merge); nothing is extracted from summaries.
    """
    settings = session.settings
    if estimate_tokens(render(transcripts)) > settings.long_transcript_tokens:
        parts = segment(transcripts, settings.segment_tokens)
        outputs = await asyncio.gather(*(_extract_once(session, p) for p in parts))
    else:
        outputs = [await _extract_once(session, transcripts)]

    merged: dict[str, Requirement] = {}
    discarded: list[DiscardedMention] = []
    for out in outputs:
        discarded.extend(out.discarded)
        for item in out.requirements:
            evidence, reason = grounded(item.evidence, transcripts)
            if reason:
                discarded.append(DiscardedMention(quote=item.statement, reason=reason))
                continue
            rid = requirement_id(item.statement)
            if rid in merged:
                merged[rid].evidence.extend(e for e in evidence if e not in merged[rid].evidence)
                continue
            merged[rid] = Requirement(
                id=rid,
                title=item.title,
                statement=item.statement,
                category=item.category,
                priority=item.priority,
                evidence=evidence,
            )
    return list(merged.values()), discarded
