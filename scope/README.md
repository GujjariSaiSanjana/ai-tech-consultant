# Meeting to Scope of Work (Task 3)

A team of agents turns long, messy client meeting transcripts into three things:

1. a **scope of work** (requirements with quotes, how each one is delivered),
2. a **task list with hour estimates** (three-point, priced from a rate card),
3. a **client-ready proposal**, which reaches the client only after a person approves it.

The agents ask the questions a senior consultant would still ask. Before anyone estimates custom
code, they check the Knowledge Brain (Task 1) over HTTP to see whether the request is already an
existing product setting. When a follow-up meeting changes things, the scope is **updated, not
rebuilt**: you see what changed, what is new, and what falls outside the agreed scope and should be
quoted as a change request.

## Quick start (offline, no keys needed)

```bash
cd scope
uv sync
uv run pytest                                   # 188 tests, about 2 s

# Meetings 1 and 2 build scope v1
uv run python -m scope.cli run --client "Trailhead Media" \
  --transcript data/transcripts/trailhead/meeting-01-kickoff.txt \
  --transcript data/transcripts/trailhead/meeting-02-deep-dive.txt
uv run python -m scope.cli approve --project 1 --actor "Sam Patel"
# Meeting 3 is a follow-up: it is reconciled against the approved v1 and produces v2 plus a change log
uv run python -m scope.cli run --project 1 --transcript data/transcripts/trailhead/meeting-03-follow-up.txt

uv run python -m scope.web                      # http://127.0.0.1:8100
uv run python -m scope.evals.run --mock         # writes ../evals/results/scope/<run_id>.jsonl
```

Without any LLM key (`ANTHROPIC_API_KEY`, `GEMINI_API_KEY` or `NVIDIA_API_KEY`), the app uses a **rule-based fake** in place of Claude and a small
fictional fixture in place of the Brain. A banner in the UI says so. The fake shows the workflow
working; it says nothing about model quality.

## With Claude and the Knowledge Brain

| Variable | Default | Meaning |
|---|---|---|
| `ANTHROPIC_API_KEY` | unset | Use Claude (`SCOPE_LLM=auto`). |
| `GEMINI_API_KEY`, `NVIDIA_API_KEY` | unset | Fallbacks behind Claude; see [LLM fallback](#llm-fallback). |
| `BRAIN_URL`, `BRAIN_API_KEY` | `http://localhost:8000`, `dev-brain-key` | Knowledge Brain API (contract: `docs/contracts/brain-api.md`). |
| `SCOPE_DATABASE_URL` | `sqlite+aiosqlite:///./data/runtime/scope.db` | SQLite file. |
| `SCOPE_RUN_BUDGET_USD` | `5.0` | Spending cap per pipeline run; calls stop once it is reached. |
| `SCOPE_LLM`, `SCOPE_BRAIN` | `auto` | Force `fake`/`anthropic` and `fake`/`http`. |
| `SCOPE_EXPORT_DIR` | `./data/runtime/exports` | Where "sent" proposals are written. |

Docker: `docker build -t scope . && docker run -p 8100:8100 -e ANTHROPIC_API_KEY -e BRAIN_URL scope`.

## The agent team

Orchestration is plain Python in `orchestrator.py`, not an LLM router, so every step can be tested
and explained. Each agent is one module (`agents/`) with one job, one versioned prompt
(`prompts/<agent>.v1.md`) and one Pydantic output schema.

| # | Agent | Model | Its one job |
|---|---|---|---|
| 1 | Requirements Extractor | Sonnet 5.5 | List what the client needs, each item backed by verbatim quotes (meeting, timestamp, speaker). |
| 2 | Senior Consultant Questioner | Opus 5.5 | Check the meetings against an 11-point discovery checklist; return open questions and the assumptions to use until they are answered. Runs in parallel with 1. |
| 3 | Feasibility Checker | Sonnet 5.5 + Brain | Decide setting, add-on, widget customization, custom code or unknown for each requirement, with citations. |
| 4 | Scoper + Estimator | Opus 5.5 | Group requirements into scope items and tasks with optimistic, likely and pessimistic hours. |
| 5 | Reviewer | Opus 5.5 | Look for missed requirements, inconsistent hours and missing assumptions, and send notes back to 4 (at most 2 rounds). |
| 6 | Proposal Writer | Sonnet 5.5 | Write the client-facing sections, after the scope is approved. |
| 7 | Reconciler | Opus 5.5 | Follow-up meetings only: mark each statement as `matches: REQ-...` or `new`, and as unchanged, modified, removed or added. |

**Why seven agents.** Each agent covers a different judgement and needs a different context and
model, so its output can be checked on its own:
- Extraction is about recall and runs on a cheaper model.
- Questioning and estimating are senior judgement and run on Opus.
- Feasibility is the only agent that needs the Brain.
- Writing is about tone.
- Reconciling only happens on follow-up meetings.

Merging any two would blur what an eval measures, for example extraction recall against estimate
quality. Splitting further, say one agent per checklist item, would add calls and coordination
without adding any new judgement. Everything deterministic is done in code, not by an agent.

```
transcripts ─► [Extractor ‖ Questioner] ─► Feasibility (Brain) ─► Scoper ⇄ Reviewer (≤2) ─► scope vN (draft)
                                                                                   │ human approves
follow-up  ─► [Reconciler ‖ Questioner] ─► Feasibility (changed only) ─► re-scope touched items ─► vN+1 + ChangeLog
                                                                                   │ human approves
                                                          Proposal Writer ─► draft ─► in_review ─► approved ─► sent (file export)
```

## What code does, not the model

- **IDs**: `REQ-<sha256 of normalized statement>[:8]`, and similarly for items, tasks, questions and change requests. The models never mint IDs.
- **Grounding**: every quote is checked against the transcript (`domain/transcript.verify_evidence`). A requirement with no quote that can be found is dropped and listed under "Mentioned but not included".
- **PERT**: expected = (O + 4M + P) / 6 and sigma = (P − O) / 6. The total sigma is the root of the summed variances, giving a 90% range. A bad triplet is repaired, flagged, and sent back to the Reviewer.
- **Estimate rules** (`domain/checks.py`), sent to the Reviewer as notes:
  - every requirement is covered;
  - a settings item takes at most `SCOPE_SETTING_MAX_HOURS` (default 6);
  - a settings item is always cheaper than any custom item;
  - an item's kind matches its feasibility;
  - out-of-scope requests sit in their own items.
- **Diffing** (`domain/diff.py`): turns Reconciler findings into the next version and a ChangeLog `{added, modified[{id,before,after}], removed, out_of_scope, change_requests[{requirement_ids, delta_hours, price, rationale}]}`. Hour deltas and change-request prices come from the two estimates. Only touched items are re-estimated; the others are carried over as they are.
- **Feasibility guardrails**:
  - Every requirement goes to `POST /v1/feasibility` in code first.
  - If the Brain is down, the requirement gets kind `unknown` and the flag `brain_unavailable`. It is never guessed.
  - If the model calls something a setting but cites no article the Brain returned, it is downgraded to `unknown`.
- **Prices in the proposal**: the price and change-request sections are rendered from the estimate, so the model never writes a number the client sees.
- **Prompt injection**:
  - Transcripts are framed as data.
  - Lines that read like instructions to a model are flagged before any prompt is built.
  - A requirement supported only by flagged lines is dropped.

## Long transcripts and prompt caching

A 90-minute meeting is about 20K tokens. It is sent whole and never summarized first. Every request
is built in `llm/request.py` with this layout:

```
tools   : identical Brain tool list for every agent, sorted by name
system  : [0] shared preamble + all transcripts   ← cache_control breakpoint
          [1] this agent's role prompt            (after the breakpoint)
messages: [user] the agent's JSON payload
```

This means agents on the same model reuse the cached transcript prefix: Scoper, Reviewer
(including both review rounds) and Questioner on Opus; Extractor, Feasibility and Proposal Writer on
Sonnet. Nothing volatile goes before the breakpoint. A test checks the placement and that the
prefix is byte-identical across agents.

Two limits are worth knowing:
- Caches are per model, so Sonnet and Opus each write their own entry once per run.
- Extractor and Questioner start together, so the Questioner cannot read a cache the Extractor is still writing.

Above about 150K estimated tokens, extraction and questioning switch to segment-then-merge:
consecutive whole-utterance segments of about 100K tokens each, with results merged by ID. The
later agents then work from the verified quotes.

## API usage (Anthropic SDK 2.x)

- Structured outputs use `client.messages.parse(output_format=Model)`.
- Depth is set with `output_config.effort`. There is no `temperature`, `top_p` or `top_k`, no thinking budget, and no forced `tool_choice`. A test checks this.
- The SDK retries failed calls (`SCOPE_LLM_MAX_RETRIES`). Errors are then mapped to a typed chain: `LLMConfigError`, `LLMUnavailableError`, `LLMRefusalError` (a `refusal` stop reason), `LLMTruncatedError` and `LLMSchemaError`.
- Every call, including failed ones, is written to `llm_calls` with its tokens, cache reads and writes, cost, latency, stop reason and request id.
- Cost is computed per MTok:

  | Model | Input | Output | Cache read |
  |---|---|---|---|
  | Haiku 5.5 | 0.10 | 0.50 | 0.01 |
  | Sonnet 5.5 | 2 | 10 | 0.20 |
  | Opus 5.5 | 4 | 20 | 0.20 |

  Cache writes cost 1.25 times the input price.

## LLM fallback

Claude stays primary. Google Gemini and NVIDIA (build.nvidia.com / NIM) are fallbacks behind it,
both called through their OpenAI-compatible Chat Completions endpoint with `httpx`
(`llm/openai_compat_backend.py`, composed by `llm/fallback_backend.py`).

| Variable | Default |
|---|---|
| `GEMINI_API_KEY`, `GEMINI_MODEL`, `GEMINI_BASE_URL` | unset, `gemini-2.5-flash`, `https://generativelanguage.googleapis.com/v1beta/openai/` |
| `NVIDIA_API_KEY`, `NVIDIA_MODEL`, `NVIDIA_BASE_URL` | unset, `openai/gpt-oss-20b`, `https://integrate.api.nvidia.com/v1` |
| `LLM_FALLBACK_ORDER` | `gemini,nvidia` (empty disables fallback) |
| `SCOPE_GEMINI_PRICE`, `SCOPE_NVIDIA_PRICE` | 0.30/2.50 per MTok for `gemini-2.5-flash`; NVIDIA 0 (free tier) |
| `SCOPE_FALLBACK_TIMEOUT_S` | `120` |

- **When.** Only when Claude raises `LLMUnavailableError` (rate limit, 5xx, network, after
  retries), or when there is no `ANTHROPIC_API_KEY` but a fallback key is set. Refusals, schema,
  truncation and config errors never fall back. Providers are tried in order, keyless ones are
  skipped, and if all are unavailable Claude's original error is re-raised.
- **Output.** `response_format` is a strict `json_schema` from the Pydantic model, validated with
  Pydantic (`LLMSchemaError` on failure). If a provider answers 400, the call is retried once as
  `json_object` with the schema in the system prompt, and that provider stays on `json_object`.
- **Tools.** The Brain tools are sent in OpenAI `tools` format and run in the same bounded loop
  (`SCOPE_MAX_TOOL_ROUNDS`), recorded as `ToolCall`s.
- **Audit and cost.** The `llm_calls` row carries the provider model (for example
  `gemini-2.5-flash`), so the log shows which provider answered. Usage comes from
  `prompt_tokens`/`completion_tokens`, and the run budget applies as usual. A fallback model
  with no price fails at startup; set `SCOPE_GEMINI_PRICE='{"input": 1.25, "output": 10}'`.
- No sampling parameters are sent; `max_tokens` is the route's cap. With fallbacks there is no
  prompt caching, and output quality on Gemini and Llama has not been evaluated.

## Human approval gate and audit

- A scope version starts as a draft. **Generate proposal is disabled until a person approves it**, and the service enforces the same rule.
- A follow-up meeting cannot be processed while the latest scope is still a draft.
- The proposal moves through `draft → in_review → approved → sent`.
- Each section can be approved, edited or regenerated:
  - every section must be approved before the proposal can be;
  - editing an approved proposal sends it back to review;
  - a sent proposal cannot change.
- "Send" exports Markdown and HTML to `SCOPE_EXPORT_DIR`. No email is sent.
- Every action is written to the audit log with actor, version, section, a unified diff and a timestamp. The audit log is shown on the Proposal tab.

## UI

The project page has six tabs, written for a non-technical reader:
- **Transcripts**: add or upload meetings, run the agents, re-run from any stage.
- **Questions**: open questions and assumptions.
- **Scope**: requirements, how each is delivered, quotes, reviewer notes, and the approve button.
- **Hours**: tasks with O/M/P, expected hours and cost.
- **Proposal**: section-by-section review and the audit log.
- **Changes**: a split diff from v1 to v2. Green is added, amber modified, red removed, and grey outside the agreed scope with its change-request quote.

## Evals

```bash
uv run python -m scope.evals.run --mock                    # offline
uv run python -m scope.evals.run --split heldout           # Claude, per-agent routing
uv run python -m scope.evals.run --models claude-sonnet-5-5,claude-haiku-5-5
```

`evals/cases.jsonl` has 24 cases, 6 of them `split: heldout`, including the whole second client. They cover:
- requirement F1 against gold (one-to-one keyword alignment; traps count as false positives);
- each trap type;
- feasibility kind accuracy;
- question coverage against the checklist;
- estimate sanity;
- schema and evidence validity;
- follow-up diff set-F1 by ID, with change-request quoting and unchanged items preserved;
- injection, empty, garbled and segmented (long) transcripts;
- the Brain being down.

Rows follow `docs/contracts/eval-results.md`. Per-case detail goes to the gitignored
`evals/results/raw/scope/`. When several cases score the same pipeline run, the cost is reported
once.

`--models` routes every agent to that model, which gives a whole-pipeline comparison.

**Results so far: mock only.** No API key was available while building this, so the committed run is
mocked (`mocked: true`). It scores the rule-based fake, not Claude: 8/24 pass. The structural cases
pass: estimate sanity, schema, injection guard, empty transcript and Brain down. The judgement cases
fail, as expected for keyword rules: extraction F1 about 0.3, and diff F1 0. Those numbers are a
floor for the plumbing, not a quality claim. Claude numbers need a real run.

## Code map

```
src/scope/
  domain/        pure: models, ids, pert, diff, checks, states, transcript, render
  agents/        one module per agent (+ base helpers)
  prompts/       versioned prompt files + discovery checklist
  llm/           request building, Anthropic + OpenAI-compatible fallback backends, session, errors
  brain/         HTTP client per contract, tool box shared by all agents
  fakes/         offline LLM responders and Brain fixture
  orchestrator.py, service.py, db/, web/, cli.py, evals/
```

Quality gates: `uv run pytest`, `uv run mypy` (strict, src and tests) and `uv run ruff check .`.

## Known limits

- No real-model eval numbers yet (see Evals). The prompts have not been tuned against real outputs.
- Gold matching uses keywords. A correct requirement worded unusually can be missed.
- The Brain fixture is fictional. Feasibility labels against the real Brain depend on Task 1.
- The budget cap is checked before each call, so calls already running in parallel can go over it by the cost of those calls.
- Docker was not available on the build machine, so the Dockerfile has not been built yet.
- One process, SQLite and in-process background tasks. Fine for a team tool; not built for many concurrent users.
