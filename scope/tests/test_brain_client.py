from __future__ import annotations

import json

import httpx
import pytest

from scope.brain.client import BrainUnavailableError, HttpBrainClient

ANSWER = {"kind": "setting", "setting_path": "Settings > Advanced Settings > Members > Suspend Past Due",
          "rationale": "Built in.", "confidence": 0.9,
          "citations": [{"article_id": "12000047895", "title": "Suspend", "url": "https://x/1",
                         "quote": "Suspend past due members", "image_url": None}]}


def client(handler: httpx.MockTransport) -> HttpBrainClient:
    return HttpBrainClient("http://brain", "k-123", timeout_s=1, transport=handler)


async def test_feasibility_contract_and_api_key_header() -> None:
    seen: list[httpx.Request] = []

    def handle(req: httpx.Request) -> httpx.Response:
        seen.append(req)
        return httpx.Response(200, json=ANSWER)

    ans = await client(httpx.MockTransport(handle)).feasibility("suspend failed payments")
    assert ans.kind == "setting" and ans.citations[0].article_id == "12000047895"
    assert seen[0].url.path == "/v1/feasibility"
    assert seen[0].headers["X-API-Key"] == "k-123"
    assert json.loads(seen[0].content) == {"requirement": "suspend failed payments"}


async def test_search_contract() -> None:
    hit = {"chunk_id": "c", "article_id": "a", "title": "t", "url": "u", "category": "c",
           "folder": "f", "kind": "text", "text": "x", "score": 0.5}

    def handle(req: httpx.Request) -> httpx.Response:
        assert json.loads(req.content) == {"query": "q", "top_k": 5}
        return httpx.Response(200, json={"results": [hit]})

    res = await client(httpx.MockTransport(handle)).search("q")
    assert res.results[0].article_id == "a"


@pytest.mark.parametrize(
    "response",
    [
        httpx.Response(500, json={"error": {"code": "internal", "message": "x"}}),
        httpx.Response(401, json={"error": {"code": "unauthorized", "message": "bad key"}}),
        httpx.Response(200, json={"kind": "maybe"}),
        httpx.Response(502, text="<html>bad gateway</html>"),
    ],
)
async def test_errors_become_unavailable(response: httpx.Response) -> None:
    with pytest.raises(BrainUnavailableError):
        await client(httpx.MockTransport(lambda r: response)).feasibility("x")


async def test_network_error_becomes_unavailable() -> None:
    def handle(req: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused")

    with pytest.raises(BrainUnavailableError):
        await client(httpx.MockTransport(handle)).search("x")
