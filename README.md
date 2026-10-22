# AI Tech Consultant

Writing software got about 90% cheaper. The slow part of a product consultancy is now
everything around the code: knowing what the product already does, answering the inbox,
and turning a client meeting into a scope of work. This project automates those three jobs
for one product, Brilliant Directories (a website builder for membership directories), and measures how well it does.

## The three tasks

| Folder | Task | What it does | Stack | Port |
|---|---|---|---|---|
| `brain/` | 1. Knowledge Brain | Crawls the help center, answers questions with citations, labels each answer Setting / Add-on / Widget customization / Custom dev | Python, FastAPI, Postgres + pgvector | 8000 |
| `inbox/` | 2. Inbox Agent | Drafts email replies, learns from what you actually send, earns auto-send per email kind | TypeScript, Next.js, SQLite | 3000 |
| `scope/` | 3. Meeting to Scope | Agent team turns a transcript into requirements, estimate and proposal | Python, FastAPI + HTMX, SQLite | 8100 |
| `evals/` | Shared reporter | Turns every eval run into one Markdown table | Python stdlib | – |

**How they connect.** Inbox and Scope never import Brain code. They call its HTTP API
(`BRAIN_URL`, contract in `docs/contracts/brain-api.md`). Brain also ships an MCP server
for Claude clients. Each task writes eval rows in one shared format
(`docs/contracts/eval-results.md`).

## Run it

```bash
make up        # whole project in Docker: db, brain, scheduler, inbox, scope
make dev       # same, without Docker (needs local Postgres 17 with pgvector)
make help      # every target, one line each
```

First time without Docker: `make setup`, then `make crawl LIMIT=100` to load some articles.
Open <http://localhost:3000> (inbox), <http://localhost:8100> (scope),
<http://localhost:8000/health> (brain).

Quality: `make test`, `make lint`, `make typecheck` (all offline, no keys).
Evals: `make eval` runs all three runners, then writes `evals/results/summary.md`.

## Keys

The first `make` copies `.env.example` to `.env`. Fill in:

- `ANTHROPIC_API_KEY`: every model call. Without it, eval rows are marked `mocked: true`
  and the report prints a "MOCK RUN" banner.
- `VOYAGE_API_KEY`: embeddings. Without it, Brain falls back to a local embedder.

## Docs

- `docs/DESIGN.md`: the problem, what we built and dropped, models, what breaks
- `docs/EVALS.md`: method and results
- `docs/DEMO.md`: 10-minute demo script
- `docs/LIVE_REVIEW.md`: where to make common small changes
- `docs/AI_LOG.md`: how AI was used to build this
- `docs/contracts/`: the API and eval formats the three tasks agree on
