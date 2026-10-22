# Knowledge Brain API contract (v1)

Tasks 2 and 3 call the Knowledge Brain only through this HTTP API (or the MCP server that wraps it).
They never import its code. Base URL comes from `BRAIN_URL` (default `http://localhost:8000`).
Every request sends header `X-API-Key: $BRAIN_API_KEY`. All bodies are JSON. Errors use
`{"error": {"code": str, "message": str}}` with a matching HTTP status.

## Shared types

```
Label = "setting" | "addon" | "widget_customization" | "custom_dev" | "unknown"
ChunkKind = "text" | "image" | "expert"

Citation {
  article_id: str          # Freshdesk article id, e.g. "12000047895"
  title: str
  url: str                 # canonical article URL
  quote: str               # exact cited passage
  image_url: str | null    # set when the cited chunk describes a screenshot
}
```

## Endpoints

### GET /health
`200 {"status": "ok", "articles": int, "chunks": int, "last_sync": iso8601 | null}`

### POST /v1/ask
Request `{"question": str, "top_k": int = 8, "category": str | null}`
Response
```
{
  "status": "answered" | "no_answer",
  "answer": str,                 # empty string when no_answer
  "label": Label,                # how the request is solved in the product
  "setting_path": str | null,    # e.g. "Settings > Advanced Settings > Members > Suspend Past Due"
  "warnings": [str],             # e.g. "Customizing this widget stops it receiving updates"
  "citations": [Citation],
  "confidence": float,           # 0..1
  "gap_ticket_id": str | null,   # set when status == no_answer (expert task created)
  "model": str,
  "usage": {"input_tokens": int, "output_tokens": int, "cost_usd": float, "latency_ms": int}
}
```

### POST /v1/search
Request `{"query": str, "top_k": int = 8, "category": str | null}`
Response `{"results": [{"chunk_id": str, "article_id": str, "title": str, "url": str,
"category": str, "folder": str, "kind": ChunkKind, "text": str, "score": float}]}`

### POST /v1/feasibility
Used by Task 3 to decide "existing setting vs custom code".
Request `{"requirement": str}`
Response `{"kind": Label, "setting_path": str | null, "rationale": str, "confidence": float,
"citations": [Citation]}`

### GET /v1/articles/{article_id}
`200 {"article_id", "title", "url", "category", "folder", "body_text", "images": [{"url", "description"}],
"lastmod": iso8601, "status": "active" | "removed"}`; `404` if unknown.

### POST /v1/gaps
Create an expert task. Request `{"question": str, "context": str | null, "source": "chat" | "inbox" | "scope"}`
Response `{"id": str, "status": "open"}`

### GET /v1/gaps?status=open|answered
`{"items": [{"id", "question", "context", "source", "status", "bot_attempt", "answer", "created_at"}]}`

### POST /v1/gaps/{id}/answer
Request `{"answer": str, "expert": str}`. The answer is stored as a curated `expert` chunk and is
retrievable by /v1/ask and /v1/search immediately. Response `{"id", "status": "answered", "chunk_id"}`

### POST /v1/sync/run
Starts an incremental sync. `202 {"run_id": str}`
### GET /v1/sync/runs/{run_id}
`{"run_id", "status": "running" | "done" | "failed", "added": int, "updated": int, "removed": int,
"unchanged": int, "errors": int, "started_at", "finished_at"}`

## MCP

`python -m brain.mcp_server` exposes tools `kb_search`, `kb_ask`, `kb_feasibility`, `kb_create_gap`
with the same inputs and outputs as the endpoints above (stdio transport).
