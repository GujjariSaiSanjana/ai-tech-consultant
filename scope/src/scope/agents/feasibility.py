"""Feasibility Checker: for each requirement, existing setting vs add-on vs widget vs custom code.

Every requirement is first sent to the Brain's ``POST /v1/feasibility`` in code, so the check never
depends on the model choosing to call a tool. The model then judges the Brain answers (and may
search the Brain again for vague requirements). Code enforces the guardrails:
- Brain unreachable for a requirement -> kind "unknown", flagged; never guessed.
- A claim that something is a setting/add-on/widget must cite an article the Brain returned in this
  run, otherwise it is downgraded to "unknown" and flagged.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Sequence

from pydantic import BaseModel, ValidationError

from scope.agents.base import dumps, request
from scope.brain.client import BrainClient, BrainUnavailableError, FeasibilityAnswer, SearchAnswer
from scope.config import AgentName
from scope.domain.models import Citation, Feasibility, Kind, Requirement
from scope.llm.session import LLMSession
from scope.llm.types import ToolCall

AGENT: AgentName = "feasibility"
BRAIN_DOWN = "brain_unavailable"
UNCITED = "uncited_claim_downgraded"
NO_DECISION = "no_model_decision_used_brain_answer"


class FeasibilityDecision(BaseModel):
    requirement_id: str
    kind: Kind
    setting_path: str | None
    rationale: str
    confidence: float
    citation_article_ids: list[str]
    needs_clarification: bool


class FeasibilityOutput(BaseModel):
    decisions: list[FeasibilityDecision]


async def ask_brain(
    brain: BrainClient, reqs: Sequence[Requirement], concurrency: int
) -> dict[str, FeasibilityAnswer | None]:
    sem = asyncio.Semaphore(concurrency)

    async def one(r: Requirement) -> tuple[str, FeasibilityAnswer | None]:
        async with sem:
            try:
                return r.id, await brain.feasibility(f"{r.title}: {r.statement}")
            except BrainUnavailableError:
                return r.id, None

    return dict(await asyncio.gather(*(one(r) for r in reqs)))


def citations_from_tools(calls: Sequence[ToolCall]) -> dict[str, Citation]:
    found: dict[str, Citation] = {}
    for c in calls:
        if c.is_error:
            continue
        try:
            if c.name == "brain_feasibility":
                for cit in FeasibilityAnswer.model_validate_json(c.output).citations:
                    found[cit.article_id] = cit
            elif c.name == "brain_search":
                for hit in SearchAnswer.model_validate_json(c.output).results:
                    found.setdefault(
                        hit.article_id,
                        Citation(
                            article_id=hit.article_id, title=hit.title, url=hit.url, quote=hit.text
                        ),
                    )
        except (ValidationError, json.JSONDecodeError):
            continue
    return found


def unknown(rid: str, rationale: str, flag: str) -> Feasibility:
    return Feasibility(
        requirement_id=rid,
        kind="unknown",
        setting_path=None,
        rationale=rationale,
        confidence=0.0,
        citations=[],
        flags=[flag],
    )


def from_brain(rid: str, ans: FeasibilityAnswer, flags: list[str]) -> Feasibility:
    return Feasibility(
        requirement_id=rid,
        kind=ans.kind,
        setting_path=ans.setting_path,
        rationale=ans.rationale,
        confidence=ans.confidence,
        citations=ans.citations,
        flags=flags,
    )


def merge_decision(
    rid: str, ans: FeasibilityAnswer, d: FeasibilityDecision | None, known: dict[str, Citation]
) -> Feasibility:
    if d is None:
        return from_brain(rid, ans, [NO_DECISION])
    flags: list[str] = ["needs_clarification"] if d.needs_clarification else []
    cites = [known[a] for a in d.citation_article_ids if a in known]
    if d.kind in ("setting", "addon", "widget_customization") and not cites:
        return Feasibility(
            requirement_id=rid,
            kind="unknown",
            setting_path=None,
            rationale=f"Model said {d.kind} without a Brain citation: {d.rationale}",
            confidence=0.0,
            citations=[],
            flags=[*flags, UNCITED],
        )
    path = d.setting_path if d.kind != "custom_dev" else None
    return Feasibility(
        requirement_id=rid,
        kind=d.kind,
        setting_path=path,
        rationale=d.rationale,
        confidence=max(0.0, min(1.0, d.confidence)),
        citations=cites,
        flags=flags,
    )


async def check(
    session: LLMSession, brain: BrainClient, reqs: Sequence[Requirement], context: str
) -> list[Feasibility]:
    if not reqs:
        return []
    answers = await ask_brain(brain, reqs, session.settings.brain_concurrency)
    down = {rid for rid, ans in answers.items() if ans is None}
    results: dict[str, Feasibility] = {
        rid: unknown(rid, "Knowledge Brain unavailable; not guessed.", BRAIN_DOWN) for rid in down
    }
    answered = [(r, a) for r in reqs if (a := answers[r.id]) is not None]
    if answered:
        payload = dumps(
            {
                "task": "Decide how each requirement is delivered in the product.",
                "requirements": [
                    {
                        "requirement_id": r.id,
                        "title": r.title,
                        "statement": r.statement,
                        "brain_answer": a.model_dump(mode="json"),
                    }
                    for r, a in answered
                ],
            }
        )
        result = await session.parse(
            request(AGENT, session.settings, context, payload), FeasibilityOutput
        )
        known = {c.article_id: c for _, a in answered for c in a.citations}
        known.update(citations_from_tools(result.tool_calls))
        decisions = {d.requirement_id: d for d in result.output.decisions}
        for r, a in answered:
            results[r.id] = merge_decision(r.id, a, decisions.get(r.id), known)
    return [results[r.id] for r in reqs]
