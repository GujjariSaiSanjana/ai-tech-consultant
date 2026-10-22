"""Upgrading alt-text screenshots with Claude vision, live and through the Message Batches API."""

from __future__ import annotations

import json
from typing import Any

import anthropic
import httpx2
import pytest

from brain.describe import describe_images
from brain.ingest.embedder import HashEmbedder
from brain.ingest.indexer import Indexer
from brain.llm.client import LLMClient
from brain.repo import Repository
from tests.helpers import fixture_article, make_settings
from tests.test_llm import Recorder, _message

pytestmark = pytest.mark.db
BATCH = {
    "id": "msgbatch_1",
    "type": "message_batch",
    "created_at": "2026-10-22T00:00:00Z",
    "expires_at": "2026-10-23T00:00:00Z",
    "request_counts": {"processing": 0, "succeeded": 2, "errored": 0, "canceled": 0, "expired": 0},
}


def _batch_api(seen: dict[str, Any]) -> LLMClient:
    polls = {"n": 0}

    def handler(request: httpx2.Request) -> httpx2.Response:
        path = request.url.path
        if request.method == "POST" and path == "/v1/messages/batches":
            seen["requests"] = json.loads(request.content)["requests"]
            return httpx2.Response(200, json=BATCH | {"processing_status": "in_progress"})
        if path == "/v1/messages/batches/msgbatch_1":
            polls["n"] += 1
            ended = polls["n"] > 1
            return httpx2.Response(
                200,
                json=BATCH
                | {
                    "processing_status": "ended" if ended else "in_progress",
                    "results_url": "https://api.anthropic.com/v1/messages/batches/msgbatch_1/results"
                    if ended
                    else None,
                },
            )
        if path.endswith("/results"):
            lines = [
                {
                    "custom_id": r["custom_id"],
                    "result": {
                        "type": "succeeded",
                        "message": _message(
                            [
                                {
                                    "type": "text",
                                    "text": f"Footer menu with Facebook and Instagram icons ({r['custom_id']})",
                                }
                            ],
                            model="claude-haiku-5-5",
                        ),
                    },
                }
                for r in seen["requests"]
            ]
            return httpx2.Response(200, content="\n".join(json.dumps(x) for x in lines).encode())
        raise AssertionError(f"unexpected {request.method} {path}")

    sdk = anthropic.AsyncAnthropic(
        api_key="sk-test",
        max_retries=0,
        http_client=anthropic.DefaultAsyncHttpxClient(transport=httpx2.MockTransport(handler)),
    )
    seen["recorder"] = Recorder()
    return LLMClient(sdk, make_settings(), seen["recorder"])


async def test_batch_upgrades_alt_text_screenshots(repo: Repository, indexer: Indexer, embedder: HashEmbedder) -> None:
    await indexer.index(fixture_article("5000558389"), lastmod=None, run_id=None)
    assert len(await repo.pending_images(None)) == 2
    seen: dict[str, Any] = {}
    llm = _batch_api(seen)
    done = await describe_images(repo, embedder, llm, limit=None, batch=True, poll_s=0.0)
    assert done == 2
    body = seen["requests"][0]["params"]
    assert body["model"] == "claude-haiku-5-5" and body["messages"][0]["content"][0]["type"] == "image"
    assert not {"temperature", "top_p", "top_k"} & body.keys()
    assert await repo.pending_images(None) == []
    recorder: Recorder = seen["recorder"]
    assert recorder.calls[0].cost_usd == pytest.approx((1200 * 0.10 + 80 * 0.50) / 1e6 * 0.5)

    async with repo.pool.connection() as conn:
        cur = await conn.execute("SELECT text, boost FROM chunks WHERE kind = 'image' ORDER BY text")
        rows = await cur.fetchall()
    assert len(rows) == 2
    assert all("Instagram icons" in r["text"] and r["boost"] == 1.0 for r in rows)

    # Re-indexing the unchanged article keeps the Claude descriptions instead of reverting to alt text.
    await indexer.index(fixture_article("5000558389"), lastmod=None, run_id=None)
    assert await repo.pending_images(None) == []


async def test_poll_waits_for_the_batch_to_end(repo: Repository, indexer: Indexer, embedder: HashEmbedder) -> None:
    await indexer.index(fixture_article("12000048777"), lastmod=None, run_id=None)
    seen: dict[str, Any] = {}
    llm = _batch_api(seen)
    batch_id = await llm.submit_batch("vision", [("x", [{"role": "user", "content": "hi"}])])
    sleeps: list[float] = []

    async def sleep(seconds: float) -> None:
        sleeps.append(seconds)

    results = await llm.collect_batch("vision", batch_id, poll_s=5.0, sleep=sleep)
    assert sleeps == [5.0] and set(results) == {"x"}
