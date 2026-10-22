"""Gold requirement lists and keyword matching against extracted requirements."""

from __future__ import annotations

import json
from collections.abc import Sequence
from pathlib import Path

from pydantic import BaseModel

from scope.domain.models import Kind, Requirement

PROJECT_DIR = Path(__file__).resolve().parents[3]
DATA_DIR = PROJECT_DIR / "data"


class GoldItem(BaseModel):
    statement: str
    keywords: list[list[str]]
    gold_id: str | None = None
    meeting: str | None = None
    kind: Kind | None = None
    trap: str | None = None


class GoldModified(BaseModel):
    gold_id: str
    after: str


class GoldRemoved(BaseModel):
    gold_id: str
    reason: str


class GoldFollowUp(BaseModel):
    meeting: str
    modified: list[GoldModified]
    removed: list[GoldRemoved]
    unchanged_examples: list[str]
    added: list[GoldItem]
    out_of_scope: list[GoldItem]


class GoldMeeting(BaseModel):
    file: str
    date: str
    purpose: str


class Gold(BaseModel):
    client: str
    meetings: dict[str, GoldMeeting]
    requirements: list[GoldItem]
    not_requirements: list[GoldItem]
    expected_question_categories: list[str]
    follow_up: GoldFollowUp | None = None

    def transcript(self, meeting: str) -> str:
        return (DATA_DIR / "transcripts" / self.meetings[meeting].file).read_text("utf-8")

    def by_id(self, gold_id: str) -> GoldItem:
        return next(g for g in self.requirements if g.gold_id == gold_id)


def load_gold(name: str) -> Gold:
    return Gold.model_validate(json.loads((DATA_DIR / "gold" / f"{name}.json").read_text("utf-8")))


def matches(item: GoldItem, req: Requirement) -> bool:
    text = f"{req.title} {req.statement}".lower()
    return all(any(k in text for k in group) for group in item.keywords)


def align(gold: Sequence[GoldItem], reqs: Sequence[Requirement]) -> dict[int, Requirement]:
    """One-to-one greedy alignment: gold index -> matched requirement (first unused match)."""
    used: set[str] = set()
    out: dict[int, Requirement] = {}
    for i, g in enumerate(gold):
        for r in reqs:
            if r.id not in used and matches(g, r):
                out[i] = r
                used.add(r.id)
                break
    return out
