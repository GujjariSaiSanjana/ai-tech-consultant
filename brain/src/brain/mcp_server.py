"""`python -m brain.mcp_server`: MCP (stdio) tools that wrap the HTTP API one to one.

Tools call the running API at BRAIN_URL with BRAIN_API_KEY, so MCP clients see exactly the
contract's inputs and outputs and the server holds no database or model state of its own.
"""

from __future__ import annotations

from typing import Any, Literal

import httpx
from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError

from brain.config import Settings
from brain.models import AskResponse, FeasibilityResponse, GapCreated, SearchResponse

INSTRUCTIONS = """Knowledge Brain for the Brilliant Directories help center. Use kb_ask for cited answers
(it says no_answer instead of guessing), kb_search for raw passages, kb_feasibility to decide whether a
requirement is a built-in setting, an add-on, a widget customization or custom development, and
kb_create_gap to hand a question to a human expert."""


class BrainApiError(ToolError):
    """A ToolError's message reaches the MCP client; any other exception is reported as a bare crash."""


def build_server(http: httpx.AsyncClient) -> MCPServer:
    server = MCPServer("knowledge-brain", instructions=INSTRUCTIONS, version="1.0.0")

    async def post(path: str, body: dict[str, Any]) -> dict[str, Any]:
        response = await http.post(path, json=body)
        if response.status_code >= 400:
            try:
                error = response.json()["error"]
                message = f"{error['code']}: {error['message']}"
            except (ValueError, KeyError, TypeError):
                message = response.text[:300]
            raise BrainApiError(f"Brain API {response.status_code} {message}")
        data: dict[str, Any] = response.json()
        return data

    @server.tool(description="Hybrid search over the help center. Returns passages with article ids and scores.")
    async def kb_search(query: str, top_k: int = 8, category: str | None = None) -> SearchResponse:
        return SearchResponse.model_validate(
            await post("/v1/search", {"query": query, "top_k": top_k, "category": category})
        )

    @server.tool(
        description="Answer a question from the help center with citations, a solution label, the setting path "
        "and warnings. status is no_answer (and an expert ticket is opened) when the docs do not cover it."
    )
    async def kb_ask(question: str, top_k: int = 8, category: str | None = None) -> AskResponse:
        return AskResponse.model_validate(
            await post("/v1/ask", {"question": question, "top_k": top_k, "category": category})
        )

    @server.tool(description="Classify a requirement as setting, addon, widget_customization, custom_dev or unknown.")
    async def kb_feasibility(requirement: str) -> FeasibilityResponse:
        return FeasibilityResponse.model_validate(await post("/v1/feasibility", {"requirement": requirement}))

    @server.tool(description="Open a task for a human expert. source is chat, inbox or scope.")
    async def kb_create_gap(
        question: str, source: Literal["chat", "inbox", "scope"], context: str | None = None
    ) -> GapCreated:
        return GapCreated.model_validate(
            await post("/v1/gaps", {"question": question, "context": context, "source": source})
        )

    return server


def main() -> None:
    settings = Settings()
    http = httpx.AsyncClient(base_url=settings.public_url, headers={"X-API-Key": settings.api_key}, timeout=120.0)
    build_server(http).run("stdio")


if __name__ == "__main__":
    main()
