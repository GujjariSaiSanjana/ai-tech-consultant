"""Scripted responders for pipeline tests: start from the heuristic fakes, override some agents."""

from __future__ import annotations

import json
from collections.abc import Callable

from pydantic import BaseModel

from scope.agents.reconciler import ReconcileOutput
from scope.agents.reviewer import ReviewNoteDraft, ReviewOutput
from scope.domain.models import Evidence, ReconcileFinding
from scope.domain.transcript import Transcript
from scope.fakes.heuristics import DEFAULT_RESPONDERS
from scope.fakes.llm import FakeLLM, Responder
from scope.llm.types import LLMRequest


def fake_with(**overrides: Responder) -> FakeLLM:
    return FakeLLM({**DEFAULT_RESPONDERS, **overrides})


def reviewer_sequence(verdicts: list[str]) -> Responder:
    """Reviewer that returns the given verdicts in order (then keeps the last)."""
    calls = {"n": 0}

    def respond(req: LLMRequest, schema: type[BaseModel]) -> BaseModel:
        v = verdicts[min(calls["n"], len(verdicts) - 1)]
        calls["n"] += 1
        notes = (
            []
            if v == "approve"
            else [
                ReviewNoteDraft(
                    severity="must_fix",
                    kind="inconsistent_hours",
                    target=None,
                    message="QA effort is missing on custom items",
                )
            ]
        )
        return ReviewOutput(verdict="approve" if v == "approve" else "revise", notes=notes)

    return respond


def utterance(t: Transcript, needle: str) -> Evidence:
    u = next(u for u in t.utterances if needle.lower() in u.text.lower())
    return Evidence(
        meeting_id=t.meeting_id, timestamp=u.timestamp, speaker=u.speaker, quote=u.text[:120]
    )


def gold_like_reconciler(m3: Transcript) -> Callable[[LLMRequest, type[BaseModel]], BaseModel]:
    """Modifies the first approved requirement, removes the second, adds one within scope and one
    out of scope, all with real quotes from meeting 3."""

    def respond(req: LLMRequest, schema: type[BaseModel]) -> BaseModel:
        prior = json.loads(req.payload)["approved_requirements"]
        first, second = prior[0], prior[1]

        def f(
            matches: str, change: str, statement: str, needle: str, status: str = "within_agreed"
        ) -> ReconcileFinding:
            return ReconcileFinding.model_validate(
                {
                    "matches": matches,
                    "change": change,
                    "title": statement[:30],
                    "statement": statement,
                    "category": "functional",
                    "priority": "must",
                    "scope_status": status,
                    "evidence": [utterance(m3, needle).model_dump()],
                    "rationale": "said in meeting 3",
                }
            )

        return ReconcileOutput(
            findings=[
                f(first["id"], "modified", "Only FareHarbor, with a Book now deep link.", "Peek"),
                f(second["id"], "removed", second["statement"], "HubSpot dashboards"),
                f("new", "added", "Admins export members with plan and renewal date.", "export"),
                f(
                    "new",
                    "added",
                    "Guides get an SMS alert via Twilio for new leads.",
                    "Twilio",
                    "out_of_scope",
                ),
            ]
        )

    return respond
