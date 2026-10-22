-- Knowledge Brain schema v1.
CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE crawl_runs (
    id          text PRIMARY KEY,
    kind        text NOT NULL CHECK (kind IN ('crawl', 'sync')),
    status      text NOT NULL CHECK (status IN ('running', 'done', 'failed')),
    added       integer NOT NULL DEFAULT 0,
    updated     integer NOT NULL DEFAULT 0,
    removed     integer NOT NULL DEFAULT 0,
    unchanged   integer NOT NULL DEFAULT 0,
    errors      integer NOT NULL DEFAULT 0,
    planned     boolean NOT NULL DEFAULT false,
    skipped_unchanged integer NOT NULL DEFAULT 0,  -- lastmod unchanged, so never fetched
    item_limit  integer,
    error       text,
    started_at  timestamptz NOT NULL DEFAULT now(),
    finished_at timestamptz
);

-- One row per URL a run must handle; the row status is what makes a run resumable.
CREATE TABLE crawl_items (
    run_id          text NOT NULL REFERENCES crawl_runs (id) ON DELETE CASCADE,
    url             text NOT NULL,
    article_id      text NOT NULL,
    action          text NOT NULL CHECK (action IN ('fetch', 'remove')),
    sitemap_lastmod timestamptz,
    status          text NOT NULL DEFAULT 'pending' CHECK (status IN ('pending', 'in_progress', 'done', 'failed')),
    outcome         text CHECK (outcome IN ('added', 'updated', 'unchanged', 'removed', 'error')),
    attempts        integer NOT NULL DEFAULT 0,
    error           text,
    position        integer NOT NULL,
    updated_at      timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (run_id, url)
);
CREATE INDEX crawl_items_pending ON crawl_items (run_id, status, position);

CREATE TABLE articles (
    id              text PRIMARY KEY,
    url             text NOT NULL,
    title           text NOT NULL,
    category        text NOT NULL DEFAULT '',
    folder          text NOT NULL DEFAULT '',
    body_text       text NOT NULL,
    content_hash    text NOT NULL,
    sitemap_lastmod timestamptz,
    is_changelog    boolean NOT NULL DEFAULT false,
    status          text NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'removed')),
    removed_at      timestamptz,
    last_seen_run_id text REFERENCES crawl_runs (id) ON DELETE SET NULL,
    fetched_at      timestamptz NOT NULL DEFAULT now(),
    updated_at      timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX articles_status ON articles (status);

CREATE TABLE images (
    id           text PRIMARY KEY,               -- Freshdesk attachment id (data-id)
    article_id   text NOT NULL REFERENCES articles (id) ON DELETE CASCADE,
    url          text NOT NULL,
    change_key   text NOT NULL,                  -- upload timestamp from the S3 query string
    position     integer NOT NULL,
    caption      text NOT NULL DEFAULT '',
    description  text NOT NULL DEFAULT '',
    described_by text NOT NULL DEFAULT 'alt_text' CHECK (described_by IN ('alt_text', 'claude'))
);
CREATE INDEX images_article ON images (article_id);

CREATE TABLE gap_tickets (
    id          text PRIMARY KEY,
    question    text NOT NULL,
    context     text,
    source      text NOT NULL CHECK (source IN ('chat', 'inbox', 'scope')),
    status      text NOT NULL DEFAULT 'open' CHECK (status IN ('open', 'answered')),
    bot_attempt text,
    answer      text,
    expert      text,
    created_at  timestamptz NOT NULL DEFAULT now(),
    answered_at timestamptz
);
CREATE INDEX gap_tickets_status ON gap_tickets (status, created_at);

CREATE TABLE chunks (
    id             text PRIMARY KEY,             -- content-derived, so unchanged chunks keep their embedding
    article_id     text REFERENCES articles (id) ON DELETE CASCADE,
    gap_id         text REFERENCES gap_tickets (id) ON DELETE CASCADE,
    image_id       text REFERENCES images (id) ON DELETE SET NULL,
    kind           text NOT NULL CHECK (kind IN ('text', 'image', 'expert')),
    position       integer NOT NULL DEFAULT 0,
    heading        text NOT NULL DEFAULT '',
    text           text NOT NULL,
    context_prefix text NOT NULL DEFAULT '',
    embedding      vector,
    embedding_dim  integer,
    embedding_model text,
    boost          real NOT NULL DEFAULT 1.0,
    tsv            tsvector GENERATED ALWAYS AS (
        setweight(to_tsvector('english'::regconfig, context_prefix), 'A')
        || setweight(to_tsvector('english'::regconfig, text), 'B')
    ) STORED,
    created_at     timestamptz NOT NULL DEFAULT now(),
    CHECK ((kind = 'expert') = (gap_id IS NOT NULL)),
    CHECK ((kind = 'expert') OR (article_id IS NOT NULL))
);
CREATE INDEX chunks_article ON chunks (article_id);
CREATE INDEX chunks_tsv ON chunks USING gin (tsv);
-- One partial HNSW index per supported embedding width, so switching embedders needs no schema change.
CREATE INDEX chunks_hnsw_1024 ON chunks USING hnsw ((embedding::vector(1024)) vector_cosine_ops) WHERE embedding_dim = 1024;
CREATE INDEX chunks_hnsw_384 ON chunks USING hnsw ((embedding::vector(384)) vector_cosine_ops) WHERE embedding_dim = 384;
CREATE INDEX chunks_hnsw_256 ON chunks USING hnsw ((embedding::vector(256)) vector_cosine_ops) WHERE embedding_dim = 256;

CREATE TABLE llm_calls (
    id                 bigserial PRIMARY KEY,
    purpose            text NOT NULL,
    model              text NOT NULL,
    input_tokens       integer NOT NULL DEFAULT 0,
    output_tokens      integer NOT NULL DEFAULT 0,
    cache_read_tokens  integer NOT NULL DEFAULT 0,
    cache_write_tokens integer NOT NULL DEFAULT 0,
    cost_usd           numeric(12, 6) NOT NULL DEFAULT 0,
    latency_ms         integer NOT NULL DEFAULT 0,
    stop_reason        text,
    request_id         text,
    error              text,
    created_at         timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE answer_feedback (
    id         bigserial PRIMARY KEY,
    question   text NOT NULL,
    answer     text NOT NULL,
    helpful    boolean NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now()
);
