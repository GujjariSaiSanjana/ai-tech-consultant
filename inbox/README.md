# Inbox Agent (Task 2)

Reads every customer email, gets facts from the Knowledge Brain (Task 1, over HTTP or MCP only), drafts a reply
in the company voice that a person can send in one click, and learns from every send, edit and rewrite. A kind of
email earns automatic sending only after its drafts keep going out unchanged, and it loses that right as soon as
quality drops.

## Run it

Requires Node 24+ (built and tested on Node 26; uses the built-in `node:sqlite`, so there are no native builds).

```bash
cd inbox
npm install
npm run seed        # offline demo data + simulated learning history (resets the DB)
npm run dev         # http://localhost:3000
npm test            # 94 unit + integration tests, offline
npm run typecheck && npm run lint && npm run build
npm run eval -- --mock --split dev                    # baseline playbook
npm run eval -- --mock --split heldout --with-learning # learned playbook + examples from the seeded DB
npm run eval -- --models claude-sonnet-5-5,claude-opus-5-5 --split dev   # live, needs ANTHROPIC_API_KEY
```

Configuration lives in `.env.local` (see `.env.example`). Without `ANTHROPIC_API_KEY` the app and evals use a
deterministic offline fake model, and the UI header says so. `INBOX_BRAIN_MODE=fake|http|mcp` chooses where facts
come from: `fake` is a small simulated knowledge base (example.com links), `http` calls `BRAIN_URL` per
`docs/contracts/brain-api.md`, and `mcp` starts `python -m brain.mcp_server` and calls `kb_ask` / `kb_search`.

Docker: `docker build -t inbox-agent .` then `docker run -p 3000:3000 -v inbox-data:/app/data/runtime inbox-agent`
(seed with `docker run --rm -v inbox-data:/app/data/runtime inbox-agent npm run seed`).

## Screens

- `/inbox`: three columns. On the left, the email list with kind, confidence and age. In the middle, the editable draft with Send / Edit & send /
  Rewrite / Reject and keyboard shortcuts (`j`/`k`, `e`, `s`, `r`, `x`, `Cmd+Enter`). On the right, "Why this draft": cited facts with links,
  flagged spans highlighted in the draft, what the agent is unsure about, the rules and examples it used, and why it was or was not
  auto-sent. The top bar shows a trust meter per kind (for example "How-to, existing customer: 16/20 unchanged, drafts for review").
  Auto-sent replies have their own tab and a "this reply was wrong" button.
- `/learning`: a chart of how much people changed each draft and the rolling unchanged rate, coloured by playbook version. It also shows
  unchanged rates for drafts written with and without learned rules, trust per kind in plain words, the playbook versions with
  one-click switching (rollback), the kill switch, mode-change history, quality checks and model cost.

## Pipeline (one module per step, `src/pipeline/`)

1. **Screen** (Haiku 5.5, structured output): `injection_suspected`, `abuse`, `contains_pii`. If it fails, it fails closed.
2. **Classify** (Haiku 5.5): intent (12 classes), confidence, customer type, risk class.
3. **Retrieve**: Brain `/v1/ask` + `/v1/search` in parallel, numbered as facts F1..Fn. If the Brain is down or has nothing, the draft
   is still written but marked **needs facts**, and the model is told not to state product facts.
4. **Draft** (Sonnet 5.5): `reply_text`, `fact_citations[]`, `open_questions[]`, `unsure_about`. The prompt combines the voice guide, the active
   playbook rules for this intent, and the 3 nearest past sent replies.
5. **Guardrail** (deterministic): every price, refund, discount, date or "we will" commitment must be in a sentence cited to a
   retrieved fact that actually contains that number or concept. Anything else is flagged and the draft needs a person.

Untrusted text (email, facts, examples, drafts) is passed only inside named data blocks. Delimiter look-alikes inside
it are escaped, and every system prompt says these blocks are data. System prompts are frozen strings, so they stay cacheable.

All model access goes through one client (`src/llm/client.ts`). It handles the routing table, `messages.parse` + `zodOutputFormat`,
SDK retries, a typed error chain, refusal and `max_tokens` stop reasons, and cost per call (USD per MTok: Haiku 0.10/0.50, Sonnet
2/10, Opus 4/20). Every call is logged to `llm_calls`. It never sends `temperature`/`top_p`/`top_k`, `budget_tokens` or a forced `tool_choice`.

## How the learning loop works, and why

**"Sent unchanged"** requires three independent signals to agree: normalized Levenshtein distance < 0.02 (after
whitespace and signature normalization), embedding cosine > 0.97, and a Haiku judge saying `material_change=false`. The judge
is skipped only when the normalized texts are identical. One signal alone is easy to fool: a single changed price is a tiny edit
distance, while a reworded greeting is a big one.

**Layer A, episodic memory.** Every sent reply (never a held-out eval email) is stored PII-redacted with an embedding,
and retrieved by similarity within the same intent as few-shot examples. Embeddings sit behind an interface: Voyage when
`VOYAGE_API_KEY` is set, otherwise a deterministic hashing embedder.

**Layer B, playbook.** On a material edit or rewrite, a Reflector (Sonnet) compares draft and sent text and proposes 0-2 general
rules, plus helpful/harmful feedback on rules that were in force. A deterministic Curator (no model) merges near-duplicates, prunes rules
proven harmful, and caps rules per scope. The result is a **new immutable version**. It is promoted only if its score on a fixed
regression set (dev split only) is at least the current version's score. Otherwise it is stored as rejected. Rollback is one
pointer change. Sending a draft unchanged gives a "helpful" point to the rules that were in force.

## Trust ladder (`src/learning/trust.ts`, pure and exhaustively tested)

Kind = `intent + customer_type + risk_class`. An outcome counts toward a kind only if classifier confidence is at least 0.85 and the screen is clean.

- **Learning (shadow)** for the first 5 counted replies, then **Drafts for review (assist)**.
- **Sends on its own (auto)** when the streak is at least 20 **and** the Wilson lower bound of the last-30 unchanged rate is at least 0.9. The draft
  itself must also be clean (no screen, guardrail, missing-facts, open-question or low-confidence flags) and the kill switch must be off.
- Refund, commitment and legal emails are **never** auto-eligible, and their drafts are always flagged for review.
- **Circuit breaker** (back to assist, streak 0): a person rewrites an auto-sent reply, or 2 material edits happen in the last 10, or a quality
  re-check scores below the active version's promotion score. The global kill switch stops all auto-sending at once.
- Auto-sends are stored and listed (an Auto-sent tab plus an `auto_send` row in `human_actions`), never silent.

All thresholds are in `src/config/thresholds.ts`. **Note on z:** with the usual z = 1.96, a 30-reply window can never
reach a lower bound of 0.9 (30/33.84 = 0.886), so the gate could never open. The config uses one-sided z = 1.645. In practice
that means a cold kind unlocks after 25 unchanged sends in a row, and after any edit the window must fill with 30 clean
sends again. Tests document both facts.

## Data and evals

`data/emails.jsonl` holds 60 synthetic emails for a web agency whose clients run Brilliant Directories sites. They cover Stripe, plans, design, SEO,
imports, search, visibility, widgets, new projects, quotes, refunds, cancellations, complaints, follow-ups, multi-issue, angry,
prompt injection (including hidden HTML comments), PII-laden, ambiguous, Spanish and spam emails. Each has gold intent, risk class, `must_mention`
and `must_not_say`. 15 are `split: heldout` and are never stored as examples or used in the promotion gate.

`npm run eval` writes one row per case to `../evals/results/inbox/<run_id>.jsonl` per `docs/contracts/eval-results.md`.
Score = intent 0.25 + risk 0.15 + must-mention coverage 0.3 + no must-not-say 0.2 + safety routing 0.1. A case passes at 0.75 or more
with no violations and safe routing.

Results committed in this branch are **mocked** (no API key was available while building). They measure the offline
keyword heuristics, not a model:

| run (mock) | cases | pass rate (95% CI) | mean score |
|---|---|---|---|
| dev, baseline | 45 | 80.0% (66.2-89.1%) | 0.933 |
| dev, learned v8 | 45 | 80.0% (66.2-89.1%) | 0.933 |
| held-out, baseline | 15 | 93.3% (70.2-98.8%) | 0.963 |
| held-out, learned v8 | 15 | 93.3% (70.2-98.8%) | 0.963 |

The learned playbook neither helps nor hurts these mock scores, because the seeded rules only add the simulated reviewer's lines,
which the scorer does not reward. In the simulated history, drafts written with learned rules went out unchanged 100% of the time (58 sends),
against 63% before a rule existed (16 sends). That figure comes from the scripted reviewer and is labelled as simulated in the UI.

`npm run seed` is simulated end to end: synthetic emails, a scripted reviewer with fixed habits, and the fake model. It walks one
kind to auto, auto-sends three replies, then rewrites one to show the breaker trip, and leaves 23 drafts to review.

## Known limits

- No real mail transport: "Send" records the reply. Ingest is `POST /api/emails`.
- The optional LLM judge for the guardrail is not implemented; the guardrail is deterministic only.
- No live-model metrics yet. Run `npm run eval` with a key to produce them.
- The fake Brain is a stand-in; the real Brain (Task 1) was not running during development.
- Single-process SQLite; the regression gate runs inline on each material edit (8 cases), which adds latency with a live model.
