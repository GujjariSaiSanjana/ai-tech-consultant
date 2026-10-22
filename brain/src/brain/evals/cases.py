from __future__ import annotations

from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field

from brain.config import PROJECT_DIR
from brain.domain import Label

CASES_PATH = PROJECT_DIR / "evals" / "cases.jsonl"


class Setup(BaseModel):
    remove_article_ids: list[str] = Field(default_factory=list)


class EvalCase(BaseModel):
    id: str
    split: Literal["dev", "heldout"]
    tags: list[str]
    question: str
    expect: Literal["answer", "no_answer"]
    gold_article_ids: list[str] = Field(default_factory=list)
    reference: str = ""
    expected_label: Label | None = None
    endpoint: Literal["ask", "feasibility"] = "ask"
    setup: Setup = Field(default_factory=Setup)


def load_cases(path: Path = CASES_PATH) -> list[EvalCase]:
    lines = [line for line in path.read_text().splitlines() if line.strip()]
    cases = [EvalCase.model_validate_json(line) for line in lines]
    ids = [c.id for c in cases]
    if len(ids) != len(set(ids)):
        raise ValueError("duplicate case ids")
    return cases
