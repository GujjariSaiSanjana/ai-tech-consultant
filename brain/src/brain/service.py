"""Use cases behind the API, MCP tools, UI and evals. Collaborators are injected."""

from __future__ import annotations

import logging
import time
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime

from brain.crawl.parser import setting_path_from_text
from brain.domain import ChunkDraft, SearchHit
from brain.ingest.chunker import chunk_id
from brain.ingest.embedder import Embedder
from brain.ingest.indexer import embedding_input
from brain.llm.answerer import AnswerDraft, Answerer
from brain.llm.classifier import Classification, Classifier
from brain.llm.client import LLMError, LLMRefusalError, Usage
from brain.llm.text import or_query
from brain.models import (
    ArticleImage,
    ArticleResponse,
    AskResponse,
    Citation,
    FeasibilityResponse,
    GapAnswered,
    GapItem,
    SearchResult,
    UsageOut,
)
from brain.repo import NewChunk, Repository

log = logging.getLogger(__name__)


class NotFoundError(Exception):
    pass


class ConflictError(Exception):
    pass


@dataclass(frozen=True, slots=True)
class RetrievalPolicy:
    min_similarity: float
    changelog_boost: float
    expert_boost: float
    public_url: str


class BrainService:
    def __init__(
        self,
        repo: Repository,
        embedder: Embedder,
        answerer: Answerer,
        classifier: Classifier,
        policy: RetrievalPolicy,
    ) -> None:
        self._repo = repo
        self._embedder = embedder
        self._answerer = answerer
        self._classifier = classifier
        self._policy = policy

    @property
    def answer_model(self) -> str:
        return self._answerer.model

    async def search(self, query: str, top_k: int = 8, category: str | None = None) -> list[SearchHit]:
        [vector] = await self._embedder.embed([query], "query")
        return await self._repo.hybrid_search(
            query_text=or_query(query),
            query_vector=vector,
            dim=self._embedder.dim,
            top_k=top_k,
            category=category,
            changelog_boost=self._policy.changelog_boost,
            expert_boost=self._policy.expert_boost,
            public_url=self._policy.public_url,
        )

    async def search_results(self, query: str, top_k: int, category: str | None) -> list[SearchResult]:
        return [
            SearchResult(
                chunk_id=h.chunk_id,
                article_id=h.article_id,
                title=h.title,
                url=h.url,
                category=h.category,
                folder=h.folder,
                kind=h.kind,
                text=h.text,
                score=round(h.score, 6),
            )
            for h in await self.search(query, top_k, category)
        ]

    async def ask(
        self, question: str, top_k: int = 8, category: str | None = None, *, source: str = "chat"
    ) -> AskResponse:
        started = time.perf_counter()
        hits = await self.search(question, top_k, category)
        strength = self._strength(hits)
        usage = Usage()
        draft: AnswerDraft | None = None
        if strength > 0:
            try:
                draft = await self._answerer.answer(question, hits)
                usage.add(draft.usage)
            except LLMRefusalError as exc:
                log.warning("answer refused: %s", exc)
        if draft is None or draft.status == "no_answer" or not draft.citations:
            gap_id = await self._open_gap(question, None, source, bot_attempt=_abstain_note(hits, strength))
            return AskResponse(
                status="no_answer",
                answer="",
                label="unknown",
                setting_path=None,
                warnings=[],
                citations=[],
                confidence=round(0.3 * strength, 3),
                gap_ticket_id=gap_id,
                model=draft.model if draft else self._answerer.model,
                usage=_usage_out(usage, started),
            )

        cited = _cited_hits(draft, hits)
        classification, class_usage = await self._classify(question, draft.text, cited or hits)
        usage.add(class_usage)
        citations = _citations(draft, hits)
        if classification.label == "setting" and not classification.setting_path:
            path = next((p for c in citations if (p := setting_path_from_text(c.quote))), None)
            classification = classification.model_copy(update={"setting_path": path})
        grounded = min(1.0, len({c.article_id for c in citations}) / 2 + 0.5)
        return AskResponse(
            status="answered",
            answer=draft.text,
            label=classification.label,
            setting_path=classification.setting_path,
            warnings=classification.warnings,
            citations=citations,
            confidence=round(min(1.0, 0.25 + 0.45 * strength + 0.3 * grounded), 3),
            gap_ticket_id=None,
            model=draft.model,
            usage=_usage_out(usage, started),
        )

    async def feasibility(self, requirement: str) -> FeasibilityResponse:
        response, _ = await self.feasibility_with_usage(requirement)
        return response

    async def feasibility_with_usage(self, requirement: str) -> tuple[FeasibilityResponse, UsageOut]:
        """The contract's feasibility body has no usage field; evals still need the true cost."""
        started = time.perf_counter()
        usage = Usage()
        hits = await self.search(requirement, 8, None)
        draft: AnswerDraft | None = None
        if self._strength(hits) > 0:
            try:
                draft = await self._answerer.answer(requirement, hits)
                usage.add(draft.usage)
            except LLMRefusalError as exc:
                log.warning("feasibility answer refused: %s", exc)
        answered = draft is not None and draft.status == "answered" and bool(draft.citations)
        basis = _cited_hits(draft, hits) if draft and answered else hits
        classification, class_usage = await self._classify(
            requirement, draft.text if draft and answered else None, basis
        )
        usage.add(class_usage)
        response = FeasibilityResponse(
            kind=classification.label,
            setting_path=classification.setting_path,
            rationale=classification.rationale,
            confidence=round(classification.confidence, 3),
            citations=_citations(draft, hits) if draft and answered else [],
        )
        return response, _usage_out(usage, started)

    async def article(self, article_id: str) -> ArticleResponse:
        row = await self._repo.get_article(article_id)
        if row is None:
            raise NotFoundError(f"article {article_id} not found")
        return ArticleResponse(
            article_id=row["id"],
            title=row["title"],
            url=row["url"],
            category=row["category"],
            folder=row["folder"],
            body_text=row["body_text"],
            images=[ArticleImage(url=i["url"], description=i["description"]) for i in row["images"]],
            lastmod=row["sitemap_lastmod"],
            status=row["status"],
        )

    async def create_gap(self, question: str, context: str | None, source: str) -> str:
        return await self._open_gap(question, context, source, bot_attempt=None)

    async def list_gaps(self, status: str | None) -> list[GapItem]:
        return [GapItem.model_validate(row) for row in await self._repo.list_gaps(status)]

    async def answer_gap(self, gap_id: str, answer: str, expert: str) -> GapAnswered:
        gap = await self._repo.get_gap(gap_id)
        if gap is None:
            raise NotFoundError(f"gap {gap_id} not found")
        draft = ChunkDraft(
            kind="expert",
            position=0,
            heading=gap["question"],
            text=f"Question: {gap['question']}\nAnswer from expert {expert}: {answer}",
            context_prefix="Expert answers",
        )
        cid = chunk_id(f"gap:{gap_id}", draft, set())
        [vector] = await self._embedder.embed([embedding_input(draft)], "document")
        await self._repo.answer_gap(gap_id, answer, expert, NewChunk(cid, draft, vector), self._embedder.name)
        return GapAnswered(id=gap_id, status="answered", chunk_id=cid)

    async def reembed_expert_answers(self) -> int:
        """After an embedder switch; article chunks are re-embedded by the next crawl."""
        stale = await self._repo.foreign_expert_chunks(self._embedder.name)
        if not stale:
            return 0
        vectors = await self._embedder.embed([f"{c['context_prefix']}\n{c['text']}" for c in stale], "document")
        for chunk, vector in zip(stale, vectors, strict=True):
            await self._repo.set_embedding(chunk["id"], vector, self._embedder.name)
        return len(stale)

    async def health(self) -> tuple[int, int, datetime | None]:
        return await self._repo.counts()

    async def record_feedback(self, question: str, answer: str, helpful: bool) -> None:
        await self._repo.record_feedback(question, answer, helpful)

    async def _classify(
        self, request: str, answer: str | None, hits: Sequence[SearchHit]
    ) -> tuple[Classification, Usage]:
        try:
            return await self._classifier.classify(request, answer, hits)
        except LLMError as exc:  # a label is a nice-to-have; never lose a grounded answer over it
            log.warning("classification failed: %s", exc)
            fallback = Classification(
                label="unknown",
                setting_path=None,
                warnings=[],
                rationale=f"classifier unavailable: {exc}",
                confidence=0.0,
            )
            return fallback, Usage()

    def _strength(self, hits: Sequence[SearchHit]) -> float:
        """0 below the abstain gate, else how far the best cosine hit clears it (0..1]."""
        best = max((h.similarity for h in hits if h.similarity is not None), default=None)
        threshold = self._policy.min_similarity
        if best is None or best < threshold:
            return 0.0
        return max(0.05, min(1.0, (best - threshold) / max(1e-6, 1.0 - threshold)))

    async def _open_gap(self, question: str, context: str | None, source: str, bot_attempt: str | None) -> str:
        return await self._repo.open_gap(question, context, source, bot_attempt)


def _abstain_note(hits: Sequence[SearchHit], strength: float) -> str:
    if not hits:
        return "Bot abstained: nothing in the index matched."
    titles = "; ".join(dict.fromkeys(h.title for h in hits[:3]))
    reason = "the closest articles did not contain the answer" if strength > 0 else "no article was close enough"
    return f"Bot abstained: {reason}. Closest: {titles}"


def _cited_hits(draft: AnswerDraft | None, hits: Sequence[SearchHit]) -> list[SearchHit]:
    if draft is None:
        return []
    order = dict.fromkeys(c.hit_index for c in draft.citations)
    return [hits[i] for i in order]


def _citations(draft: AnswerDraft | None, hits: Sequence[SearchHit]) -> list[Citation]:
    if draft is None:
        return []
    return [
        Citation(
            article_id=hits[c.hit_index].article_id,
            title=hits[c.hit_index].title,
            url=hits[c.hit_index].url,
            quote=c.quote,
            image_url=hits[c.hit_index].image_url,
        )
        for c in draft.citations
    ]


def _usage_out(usage: Usage, started: float) -> UsageOut:
    return UsageOut(
        input_tokens=usage.input_tokens,
        output_tokens=usage.output_tokens,
        cost_usd=round(usage.cost_usd, 6),
        latency_ms=int((time.perf_counter() - started) * 1000),
    )
