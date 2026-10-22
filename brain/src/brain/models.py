"""Request and response bodies of the public API, exactly as in docs/contracts/brain-api.md."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

from brain.domain import ChunkKind, Label

GapSource = Literal["chat", "inbox", "scope"]


class Citation(BaseModel):
    article_id: str = Field(description="Freshdesk article id, or `expert:<gap id>` for a curated expert answer")
    title: str
    url: str
    quote: str = Field(description="Exact cited passage")
    image_url: str | None = Field(None, description="Set when the cited chunk describes a screenshot")


class UsageOut(BaseModel):
    input_tokens: int
    output_tokens: int
    cost_usd: float
    latency_ms: int


class HealthResponse(BaseModel):
    status: Literal["ok"]
    articles: int
    chunks: int
    last_sync: datetime | None


class AskRequest(BaseModel):
    question: str = Field(min_length=1, max_length=4000)
    top_k: int = Field(8, ge=1, le=30)
    category: str | None = None


class AskResponse(BaseModel):
    status: Literal["answered", "no_answer"]
    answer: str
    label: Label
    setting_path: str | None
    warnings: list[str]
    citations: list[Citation]
    confidence: float = Field(ge=0, le=1)
    gap_ticket_id: str | None
    model: str
    usage: UsageOut


class SearchRequest(BaseModel):
    query: str = Field(min_length=1, max_length=4000)
    top_k: int = Field(8, ge=1, le=50)
    category: str | None = None


class SearchResult(BaseModel):
    chunk_id: str
    article_id: str
    title: str
    url: str
    category: str
    folder: str
    kind: ChunkKind
    text: str
    score: float = Field(description="Reciprocal-rank-fusion score after boosts; compare within one response")


class SearchResponse(BaseModel):
    results: list[SearchResult]


class FeasibilityRequest(BaseModel):
    requirement: str = Field(min_length=1, max_length=4000)


class FeasibilityResponse(BaseModel):
    kind: Label
    setting_path: str | None
    rationale: str
    confidence: float = Field(ge=0, le=1)
    citations: list[Citation]


class ArticleImage(BaseModel):
    url: str
    description: str


class ArticleResponse(BaseModel):
    article_id: str
    title: str
    url: str
    category: str
    folder: str
    body_text: str
    images: list[ArticleImage]
    lastmod: datetime | None
    status: Literal["active", "removed"]


class GapCreateRequest(BaseModel):
    question: str = Field(min_length=1, max_length=4000)
    context: str | None = None
    source: GapSource


class GapCreated(BaseModel):
    id: str
    status: Literal["open"]


class GapItem(BaseModel):
    id: str
    question: str
    context: str | None
    source: GapSource
    status: Literal["open", "answered"]
    bot_attempt: str | None
    answer: str | None
    created_at: datetime


class GapList(BaseModel):
    items: list[GapItem]


class GapAnswerRequest(BaseModel):
    answer: str = Field(min_length=1, max_length=20000)
    expert: str = Field(min_length=1, max_length=200)


class GapAnswered(BaseModel):
    id: str
    status: Literal["answered"]
    chunk_id: str


class SyncStarted(BaseModel):
    run_id: str


class SyncRun(BaseModel):
    run_id: str
    status: Literal["running", "done", "failed"]
    added: int
    updated: int
    removed: int
    unchanged: int
    errors: int
    started_at: datetime
    finished_at: datetime | None


class ErrorBody(BaseModel):
    code: str
    message: str


class ErrorResponse(BaseModel):
    error: ErrorBody
