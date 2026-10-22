"""Helpers shared by agents: request construction and deterministic payload serialization."""

from __future__ import annotations

import json
from collections.abc import Sequence
from typing import Any

from pydantic import BaseModel

from scope.config import AgentName, Settings
from scope.domain.models import Evidence, Requirement
from scope.domain.transcript import Transcript, evidence_is_suspicious, verify_evidence
from scope.llm.types import LLMRequest
from scope.prompts import load_prompt

PROMPT_VERSION = "v1"


def dumps(obj: Any) -> str:
    """Stable JSON for prompts (sorted keys) so identical inputs give identical bytes."""
    if isinstance(obj, BaseModel):
        obj = obj.model_dump(mode="json")
    elif isinstance(obj, list):
        obj = [o.model_dump(mode="json") if isinstance(o, BaseModel) else o for o in obj]
    return json.dumps(obj, sort_keys=True, ensure_ascii=False, indent=1)


def request(agent: AgentName, settings: Settings, context: str, payload: str) -> LLMRequest:
    return LLMRequest(
        agent=agent,
        route=settings.routes[agent],
        context=context,
        instructions=load_prompt(f"{agent}.{PROMPT_VERSION}"),
        payload=payload,
    )


def requirement_brief(reqs: Sequence[Requirement]) -> list[dict[str, Any]]:
    """Compact requirement view for downstream prompts (quotes kept, they ground the estimate)."""
    return [
        {
            "id": r.id,
            "title": r.title,
            "statement": r.statement,
            "category": r.category,
            "priority": r.priority,
            "flags": r.flags,
            "evidence": [
                f"[{e.meeting_id} {e.timestamp}] {e.speaker}: {e.quote}" for e in r.evidence
            ],
        }
        for r in reqs
    ]


def grounded(
    evidence: Sequence[Evidence], transcripts: Sequence[Transcript]
) -> tuple[list[Evidence], str | None]:
    """Keep only verifiable evidence. Returns (evidence, reason to discard the item or None)."""
    ok, _bad = verify_evidence(evidence, transcripts)
    if not ok:
        return ok, "no quote could be found in the transcript"
    if evidence_is_suspicious(ok, transcripts):
        return ok, "only supported by instruction-like text (possible prompt injection)"
    return ok, None
