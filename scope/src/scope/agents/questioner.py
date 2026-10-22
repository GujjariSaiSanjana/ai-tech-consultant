"""Senior Consultant Questioner: the questions a senior consultant would still ask, plus the
assumptions the team will make if they stay unanswered. Seeded with a discovery checklist."""

from __future__ import annotations

import asyncio
from collections.abc import Sequence

from pydantic import BaseModel

from scope.agents.base import dumps, request
from scope.config import AgentName
from scope.domain.ids import assumption_id, question_id
from scope.domain.models import CHECKLIST, Assumption, ChecklistCategory, OpenQuestion
from scope.domain.transcript import Transcript, estimate_tokens, render, segment
from scope.llm.session import LLMSession
from scope.prompts import load_prompt

AGENT: AgentName = "questioner"


class QuestionDraft(BaseModel):
    category: ChecklistCategory
    question: str
    why_it_matters: str
    blocking: bool


class AssumptionDraft(BaseModel):
    category: ChecklistCategory
    statement: str
    risk_if_wrong: str


class QuestionsOutput(BaseModel):
    covered_categories: list[ChecklistCategory]
    questions: list[QuestionDraft]
    assumptions: list[AssumptionDraft]


async def _ask_once(session: LLMSession, transcripts: Sequence[Transcript]) -> QuestionsOutput:
    payload = dumps(
        {
            "task": "Review the meetings against the discovery checklist.",
            "checklist": load_prompt("discovery_checklist.v1"),
            "categories": list(CHECKLIST),
        }
    )
    req = request(AGENT, session.settings, render(transcripts), payload)
    return (await session.parse(req, QuestionsOutput)).output


async def ask(
    session: LLMSession, transcripts: Sequence[Transcript]
) -> tuple[list[OpenQuestion], list[Assumption]]:
    settings = session.settings
    if estimate_tokens(render(transcripts)) > settings.long_transcript_tokens:
        parts = segment(transcripts, settings.segment_tokens)
        outputs = await asyncio.gather(*(_ask_once(session, p) for p in parts))
    else:
        outputs = [await _ask_once(session, transcripts)]
    questions: dict[str, OpenQuestion] = {}
    assumptions: dict[str, Assumption] = {}
    for out in outputs:
        for q in out.questions:
            qid = question_id(q.category, q.question)
            questions.setdefault(
                qid,
                OpenQuestion(
                    id=qid,
                    category=q.category,
                    question=q.question,
                    why_it_matters=q.why_it_matters,
                    blocking=q.blocking,
                ),
            )
        for a in out.assumptions:
            aid = assumption_id(a.statement)
            assumptions.setdefault(
                aid,
                Assumption(
                    id=aid,
                    category=a.category,
                    statement=a.statement,
                    risk_if_wrong=a.risk_if_wrong,
                ),
            )
    return list(questions.values()), list(assumptions.values())
