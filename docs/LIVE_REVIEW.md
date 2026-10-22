# Live review: "make one small change"

Reviewers often ask for one small change during the review. This page says which file to
open for each likely request, so you spend the time on the change, not on searching.
After any change: `make test` (and `make up` again if you are running Docker).

Paths inside `brain/`, `inbox/` and `scope/` were checked against those branches while they
were in progress. If a path has moved, use `grep -rn "<setting name>" <folder>/src`.

## Models and cost

| Request | File | What to change |
|---|---|---|
| "Use a different model for Brain answers" | `brain/src/brain/config.py` | `answer_model` (or set `BRAIN_ANSWER_MODEL` in `.env`, no code change) |
| "Use a different model for classification / screenshots" | `brain/src/brain/config.py` | `classify_model`, `vision_model` (env `BRAIN_CLASSIFY_MODEL`, `BRAIN_VISION_MODEL`) |
| "Change the USD to INR rate" | `.env` | `USD_TO_INR` (default 94.59 lives in `evals/report.py`) |
| "Change model prices" | the task's pricing table (`grep -rn "per_mtok\|PRICES" brain/src inbox/src scope/src`) | the price constant |

## Knowledge Brain

| Request | File | What to change |
|---|---|---|
| "Be stricter about saying I don't know" | `brain/src/brain/config.py` | `min_similarity` (env `BRAIN_MIN_SIMILARITY`): higher = abstains more |
| "Trust expert answers more / less" | `brain/src/brain/config.py` | `expert_boost` (default 1.5) |
| "Prefer changelog articles more" | `brain/src/brain/config.py` | `changelog_boost` (default 0.5) |
| "Run the nightly sync at another hour" | `brain/src/brain/config.py` | `sync_hour` (env `BRAIN_SYNC_HOUR`, default 3) |
| "Crawl slower / politer" | `brain/src/brain/config.py` | `crawl_interval_s` (default 1.0 s) |
| "Allow bigger deletions in one sync" | `brain/src/brain/config.py` | `max_removed_fraction` (default 0.2) |
| "Change retrieved chunk count" | `brain/src/brain/config.py` or the `/v1/ask` request | `top_k` (contract default 8) |
| "Add a DB column" | `brain/migrations/` | new numbered SQL file, then `make migrate` |

## Inbox Agent

All trust and learning numbers are in one file: `inbox/src/config/thresholds.ts`.

| Request | Constant |
|---|---|
| "Make auto-send harder / easier" | `autoMinStreak` (20), `autoMinWilsonLowerBound` (0.9), `rollingWindow` (30) |
| "Make 'unchanged' stricter" | `unchangedMaxEditDistance` (0.02), `unchangedMinCosine` (0.97) |
| "Trip the circuit breaker sooner" | `breakerMaterialEdits` (2), `breakerWindow` (10) |
| "Longer shadow mode" | `shadowOutcomes` (5) |
| "More few-shot examples" | `fewShotK` (3) |
| "Brain is slow, wait longer" | `brainTimeoutMs` (8000) |
| "Never auto-send another risk class" | `NEVER_AUTO_RISKS` in `inbox/src/domain/kind.ts` |
| "Change what counts as the same kind of email" | `kindKey` in `inbox/src/domain/kind.ts` |

## Meeting to Scope

| Request | Where |
|---|---|
| "Add a gold example project" | `scope/data/gold/` (one JSON file per project, like `trailhead.json`) |
| "Change an agent's prompt or model" | `scope/src/scope/` agent modules (`grep -rn "opus\|sonnet" scope/src`) |
| "Change the hourly rate or estimate rounding" | `grep -rn "rate\|hours" scope/src/scope` |

## Platform (this branch)

| Request | File |
|---|---|
| "Change a port" | `docker-compose.yml` (`ports:`) and the matching `BRAIN_URL` |
| "Add a make target" | `Makefile` (add `## description` so it shows in `make help`) |
| "Add a CI step" | `.github/workflows/ci.yml` (call a make target, keep CI = local) |
| "Add a column to the eval report" | `evals/report.py` (`Summary`, `summarize`, `render`) and a test in `evals/test_report.py` |
| "Use a 90% interval instead of 95%" | `evals/report.py`: `wilson_interval(..., z=1.645)` |
| "Change the Postgres / pgvector version" | `docker-compose.yml` and `.github/workflows/ci.yml` (both pin `pgvector/pgvector:0.8.7-pg17`) |
