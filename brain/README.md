# Knowledge Brain (Task 1)

The memory the rest of AI Tech Consultant depends on: it reads the public Brilliant Directories
Solution Center once (politely, resumable), keeps it fresh every day, answers questions with
citations, says honestly when it does not know, and turns those gaps into tasks for a human expert
whose answers flow straight back into memory.

Every answer also carries what nobody wrote down: **how** the request is solved in the product
(`setting | addon | widget_customization | custom_dev | unknown`), **where to click**
(`setting_path`), and **warnings** such as "customizing a default widget stops it receiving updates".

Tasks 2 and 3 use it only through the HTTP API in [`docs/contracts/brain-api.md`](../docs/contracts/brain-api.md)
or the MCP server that wraps it.

```
 sitemap.xml ──► crawl / sync engine ──► parser ──► chunker ──► embedder ──► Postgres + pgvector
   (lastmod      (crawl_items: resumable,  (title, breadcrumb,   (H2/H3,      (voyage | local |     articles, images,
    = hint)       1 req/s, robots, backoff) sections, screenshots) 300-600 tok)  hash)              chunks(vector, tsvector)
                                                                                                      │
 question ─► embed ─► hybrid search (HNSW cosine + full text, RRF k=60, active only, boosts) ◄────────┘
                         │ best cosine below the gate? ──► no_answer + gap ticket
                         ▼
               Claude Sonnet: chunks as document blocks, citations on ── NO_ANSWER? ──► gap ticket
                         ▼                                                              │
               Claude Haiku: label, setting_path, warnings (structured output)      expert answers
                         ▼                                                              │ (UI or API)
               AskResponse: answer, citations, label, path, warnings, confidence      ▼
                                                                         expert chunk, retrievable at once
```

## Quick start

Requirements: [uv](https://docs.astral.sh/uv/), Postgres 17 with pgvector 0.8 (`CREATE EXTENSION vector` must work).

```bash
cd brain
createdb brain && createdb brain_test        # once
uv sync                                      # installs Python deps (Python >= 3.12)
uv run python -m brain.migrate               # applies migrations/*.sql
uv run python -m brain.crawl --limit 90      # polite sample crawl (~2 min); omit --limit for all 1540 (~31 min)
uv run python -m brain.api                   # http://localhost:8000  (UI at /, API docs at /docs)
```

The first run of the local embedder downloads `BAAI/bge-small-en-v1.5` (~130 MB) once.

Configuration comes from environment variables (or a `.env` in the repo root or `brain/`):

| Variable | Default | Purpose |
|---|---|---|
| `BRAIN_DATABASE_URL` | `postgresql://postgres:postgres@localhost:5432/brain` | Postgres with pgvector |
| `BRAIN_API_KEY` | `dev-brain-key` | Shared secret for `X-API-Key` on `/v1/*` |
| `BRAIN_URL` | `http://localhost:8000` | Public base URL (expert citation links, MCP server target) |
| `ANTHROPIC_API_KEY` | unset | Enables Claude answering, labels and vision. Unset = offline mode (below) |
| `VOYAGE_API_KEY` | unset | Enables the `voyage` embedder |
| `BRAIN_EMBEDDER` | `voyage` if a Voyage key is set, else `local` | `voyage` (voyage-4, 1024-d), `local` (fastembed bge-small, 384-d), `hash` (tests only) |
| `BRAIN_ANSWER_MODEL` / `BRAIN_CLASSIFY_MODEL` / `BRAIN_VISION_MODEL` | `claude-sonnet-5-5` / `claude-haiku-5-5` / `claude-haiku-5-5` | Model routing |
| `BRAIN_MIN_SIMILARITY` | per embedder (local 0.60, voyage 0.35) | Abstain gate on the best cosine hit |
| `BRAIN_CHANGELOG_BOOST` / `BRAIN_EXPERT_BOOST` | `0.5` / `1.5` | Ranking multipliers |
| `BRAIN_CONTEXTUAL_PREFIX` | `false` | Haiku-written situating sentence per chunk (contextual retrieval) |
| `BRAIN_CRAWL_INTERVAL_S` | `1.0` | Minimum gap between requests to the help center |
| `BRAIN_SYNC_HOUR` | `3` | Hour (local time) of the daily sync |
| `BRAIN_TEST_DATABASE_URL` | `postgresql://postgres:postgres@localhost:5432/brain_test` | Used by the tests; its `public` schema is dropped and recreated |

## Commands

All from `brain/`:

| Command | What it does |
|---|---|
| `uv run pytest` | Full test suite, offline (needs the local `brain_test` database for DB tests) |
| `uv run python -m brain.migrate` | Apply pending SQL migrations |
| `uv run python -m brain.crawl [--limit N] [--resume] [--save-html DIR]` | Initial crawl; `--limit` samples evenly across the sitemap; `--resume` continues the last unfinished crawl |
| `uv run python -m brain.sync [--limit N] [--resume] [--sitemap-file F]` | One incremental sync (added / updated / removed / unchanged) |
| `uv run python -m brain.api` | API + UI on `BRAIN_HOST:BRAIN_PORT` (127.0.0.1:8000) |
| `uv run python -m brain.scheduler` | Runs the sync daily at 03:00 (resumes an interrupted sync first) |
| `uv run python -m brain.mcp_server` | MCP server over stdio (needs the API running) |
| `uv run python -m brain.describe [--batch] [--limit N]` | Replace alt-text screenshot descriptions with Claude vision ones (`--batch` = Message Batches, half price) |
| `uv run python -m brain.evals.run [--models a,b] [--split dev\|heldout] [--mock] [--reps N]` | Eval run; rows go to `evals/results/brain/<run_id>.jsonl` |

Lint and types: `uv run ruff check src tests && uv run ruff format --check src tests && uv run mypy` (strict).

Docker: `docker build -t brain .` then `docker run -p 8000:8000 -e BRAIN_DATABASE_URL=... brain`
(any other entry point: `docker run brain python -m brain.scheduler`).

## Using the API

Base URL `BRAIN_URL`, header `X-API-Key: $BRAIN_API_KEY` on every `/v1` call, JSON bodies.
Errors are always `{"error": {"code": str, "message": str}}` with a matching status
(`401 unauthorized`, `404 not_found`, `422 invalid_request`, `502 llm_error`, `503 llm_unavailable`, `500 internal`).
Interactive docs with every schema: `GET /docs` (OpenAPI at `/openapi.json`).

```bash
export BRAIN=http://localhost:8000 KEY=dev-brain-key

# Health (no key needed): index size and last successful sync
curl -s $BRAIN/health
# {"status":"ok","articles":1540,"chunks":...,"last_sync":"2026-10-22T15:10:02Z"}

# Ask: cited answer + label + where to click + warnings
curl -s $BRAIN/v1/ask -H "X-API-Key: $KEY" -H 'content-type: application/json' \
  -d '{"question": "How do I change the phone number shown in the site header?"}'
# {"status":"answered","answer":"...","label":"setting",
#  "setting_path":"Settings > General Settings > General > Website Phone Number","warnings":[],
#  "citations":[{"article_id":"12000048777","title":"How to Update Website's Phone Number",
#                "url":"https://support.brilliantdirectories.com/...","quote":"...","image_url":null}],
#  "confidence":0.8,"gap_ticket_id":null,"model":"claude-sonnet-5-5",
#  "usage":{"input_tokens":...,"output_tokens":...,"cost_usd":...,"latency_ms":...}}

# When the docs do not cover it: status no_answer, empty answer, and an expert ticket
curl -s $BRAIN/v1/ask -H "X-API-Key: $KEY" -H 'content-type: application/json' \
  -d '{"question": "Can members pay in Bitcoin?"}'
# {"status":"no_answer","answer":"","label":"unknown",...,"gap_ticket_id":"gap_3f2a..."}

# Raw hybrid search (optionally within one category)
curl -s $BRAIN/v1/search -H "X-API-Key: $KEY" -H 'content-type: application/json' \
  -d '{"query": "301 redirect import csv", "top_k": 5, "category": "Developer Hub"}'

# Feasibility for scoping: setting vs add-on vs widget customization vs custom development
curl -s $BRAIN/v1/feasibility -H "X-API-Key: $KEY" -H 'content-type: application/json' \
  -d '{"requirement": "Automatically send new leads to matching members"}'
# {"kind":"addon","setting_path":null,"rationale":"...","confidence":0.7,"citations":[...]}

# One article with its screenshot descriptions
curl -s $BRAIN/v1/articles/12000048777 -H "X-API-Key: $KEY"

# Expert loop: open a task, list open tasks, answer it (answer is retrievable immediately)
curl -s $BRAIN/v1/gaps -H "X-API-Key: $KEY" -H 'content-type: application/json' \
  -d '{"question": "Does the Stripe integration support Klarna?", "context": "client email", "source": "inbox"}'
curl -s "$BRAIN/v1/gaps?status=open" -H "X-API-Key: $KEY"
curl -s $BRAIN/v1/gaps/gap_3f2a.../answer -H "X-API-Key: $KEY" -H 'content-type: application/json' \
  -d '{"answer": "Not built in; Stripe Checkout payment methods are not exposed.", "expert": "Ana"}'
# {"id":"gap_3f2a...","status":"answered","chunk_id":"..."}

# Sync on demand, then poll
curl -s -X POST $BRAIN/v1/sync/run -H "X-API-Key: $KEY"          # 202 {"run_id":"run_..."}
curl -s $BRAIN/v1/sync/runs/run_... -H "X-API-Key: $KEY"
# {"run_id":"run_...","status":"done","added":2,"updated":5,"removed":1,"unchanged":1532,"errors":0,...}
```

Notes for callers:

- `search` scores are reciprocal-rank-fusion scores after boosts; compare them within one response only.
- Citations of curated expert answers use `article_id = "expert:<gap id>"` and link to `/experts#<gap id>`.
- `POST /v1/gaps` reuses an identical open question instead of creating a duplicate.
- `/health` is open (no key) so container health checks can call it.

### MCP

```json
{"mcpServers": {"knowledge-brain": {
  "command": "uv", "args": ["--directory", "/path/to/repo/brain", "run", "python", "-m", "brain.mcp_server"],
  "env": {"BRAIN_URL": "http://localhost:8000", "BRAIN_API_KEY": "dev-brain-key"}}}}
```

Tools `kb_search`, `kb_ask`, `kb_feasibility`, `kb_create_gap` take and return exactly the endpoint shapes.

### UI

`/` is a chat page (answer, numbered sources with quotes and screenshot links, label badge, where to
click, warnings, **Helpful** and **Ask an expert**). `/experts` is the expert queue (open questions with
the bot's attempt and an answer box; recently answered below). Server-rendered with Jinja2 + HTMX,
works without JavaScript and on a phone. The UI has no login: run it on a trusted network.

## Offline mode (no `ANTHROPIC_API_KEY`)

Everything still runs end to end, honestly labelled:

- answers come from an **extractive** answerer that only quotes retrieved lines (model `offline-extractive`)
  and abstains when they do not cover the question; it cannot invent anything, but it is not Claude;
- labels come from a keyword heuristic over the question and the retrieved titles;
- screenshots are described by their alt text and surrounding caption (`described_by = 'alt_text'`,
  down-weighted in ranking) until `python -m brain.describe` runs with a key;
- eval rows are written with `mocked: true`.

## Design choices and why

**Crawling.** One request at a time, at least 1 s apart plus jitter, identifying User-Agent, robots.txt
through `urllib.robotparser` plus the wildcard rules it ignores (`/*/hit$`), `Retry-After` and jittered
exponential backoff on 429/5xx. The site sends no ETag/Last-Modified, so the sitemap `lastmod` is the
hint (skip unchanged articles without fetching) and a SHA-256 of the normalized title, breadcrumb, body
and screenshot upload keys is the truth (a bumped `lastmod` with an identical body counts as unchanged).
Only article URLs are used; folder and category pages carry build-time lastmods and no content, and the
JS "related articles" list sits under the disallowed `/support/search`.

**Resumability.** A run is first planned into `crawl_items` (one row per URL), then worked one item at a
time; each item's outcome is committed as it finishes. After a crash, `--resume` requeues the item that
was in flight and continues; finished items are never fetched again. A Postgres advisory lock allows one
crawl or sync at a time across the API, scheduler and CLI.

**Freshness and removals.** The daily sync diffs the sitemap: new URLs are added, moved lastmods are
re-fetched, URLs that left the sitemap (or now return 404/410) are marked `removed` and lose every chunk
in the same transaction. Search also filters on `status = 'active'`, so a removed article cannot surface
even mid-sync. A sitemap that suddenly drops more than max(10, 20 %) of active articles is treated as broken
and the run fails without removing anything.

**Atomic re-indexing.** Chunk ids are derived from content, so re-indexing an article upserts only new
chunks (only those are embedded) and deletes stale ones in one transaction; readers never see a half-updated
article, and unchanged sections never pay for re-embedding.

**Chunking.** By H2/H3 section, packed to 300-600 tokens, each prefixed `category > folder > title` (the
prefix is weighted higher in full text and included in the embedding). Optional contextual retrieval:
Haiku writes one situating sentence per chunk with the whole article as a cached prefix.

**Screenshots.** Each in-body screenshot (`img.fr-fic[data-id]`, page chrome ignored) becomes its own
`image` chunk tied to its article, so a fact that only appears in a screenshot can be retrieved and cited
with `image_url`. Vision descriptions ask for verbatim UI labels, menu paths, field values and which step
the screenshot proves. They are regenerated only when the upload timestamp in the S3 URL changes.

**Retrieval.** One SQL query: HNSW cosine top 50 and `websearch_to_tsquery` (OR of the question's content
words) top 50, fused by reciprocal rank fusion (k = 60), multiplied by boosts (expert answers 1.5,
changelog articles 0.5, alt-text-only screenshots 0.6). RRF needs no score calibration between the two
rankers; the boosts keep the ~635 release-note articles from outranking current documentation. pgvector
0.8 iterative index scans keep filtered HNSW queries complete.

**Abstaining.** Two gates: the best cosine similarity must clear `BRAIN_MIN_SIMILARITY` (cheap, no model
call for clearly off-topic questions), and Claude must not reply `NO_ANSWER` (it is told to answer only
from the supplied documents). An answer with no citations is also treated as no answer. Every abstention
opens (or reuses) a gap ticket.

**Answering and labels.** Retrieved chunks go to Claude as `document` blocks with citations enabled, so
every quote maps back to a chunk, its article and its screenshot. The label, setting path and warnings come
from a second, small structured-output call on Haiku (structured outputs cannot be combined with citations
in one call). Deterministic rules on top: a `setting` label without a path takes it from a cited
breadcrumb-style title, and `widget_customization` always carries the update warning (the Widget Manager
docs say default widgets are maintained centrally and a customized copy is not).

**Claude usage.** One client module routes models per purpose, sets `output_config.effort`, never sends
sampling parameters, thinking budgets or forced `tool_choice` (all rejected by the 5.5 models), relies
on the SDK's retries, translates SDK exceptions most-specific first, treats `stop_reason == "refusal"` as
a no-answer, and logs every call (tokens, cache tokens, cost, latency, request id) to `llm_calls`.
Prices per million tokens: Haiku 5.5 0.10/0.50, Sonnet 5.5 2/10, Opus 5.5 4/20.

**Embeddings.** Behind an `Embedder` protocol. `voyage-4` when a key exists; otherwise local
`bge-small-en-v1.5` via fastembed (CPU, no key, no data leaves the machine); `hash` for fast deterministic
tests. Each width has its own partial HNSW index, and chunks remember which model embedded them, so
switching embedders re-embeds instead of mixing vector spaces.

**Code shape.** Typed end to end (mypy strict), small modules, one composition root
(`brain.container`) and constructor injection everywhere, so tests swap in a fake site, the hash
embedder, fake answerers or a mocked Claude transport and run offline in about a second.

## Evals

`evals/cases.jsonl` holds real cases from the crawled articles: answerable questions (gold article ids and
a short reference), screenshot-only answers, removed-article questions, out-of-domain questions, near
misses (features the product lacks) and label cases (setting vs add-on vs widget customization vs custom
development). About 20 % are `split: heldout`.

```bash
uv run python -m brain.evals.run --mock                 # offline: retrieval + abstention, mocked: true
uv run python -m brain.evals.run --models claude-sonnet-5-5,claude-opus-5-5 --reps 3   # with a key
python evals/report.py                                   # repo-level reporter (contract: docs/contracts/eval-results.md)
```

Cases tagged `removed` first mark their article removed in the target database (as a sitemap removal
would); the next sync restores it. Abstaining cases open gap tickets like any other question.

## Measured on this branch

All numbers are from real runs on 2026-10-22, local embedder, no `ANTHROPIC_API_KEY`.

| What | Result |
|---|---|
| Sample crawl (`--limit 90`) | 90 articles in 1 min 44 s: 87 added, 3 unchanged, 0 errors, 11 categories |
| Full crawl | 1540 articles in 30 min 50 s (1450 added, 90 unchanged from the sample), 0 errors; 12 categories, 634 detected changelogs, 3,734 text + 6,244 screenshot chunks |
| Incremental sync right after | 1540 unchanged, 0 article pages fetched (sitemap only), 3 s |
| Retrieval, eval questions with a gold article | dev (17): recall@1 10, recall@8 15, MRR 0.703; heldout (5): recall@1 4, recall@8 5 |
| `evals.run --mock` (31 cases) | dev 13/23, heldout 7/8 passed; all rows `mocked: true` |
| Tests | 96 passed offline in ~1.5 s (`uv run pytest`), ruff and mypy strict clean |

The mocked pass rate understates the product: the offline answerer quotes lines verbatim and the
offline classifier is a keyword heuristic, and every screenshot-only case fails without vision.
These rows measure retrieval and abstention (all out-of-domain and near-miss questions abstained).

## Known limits

- Without `ANTHROPIC_API_KEY` there is no generative answer, no model-made label and no screenshot vision;
  see Offline mode.
- No run with a real Claude key has happened yet: Claude answering, labels, vision descriptions,
  the Batches path and the contextual prefix are verified against a mocked HTTP transport only
  (request shapes, citations parsing, refusal and error handling), not against the live API.
- The Voyage embedder is untested live; the abstain gate's default (0.60) was calibrated on the local
  embedder only (in-domain best hits 0.71-0.87, off-topic 0.50-0.59).
- The Dockerfile has not been built here (no Docker on the build machine).
- The UI has no authentication.
