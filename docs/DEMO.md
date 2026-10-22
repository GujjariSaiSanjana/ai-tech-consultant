# Demo script (10 minutes)

Goal: show one real flow across all three tasks, one failure on purpose, and what we dropped.
Before you start: `make up` (or `make dev`), `make seed`, at least `make crawl LIMIT=100`,
and `make eval` run once so `evals/results/summary.md` exists. Have three tabs open:
inbox (:3000), scope (:8100), brain health (:8000/health).

| Time | What you show | What you say |
|---|---|---|
| 0:00-0:30 | Your face, no slides | "Software got about 90% cheaper. The slow part is product knowledge: answering clients and scoping work. I built one brain and two agents for that." |
| 0:30-1:30 | `docs/DESIGN.md` diagram | "Three tasks. Inbox and Scope only talk to the Brain over HTTP. Each one can be replaced on its own." |
| 1:30-3:30 | **Inbox:** open a seeded client email asking for a feature | Show the draft, the Brain citation, and the label. Point at the "where to click" path. "This is a Setting, so the reply is 2 lines, not a quote." |
| 3:30-4:30 | **Inbox:** edit the draft and send | Show the edit being recorded and the kind's trust state. "Auto-send is earned per kind: 20 unchanged sends in a row and a Wilson bound of 0.9. Refunds never." |
| 4:30-6:30 | **Scope:** paste the same client's meeting transcript | Show requirements, the feasibility column (Brain says Setting vs Custom dev), the estimate. "We do not charge for what the product already does." |
| 6:30-7:30 | **Scope:** run a second transcript | Show the scope diff as change requests with cost impact. |
| 7:30-8:30 | **Failure on purpose:** ask the Brain something the help center does not cover | It answers `no_answer` and opens an expert gap. Answer it as the expert; ask again; now it answers with the expert citation. "I prefer 'I don't know' to a confident wrong answer." |
| 8:30-9:30 | `evals/results/summary.md` | One table. Read one row aloud with its interval. If the run was mocked, say so: the banner is there for a reason. |
| 9:30-10:00 | `docs/DESIGN.md` section 3 | "What I dropped and why: live Gmail, audio, CRM push, PDF styling, multi-tenant auth, fine-tuning. Next: live inbox sync." |

## If something goes wrong live

- Brain not answering: `curl localhost:8000/health`. If `chunks` is 0, run `make crawl LIMIT=100`.
- No API key: everything still runs on mocks. Say "this is a mock run" out loud.
- Docker problems: stop and switch to `make dev`.
