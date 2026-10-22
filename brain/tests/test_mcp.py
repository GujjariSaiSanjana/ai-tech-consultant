from __future__ import annotations

import json
from typing import Any

import httpx
import pytest
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import CallToolResult

from brain.mcp_server import build_server

ASK: dict[str, Any] = {
    "status": "no_answer",
    "answer": "",
    "label": "unknown",
    "setting_path": None,
    "warnings": [],
    "citations": [],
    "confidence": 0.0,
    "gap_ticket_id": "gap_1",
    "model": "offline-extractive",
    "usage": {"input_tokens": 0, "output_tokens": 0, "cost_usd": 0.0, "latency_ms": 3},
}


def _api(calls: list[tuple[str, dict[str, Any], str | None]]) -> httpx.AsyncClient:
    def handle(request: httpx.Request) -> httpx.Response:
        calls.append((request.url.path, json.loads(request.content), request.headers.get("X-API-Key")))
        if request.url.path == "/v1/ask":
            return httpx.Response(200, json=ASK)
        return httpx.Response(401, json={"error": {"code": "unauthorized", "message": "bad key"}})

    return httpx.AsyncClient(
        transport=httpx.MockTransport(handle), base_url="http://brain.test", headers={"X-API-Key": "k"}
    )


async def test_lists_the_four_contract_tools() -> None:
    tools = {t.name: t for t in await build_server(_api([])).list_tools()}
    assert set(tools) == {"kb_search", "kb_ask", "kb_feasibility", "kb_create_gap"}
    assert set(tools["kb_ask"].input_schema["properties"]) == {"question", "top_k", "category"}
    assert set(tools["kb_create_gap"].input_schema["properties"]) == {"question", "source", "context"}
    assert tools["kb_ask"].output_schema is not None


async def test_tools_call_the_http_api() -> None:
    calls: list[tuple[str, dict[str, Any], str | None]] = []
    result = await build_server(_api(calls)).call_tool("kb_ask", {"question": "Klarna?"})
    assert isinstance(result, CallToolResult)
    assert calls == [("/v1/ask", {"question": "Klarna?", "top_k": 8, "category": None}, "k")]
    assert result.structured_content is not None and result.structured_content["gap_ticket_id"] == "gap_1"


async def test_api_errors_surface_as_tool_errors_with_their_message() -> None:
    with pytest.raises(ToolError, match="401 unauthorized: bad key"):
        await build_server(_api([])).call_tool("kb_search", {"query": "x"})
