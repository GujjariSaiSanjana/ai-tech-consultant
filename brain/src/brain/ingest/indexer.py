from __future__ import annotations

import logging
from datetime import datetime
from typing import Literal

from brain.crawl.parser import content_hash, is_changelog
from brain.domain import ChunkDraft, ParsedArticle
from brain.ingest.chunker import chunk_article, chunk_id
from brain.ingest.contextualizer import Contextualizer
from brain.ingest.embedder import Embedder
from brain.ingest.images import ImageDescriber, alt_text_description
from brain.llm.client import LLMError
from brain.repo import ImageWrite, NewChunk, Repository

log = logging.getLogger(__name__)
IndexOutcome = Literal["added", "updated", "unchanged"]


def embedding_input(draft: ChunkDraft) -> str:
    return f"{draft.context_prefix}\n{draft.text}"


class Indexer:
    def __init__(
        self,
        repo: Repository,
        embedder: Embedder,
        describer: ImageDescriber,
        contextualizer: Contextualizer | None = None,
    ) -> None:
        self._repo = repo
        self._embedder = embedder
        self._describer = describer
        self._contextualizer = contextualizer

    async def index(self, article: ParsedArticle, *, lastmod: datetime | None, run_id: str | None) -> IndexOutcome:
        digest = content_hash(article)
        state = await self._repo.article_state(article.article_id)
        if state is not None and state == ("active", digest):
            await self._repo.touch_article(article.article_id, lastmod, run_id)
            return "unchanged"

        images = await self._describe_images(article)
        drafts = chunk_article(article, {img.id: (img.description, img.described_by) for img in images})
        if self._contextualizer is not None:
            drafts = await self._situate(article, drafts)

        seen: set[str] = set()
        ids = [chunk_id(article.article_id, d, seen) for d in drafts]
        reusable = await self._repo.chunk_ids(article.article_id, self._embedder.name)
        fresh = [(cid, d) for cid, d in zip(ids, drafts, strict=True) if cid not in reusable]
        vectors = await self._embedder.embed([embedding_input(d) for _, d in fresh], "document") if fresh else []

        await self._repo.write_article(
            article,
            content_hash=digest,
            lastmod=lastmod,
            is_changelog=is_changelog(article),
            run_id=run_id,
            images=images,
            keep_chunk_ids=ids,
            new_chunks=[NewChunk(cid, d, v) for (cid, d), v in zip(fresh, vectors, strict=True)],
            embedding_model=self._embedder.name,
        )
        return "added" if state is None or state[0] == "removed" else "updated"

    async def _describe_images(self, article: ParsedArticle) -> list[ImageWrite]:
        """Re-describe only when the upload key changed or a better describer is now available."""
        stored = await self._repo.stored_images(article.article_id)
        out: list[ImageWrite] = []
        for image in article.images:
            prior = stored.get(image.id)
            reusable = (
                prior is not None
                and prior.change_key == image.change_key
                and prior.description
                and (prior.described_by == "claude" or self._describer.kind == "alt_text")
            )
            if reusable and prior is not None:
                text, by = prior.description, prior.described_by
            else:
                described = await self._describer.describe(image, article.title)
                text, by = described.text, described.described_by
            if not text:
                fallback = alt_text_description(image, article.title)
                text, by = fallback.text, fallback.described_by
            out.append(ImageWrite(image.id, image.url, image.change_key, image.position, image.caption, text, by))
        return out

    async def _situate(self, article: ParsedArticle, drafts: list[ChunkDraft]) -> list[ChunkDraft]:
        assert self._contextualizer is not None
        out: list[ChunkDraft] = []
        for draft in drafts:
            try:
                sentence = await self._contextualizer.situate(article.title, article.body_text, draft.text)
            except LLMError as exc:
                log.warning("contextual prefix failed for %s (%s)", article.article_id, exc)
                sentence = ""
            prefix = f"{draft.context_prefix}. {sentence}" if sentence else draft.context_prefix
            out.append(ChunkDraft(draft.kind, draft.position, draft.heading, draft.text, prefix, draft.image_id))
        return out
