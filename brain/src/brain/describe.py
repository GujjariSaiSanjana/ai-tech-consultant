"""`python -m brain.describe [--batch] [--limit N]`: replace alt-text screenshot descriptions with a vision model.

The crawl indexes screenshots immediately with their alt text and caption so nothing waits on a
vision model. This step upgrades them afterwards: one live call per image, or one Message Batch
(`--batch`, half price, usually done within the hour) for the initial backfill. Live calls fall
back to Gemini when Claude is unavailable; batches are Claude-only.
"""

from __future__ import annotations

import argparse
import asyncio
import logging

from brain.config import Settings
from brain.container import Container, build_container
from brain.domain import ImageRef
from brain.ingest.chunker import chunk_id, image_chunk
from brain.ingest.embedder import Embedder
from brain.ingest.images import message_text, vision_messages
from brain.ingest.indexer import embedding_input
from brain.llm.client import LLMClient
from brain.migrate import migrate
from brain.repo import NewChunk, PendingImage, Repository

log = logging.getLogger(__name__)


def _key(p: PendingImage) -> str:
    """Batch custom_id: unique per (article, attachment); only [A-Za-z0-9_-] is allowed."""
    return f"{p.article_id}_{p.id}"


def _ref(p: PendingImage) -> ImageRef:
    return ImageRef(id=p.id, url=p.url, change_key=p.change_key, position=p.position, caption=p.caption)


async def apply_description(repo: Repository, embedder: Embedder, pending: PendingImage, description: str) -> None:
    draft = image_chunk(
        pending.id, pending.heading, description, "claude", pending.context_prefix, position=pending.chunk_position
    )
    [vector] = await embedder.embed([embedding_input(draft)], "document")
    chunk = NewChunk(chunk_id(pending.article_id, draft, set()), draft, vector)
    await repo.upgrade_image(pending.id, description, chunk, pending.article_id, embedder.name)


async def describe_images(
    repo: Repository,
    embedder: Embedder,
    llm: LLMClient,
    *,
    limit: int | None,
    batch: bool,
    poll_s: float = 30.0,
) -> int:
    pending = await repo.pending_images(limit)
    if not pending:
        return 0
    if batch:
        batch_id = await llm.submit_batch("vision", [(_key(p), vision_messages(_ref(p), p.title)) for p in pending])
        log.info("submitted batch %s with %d screenshots", batch_id, len(pending))
        texts = {k: message_text(m) for k, m in (await llm.collect_batch("vision", batch_id, poll_s=poll_s)).items()}
    else:
        texts = {}
        for p in pending:
            message, _ = await llm.create("vision", messages=vision_messages(_ref(p), p.title))
            texts[_key(p)] = message_text(message)
    done = 0
    for p in pending:
        if text := texts.get(_key(p), "").strip():
            await apply_description(repo, embedder, p, text)
            done += 1
    return done


async def _main(limit: int | None, batch: bool) -> None:
    settings = Settings()
    if batch and not settings.anthropic_api_key:
        raise SystemExit("--batch needs ANTHROPIC_API_KEY (Message Batches have no fallback)")
    await migrate(settings.database_url)
    async with build_container(settings) as c:
        done = await describe_images(c.repo, c.embedder, _llm(c), limit=limit, batch=batch)
    print(f"described {done} screenshot(s)")


def _llm(c: Container) -> LLMClient:
    if c.llm is None or not c.llm.can_serve("vision"):
        raise SystemExit("no vision model (ANTHROPIC_API_KEY, or GEMINI_API_KEY as fallback); keeping alt text")
    return c.llm


def main() -> None:
    parser = argparse.ArgumentParser(prog="python -m brain.describe", description=__doc__)
    parser.add_argument("--batch", action="store_true", help="use the Message Batches API (50%% cheaper)")
    parser.add_argument("--limit", type=int, default=None)
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    asyncio.run(_main(args.limit, args.batch))


if __name__ == "__main__":
    main()
