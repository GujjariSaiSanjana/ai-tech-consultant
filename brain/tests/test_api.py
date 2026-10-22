"""HTTP contract: shapes, auth and error envelope, exercised through the real FastAPI app."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator

import httpx
import pytest

from brain.api.app import create_app
from brain.container import Container
from brain.db import Pool
from brain.ingest.embedder import HashEmbedder
from brain.ingest.indexer import Indexer
from brain.models import (
    ArticleResponse,
    AskResponse,
    FeasibilityResponse,
    GapAnswered,
    GapCreated,
    GapList,
    HealthResponse,
    SearchResponse,
    SyncRun,
)
from brain.repo import Repository
from tests.conftest import ServiceFactory
from tests.helpers import FakeSite, fixture_article, fixture_html, make_settings, sitemap_xml

pytestmark = pytest.mark.db
KEY = {"X-API-Key": "test-key"}


@pytest.fixture
async def client(
    db_pool: Pool,
    repo: Repository,
    indexer: Indexer,
    make_service: ServiceFactory,
    site: FakeSite,
    embedder: HashEmbedder,
) -> AsyncIterator[httpx.AsyncClient]:
    for aid in ("5000558389", "12000048777"):
        await indexer.index(fixture_article(aid), lastmod=None, run_id=None)
    site_http = httpx.AsyncClient(transport=site.transport(), base_url="https://support.brilliantdirectories.com")
    container = Container(
        settings=make_settings(BRAIN_CRAWL_INTERVAL_S=0.0),
        pool=db_pool,
        repo=repo,
        embedder=embedder,
        service=make_service(),
        indexer=indexer,
        site_http=site_http,
        llm=None,
    )
    app = create_app(container=container)
    app.state.container = container
    app.state.background = set()
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://brain.test") as c:
        yield c
    await site_http.aclose()


async def test_health_needs_no_key(client: httpx.AsyncClient) -> None:
    response = await client.get("/health")
    assert response.status_code == 200
    body = HealthResponse.model_validate(response.json())
    assert body.articles == 2 and body.chunks > 0


@pytest.mark.parametrize("headers", [{}, {"X-API-Key": "wrong"}])
async def test_v1_requires_api_key(client: httpx.AsyncClient, headers: dict[str, str]) -> None:
    response = await client.post("/v1/search", json={"query": "x"}, headers=headers)
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "unauthorized"


async def test_validation_errors_use_the_error_envelope(client: httpx.AsyncClient) -> None:
    response = await client.post("/v1/ask", json={"question": ""}, headers=KEY)
    assert response.status_code == 422
    assert set(response.json()) == {"error"} and response.json()["error"]["code"] == "invalid_request"


async def test_ask_answered_shape(client: httpx.AsyncClient) -> None:
    response = await client.post(
        "/v1/ask", json={"question": "What URL format does the TikTok social media link need?"}, headers=KEY
    )
    assert response.status_code == 200
    body = AskResponse.model_validate(response.json())
    assert set(response.json()) == set(AskResponse.model_fields)
    assert body.status == "answered" and body.citations
    assert set(response.json()["citations"][0]) == {"article_id", "title", "url", "quote", "image_url"}
    assert set(response.json()["usage"]) == {"input_tokens", "output_tokens", "cost_usd", "latency_ms"}


async def test_ask_no_answer_opens_gap(client: httpx.AsyncClient) -> None:
    body = AskResponse.model_validate(
        (await client.post("/v1/ask", json={"question": "How do I bake sourdough bread?"}, headers=KEY)).json()
    )
    assert body.status == "no_answer" and body.answer == "" and body.gap_ticket_id
    gaps = GapList.model_validate((await client.get("/v1/gaps?status=open", headers=KEY)).json())
    assert [g.id for g in gaps.items] == [body.gap_ticket_id]


async def test_search_shape(client: httpx.AsyncClient) -> None:
    response = await client.post("/v1/search", json={"query": "facebook link", "top_k": 3}, headers=KEY)
    body = SearchResponse.model_validate(response.json())
    assert 0 < len(body.results) <= 3
    assert set(response.json()["results"][0]) == set(
        ["chunk_id", "article_id", "title", "url", "category", "folder", "kind", "text", "score"]
    )


async def test_feasibility_shape(client: httpx.AsyncClient) -> None:
    response = await client.post("/v1/feasibility", json={"requirement": "Show our TikTok link"}, headers=KEY)
    body = FeasibilityResponse.model_validate(response.json())
    assert set(response.json()) == {"kind", "setting_path", "rationale", "confidence", "citations"}
    assert body.kind in {"setting", "addon", "widget_customization", "custom_dev", "unknown"}


async def test_article_found_and_not_found(client: httpx.AsyncClient) -> None:
    ok = await client.get("/v1/articles/5000558389", headers=KEY)
    article = ArticleResponse.model_validate(ok.json())
    assert article.status == "active" and len(article.images) == 2
    missing = await client.get("/v1/articles/1", headers=KEY)
    assert missing.status_code == 404 and missing.json()["error"]["code"] == "not_found"


async def test_gap_lifecycle(client: httpx.AsyncClient) -> None:
    created = GapCreated.model_validate(
        (
            await client.post(
                "/v1/gaps", json={"question": "Klarna?", "context": "from inbox", "source": "inbox"}, headers=KEY
            )
        ).json()
    )
    assert created.status == "open"
    answered = GapAnswered.model_validate(
        (
            await client.post(
                f"/v1/gaps/{created.id}/answer", json={"answer": "Not supported.", "expert": "Ana"}, headers=KEY
            )
        ).json()
    )
    assert answered.status == "answered" and answered.chunk_id
    items = GapList.model_validate((await client.get("/v1/gaps?status=answered", headers=KEY)).json()).items
    assert items[0].answer == "Not supported." and items[0].source == "inbox"
    unknown = await client.post("/v1/gaps/gap_nope/answer", json={"answer": "x", "expert": "y"}, headers=KEY)
    assert unknown.status_code == 404
    bad_source = await client.post("/v1/gaps", json={"question": "q", "source": "email"}, headers=KEY)
    assert bad_source.status_code == 422


async def test_sync_run_and_status(client: httpx.AsyncClient, site: FakeSite) -> None:
    site.sitemap = sitemap_xml({"5000558389": "2026-07-01T00:00:00+00:00", "12000095723": "2026-07-01T00:00:00+00:00"})
    site.pages = {aid: fixture_html(aid) for aid in ("5000558389", "12000095723")}
    started = await client.post("/v1/sync/run", headers=KEY)
    assert started.status_code == 202
    run_id = started.json()["run_id"]
    for _ in range(100):
        run = SyncRun.model_validate((await client.get(f"/v1/sync/runs/{run_id}", headers=KEY)).json())
        if run.status != "running":
            break
        await asyncio.sleep(0.05)
    assert run.status == "done"
    assert (run.added, run.updated, run.removed, run.unchanged) == (1, 0, 1, 1)
    missing = await client.get("/v1/sync/runs/run_nope", headers=KEY)
    assert missing.status_code == 404


async def test_openapi_documents_every_contract_path(client: httpx.AsyncClient) -> None:
    paths = set((await client.get("/openapi.json")).json()["paths"])
    assert {
        "/health",
        "/v1/ask",
        "/v1/search",
        "/v1/feasibility",
        "/v1/articles/{article_id}",
        "/v1/gaps",
        "/v1/gaps/{gap_id}/answer",
        "/v1/sync/run",
        "/v1/sync/runs/{run_id}",
    } <= paths
    assert not any(p.startswith("/ui") for p in paths)


async def test_ui_requires_login(client: httpx.AsyncClient) -> None:
    response = await client.get("/experts")
    assert response.status_code == 401
    assert response.headers["www-authenticate"].startswith("Basic")
    assert (await client.get("/experts", auth=("ana", "wrong"))).status_code == 401


async def test_ui_pages_render(client: httpx.AsyncClient) -> None:
    auth = ("ana", "test-key")
    assert "Ask about Brilliant Directories" in (await client.get("/", auth=auth)).text
    partial = await client.post(
        "/ui/ask",
        data={"question": "What URL format does the TikTok link need?"},
        headers={"HX-Request": "true"},
        auth=auth,
    )
    assert partial.status_code == 200 and "Sources" in partial.text and "<html" not in partial.text
    unknown = await client.post("/ui/ask", data={"question": "How do I bake sourdough bread?"}, auth=auth)
    assert "<html" in unknown.text and "I don't know yet" in unknown.text
    experts = await client.get("/experts", auth=auth)
    assert "How do I bake sourdough bread?" in experts.text
