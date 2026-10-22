/** One migration, applied idempotently at startup. JSON columns hold Zod-validated arrays/objects. */
export const SCHEMA_SQL = `
CREATE TABLE IF NOT EXISTS emails (
  id TEXT PRIMARY KEY,
  from_name TEXT NOT NULL,
  from_email TEXT NOT NULL,
  subject TEXT NOT NULL,
  body TEXT NOT NULL,
  received_at TEXT NOT NULL,
  split TEXT,
  tags_json TEXT NOT NULL DEFAULT '[]',
  gold_json TEXT,
  simulated INTEGER NOT NULL DEFAULT 0,
  status TEXT NOT NULL DEFAULT 'new'
) STRICT;

CREATE TABLE IF NOT EXISTS classifications (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  email_id TEXT NOT NULL REFERENCES emails(id),
  intent TEXT NOT NULL,
  confidence REAL NOT NULL,
  customer_type TEXT NOT NULL,
  risk_class TEXT NOT NULL,
  injection_suspected INTEGER NOT NULL,
  abuse INTEGER NOT NULL,
  contains_pii INTEGER NOT NULL,
  screen_reasons_json TEXT NOT NULL,
  kind_key TEXT NOT NULL,
  counted INTEGER NOT NULL,
  created_at TEXT NOT NULL
) STRICT;

CREATE TABLE IF NOT EXISTS drafts (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  email_id TEXT NOT NULL REFERENCES emails(id),
  classification_id INTEGER NOT NULL REFERENCES classifications(id),
  playbook_version_id INTEGER NOT NULL,
  reply_text TEXT NOT NULL,
  fact_citations_json TEXT NOT NULL,
  open_questions_json TEXT NOT NULL,
  unsure_about TEXT NOT NULL,
  facts_json TEXT NOT NULL,
  retrieval_status TEXT NOT NULL,
  retrieval_error TEXT,
  gap_ticket_id TEXT,
  guardrail_flags_json TEXT NOT NULL,
  review_reasons_json TEXT NOT NULL,
  needs_review INTEGER NOT NULL,
  applied_rule_ids_json TEXT NOT NULL,
  example_ids_json TEXT NOT NULL,
  trust_mode TEXT NOT NULL,
  send_decision TEXT NOT NULL,
  status TEXT NOT NULL,
  sent_text TEXT,
  model TEXT,
  error TEXT,
  created_at TEXT NOT NULL,
  sent_at TEXT
) STRICT;
CREATE INDEX IF NOT EXISTS drafts_email ON drafts(email_id);

CREATE TABLE IF NOT EXISTS human_actions (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  draft_id INTEGER NOT NULL REFERENCES drafts(id),
  email_id TEXT NOT NULL REFERENCES emails(id),
  action TEXT NOT NULL,
  sent_text TEXT,
  edit_distance REAL,
  cosine REAL,
  judge_material_change INTEGER,
  outcome TEXT,
  kind_key TEXT NOT NULL,
  counted INTEGER NOT NULL,
  playbook_version_id INTEGER NOT NULL,
  simulated INTEGER NOT NULL DEFAULT 0,
  created_at TEXT NOT NULL
) STRICT;
CREATE INDEX IF NOT EXISTS human_actions_created ON human_actions(created_at);

CREATE TABLE IF NOT EXISTS examples (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  human_action_id INTEGER NOT NULL REFERENCES human_actions(id),
  email_id TEXT NOT NULL REFERENCES emails(id),
  intent TEXT NOT NULL,
  kind_key TEXT NOT NULL,
  email_text TEXT NOT NULL,
  reply_text TEXT NOT NULL,
  embedder TEXT NOT NULL,
  embedding_json TEXT NOT NULL,
  created_at TEXT NOT NULL
) STRICT;
CREATE INDEX IF NOT EXISTS examples_intent ON examples(intent, embedder);

CREATE TABLE IF NOT EXISTS playbook_rules (
  id TEXT PRIMARY KEY,
  text TEXT NOT NULL,
  scope TEXT NOT NULL,
  helpful INTEGER NOT NULL DEFAULT 0,
  harmful INTEGER NOT NULL DEFAULT 0,
  evidence_ids_json TEXT NOT NULL,
  created_at TEXT NOT NULL
) STRICT;

CREATE TABLE IF NOT EXISTS playbook_versions (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  parent_id INTEGER,
  rule_ids_json TEXT NOT NULL,
  status TEXT NOT NULL,
  note TEXT NOT NULL,
  eval_score REAL,
  baseline_score REAL,
  created_at TEXT NOT NULL
) STRICT;

CREATE TABLE IF NOT EXISTS trust_state (
  kind_key TEXT PRIMARY KEY,
  risk_class TEXT NOT NULL,
  mode TEXT NOT NULL,
  streak INTEGER NOT NULL,
  outcomes_json TEXT NOT NULL,
  counted_total INTEGER NOT NULL,
  last_reason TEXT NOT NULL,
  updated_at TEXT NOT NULL
) STRICT;

CREATE TABLE IF NOT EXISTS trust_events (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  kind_key TEXT NOT NULL,
  from_mode TEXT NOT NULL,
  to_mode TEXT NOT NULL,
  reason TEXT NOT NULL,
  created_at TEXT NOT NULL
) STRICT;

CREATE TABLE IF NOT EXISTS eval_runs (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  run_id TEXT NOT NULL,
  purpose TEXT NOT NULL,
  playbook_version_id INTEGER,
  split TEXT NOT NULL,
  cases INTEGER NOT NULL,
  mean_score REAL NOT NULL,
  pass_rate REAL NOT NULL,
  mocked INTEGER NOT NULL,
  created_at TEXT NOT NULL
) STRICT;

CREATE TABLE IF NOT EXISTS llm_calls (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  purpose TEXT NOT NULL,
  model TEXT NOT NULL,
  input_tokens INTEGER NOT NULL,
  output_tokens INTEGER NOT NULL,
  cache_read_tokens INTEGER NOT NULL,
  cache_write_tokens INTEGER NOT NULL,
  cost_usd REAL NOT NULL,
  latency_ms INTEGER NOT NULL,
  stop_reason TEXT,
  error TEXT,
  mocked INTEGER NOT NULL,
  created_at TEXT NOT NULL
) STRICT;

CREATE TABLE IF NOT EXISTS settings (
  key TEXT PRIMARY KEY,
  value TEXT NOT NULL
) STRICT;
`;
