"""All SQL lives here. Methods take plain values and return plain values or domain types."""

from __future__ import annotations

import secrets
from collections.abc import AsyncIterator, Sequence
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from psycopg import AsyncConnection, sql
from psycopg.rows import DictRow

from brain.db import Pool, to_vector_literal
from brain.domain import ChunkDraft, ParsedArticle, RunCounts, SearchHit, SitemapEntry
from brain.llm.client import CallRecord

RRF_K = 60
# Chosen on the dev split of evals/cases.jsonl (local embedder): normalising full-text rank by
# document length (bitmask 1) lifted MRR 0.652 -> 0.703; full-text weight 0.3-1.0 was within
# noise, 0.5 keeps exact-identifier matches (variables, API names) influential.
VECTOR_WEIGHT = 1.0
FTS_WEIGHT = 0.5
FTS_NORMALIZATION = 1  # ts_rank_cd: divide by 1 + log(document length)
RUN_LOCK_KEY = 727002
MAX_ITEM_ATTEMPTS = 3


def new_id(prefix: str) -> str:
    return f"{prefix}_{secrets.token_hex(8)}"


@dataclass(frozen=True, slots=True)
class StoredImage:
    change_key: str
    description: str
    described_by: str


@dataclass(frozen=True, slots=True)
class NewChunk:
    id: str
    draft: ChunkDraft
    embedding: list[float]


@dataclass(frozen=True, slots=True)
class WorkItem:
    run_id: str
    url: str
    article_id: str
    action: str
    sitemap_lastmod: datetime | None


@dataclass(frozen=True, slots=True)
class PendingImage:
    id: str
    url: str
    change_key: str
    position: int
    caption: str
    article_id: str
    title: str
    heading: str
    context_prefix: str
    chunk_position: int


@dataclass(frozen=True, slots=True)
class ImageWrite:
    id: str
    url: str
    change_key: str
    position: int
    caption: str
    description: str
    described_by: str


class Repository:
    def __init__(self, pool: Pool) -> None:
        self.pool = pool

    # ---- articles -------------------------------------------------------------------------------

    async def known_states(self) -> dict[str, tuple[str, datetime | None]]:
        async with self.pool.connection() as conn:
            cur = await conn.execute("SELECT id, status, sitemap_lastmod FROM articles")
            return {r["id"]: (r["status"], r["sitemap_lastmod"]) for r in await cur.fetchall()}

    async def article_state(self, article_id: str) -> tuple[str, str] | None:
        """(status, content_hash) or None."""
        async with self.pool.connection() as conn:
            cur = await conn.execute("SELECT status, content_hash FROM articles WHERE id = %s", (article_id,))
            row = await cur.fetchone()
            return (row["status"], row["content_hash"]) if row else None

    async def touch_article(self, article_id: str, lastmod: datetime | None, run_id: str | None) -> None:
        async with self.pool.connection() as conn:
            await conn.execute(
                "UPDATE articles SET sitemap_lastmod = coalesce(%s, sitemap_lastmod), "
                "last_seen_run_id = coalesce(%s, last_seen_run_id) WHERE id = %s",
                (lastmod, run_id, article_id),
            )

    async def touch_many(self, entries: Sequence[SitemapEntry], run_id: str) -> None:
        if not entries:
            return
        async with self.pool.connection() as conn:
            await conn.execute(
                "UPDATE articles SET last_seen_run_id = %s WHERE id = ANY(%s)",
                (run_id, [e.article_id for e in entries]),
            )

    async def has_foreign_embeddings(self, article_id: str, embedding_model: str) -> bool:
        """True after an embedder switch: the article must be re-embedded even though it did not change."""
        async with self.pool.connection() as conn:
            cur = await conn.execute(
                "SELECT EXISTS (SELECT 1 FROM chunks WHERE article_id = %s AND embedding_model IS DISTINCT FROM %s) "
                "AS stale",
                (article_id, embedding_model),
            )
            row = await cur.fetchone()
            return bool(row and row["stale"])

    async def foreign_expert_chunks(self, embedding_model: str) -> list[dict[str, Any]]:
        async with self.pool.connection() as conn:
            cur = await conn.execute(
                "SELECT id, text, context_prefix FROM chunks WHERE kind = 'expert' "
                "AND embedding_model IS DISTINCT FROM %s",
                (embedding_model,),
            )
            return list(await cur.fetchall())

    async def set_embedding(self, chunk_id: str, embedding: list[float], embedding_model: str) -> None:
        async with self.pool.connection() as conn:
            await conn.execute(
                "UPDATE chunks SET embedding = %s::vector, embedding_dim = %s, embedding_model = %s WHERE id = %s",
                (to_vector_literal(embedding), len(embedding), embedding_model, chunk_id),
            )

    async def stored_images(self, article_id: str) -> dict[str, StoredImage]:
        async with self.pool.connection() as conn:
            cur = await conn.execute(
                "SELECT id, change_key, description, described_by FROM images WHERE article_id = %s",
                (article_id,),
            )
            return {
                r["id"]: StoredImage(r["change_key"], r["description"], r["described_by"]) for r in await cur.fetchall()
            }

    async def chunk_ids(self, article_id: str, embedding_model: str) -> set[str]:
        """Chunks that can be kept as they are: same content id and embedded by the current model."""
        async with self.pool.connection() as conn:
            cur = await conn.execute(
                "SELECT id FROM chunks WHERE article_id = %s AND embedding_model = %s",
                (article_id, embedding_model),
            )
            return {r["id"] for r in await cur.fetchall()}

    async def write_article(
        self,
        article: ParsedArticle,
        *,
        content_hash: str,
        lastmod: datetime | None,
        is_changelog: bool,
        run_id: str | None,
        images: Sequence[ImageWrite],
        keep_chunk_ids: Sequence[str],
        new_chunks: Sequence[NewChunk],
        embedding_model: str,
    ) -> None:
        """Article, images and chunks change together: upsert new chunks, then drop stale ones."""
        async with self.pool.connection() as conn, conn.transaction():
            await conn.execute(
                """
                INSERT INTO articles (id, url, title, category, folder, body_text, content_hash, sitemap_lastmod,
                                      is_changelog, status, removed_at, last_seen_run_id, fetched_at, updated_at)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, 'active', NULL, %s, now(), now())
                ON CONFLICT (id) DO UPDATE SET
                    url = EXCLUDED.url, title = EXCLUDED.title, category = EXCLUDED.category,
                    folder = EXCLUDED.folder, body_text = EXCLUDED.body_text,
                    content_hash = EXCLUDED.content_hash,
                    sitemap_lastmod = coalesce(EXCLUDED.sitemap_lastmod, articles.sitemap_lastmod),
                    is_changelog = EXCLUDED.is_changelog, status = 'active', removed_at = NULL,
                    last_seen_run_id = EXCLUDED.last_seen_run_id, fetched_at = now(), updated_at = now()
                """,
                (
                    article.article_id,
                    article.url,
                    article.title,
                    article.category,
                    article.folder,
                    article.body_text,
                    content_hash,
                    lastmod,
                    is_changelog,
                    run_id,
                ),
            )
            for img in images:
                await conn.execute(
                    """
                    INSERT INTO images (id, article_id, url, change_key, position, caption, description, described_by)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (article_id, id) DO UPDATE SET
                        url = EXCLUDED.url, change_key = EXCLUDED.change_key,
                        position = EXCLUDED.position, caption = EXCLUDED.caption,
                        description = EXCLUDED.description, described_by = EXCLUDED.described_by
                    """,
                    (
                        img.id,
                        article.article_id,
                        img.url,
                        img.change_key,
                        img.position,
                        img.caption,
                        img.description,
                        img.described_by,
                    ),
                )
            for chunk in new_chunks:
                await _insert_chunk(conn, chunk, embedding_model, article_id=article.article_id, gap_id=None)
            await conn.execute(
                "DELETE FROM chunks WHERE article_id = %s AND NOT (id = ANY(%s))",
                (article.article_id, list(keep_chunk_ids)),
            )
            await conn.execute(
                "DELETE FROM images WHERE article_id = %s AND NOT (id = ANY(%s))",
                (article.article_id, [img.id for img in images]),
            )

    async def pending_images(self, limit: int | None) -> list[PendingImage]:
        """Screenshots of active articles that only have the alt-text fallback description."""
        async with self.pool.connection() as conn:
            cur = await conn.execute(
                """
                SELECT i.id, i.url, i.change_key, i.position, i.caption, a.id AS article_id, a.title,
                       coalesce(c.heading, '') AS heading, coalesce(c.context_prefix, '') AS context_prefix,
                       coalesce(c.position, 0) AS chunk_position
                FROM images i
                JOIN articles a ON a.id = i.article_id AND a.status = 'active'
                LEFT JOIN chunks c ON c.article_id = i.article_id AND c.image_id = i.id
                WHERE i.described_by = 'alt_text'
                ORDER BY a.id, i.position
                LIMIT %s
                """,
                (limit,),
            )
            return [PendingImage(**row) for row in await cur.fetchall()]

    async def upgrade_image(
        self, image_id: str, description: str, chunk: NewChunk, article_id: str, embedding_model: str
    ) -> None:
        async with self.pool.connection() as conn, conn.transaction():
            await conn.execute(
                "UPDATE images SET description = %s, described_by = 'claude' WHERE article_id = %s AND id = %s",
                (description, article_id, image_id),
            )
            await conn.execute("DELETE FROM chunks WHERE article_id = %s AND image_id = %s", (article_id, image_id))
            await _insert_chunk(conn, chunk, embedding_model, article_id=article_id, gap_id=None)

    async def mark_removed(self, article_id: str, run_id: str | None = None) -> bool:
        """Removed articles keep their row (for audit and the articles endpoint) but lose every chunk."""
        async with self.pool.connection() as conn, conn.transaction():
            cur = await conn.execute(
                "UPDATE articles SET status = 'removed', removed_at = now(), "
                "last_seen_run_id = coalesce(%s, last_seen_run_id), updated_at = now() "
                "WHERE id = %s AND status = 'active'",
                (run_id, article_id),
            )
            await conn.execute("DELETE FROM chunks WHERE article_id = %s", (article_id,))
            return cur.rowcount > 0

    async def get_article(self, article_id: str) -> dict[str, Any] | None:
        async with self.pool.connection() as conn:
            cur = await conn.execute(
                "SELECT id, title, url, category, folder, body_text, sitemap_lastmod, status, updated_at "
                "FROM articles WHERE id = %s",
                (article_id,),
            )
            row = await cur.fetchone()
            if row is None:
                return None
            cur = await conn.execute(
                "SELECT url, description FROM images WHERE article_id = %s ORDER BY position", (article_id,)
            )
            return {**row, "images": await cur.fetchall()}

    async def counts(self) -> tuple[int, int, datetime | None]:
        async with self.pool.connection() as conn:
            cur = await conn.execute(
                """
                SELECT (SELECT count(*) FROM articles WHERE status = 'active') AS articles,
                       (SELECT count(*) FROM chunks) AS chunks,
                       (SELECT max(finished_at) FROM crawl_runs WHERE status = 'done') AS last_sync
                """
            )
            row = await cur.fetchone()
            assert row is not None
            return int(row["articles"]), int(row["chunks"]), row["last_sync"]

    # ---- retrieval ------------------------------------------------------------------------------

    async def hybrid_search(
        self,
        *,
        query_text: str,
        query_vector: list[float],
        dim: int,
        top_k: int,
        category: str | None,
        changelog_boost: float,
        expert_boost: float,
        public_url: str,
        candidates: int = 50,
    ) -> list[SearchHit]:
        """Vector (HNSW cosine) and full-text rankings fused with reciprocal rank fusion in one query.

        Removed articles are filtered here as well as having no chunks, so a half-finished removal
        can never leak into answers.
        """
        vec_type = sql.SQL("vector({})").format(sql.Literal(dim))
        query = sql.SQL(
            """
            WITH q AS (
                SELECT %(qvec)s::{vt} AS v, websearch_to_tsquery('english', %(qtext)s) AS tq
            ),
            vec AS (
                SELECT id, 1 - dist AS sim, row_number() OVER (ORDER BY dist) AS rnk
                FROM (
                    SELECT c.id, c.embedding::{vt} <=> q.v AS dist
                    FROM q, chunks c LEFT JOIN articles a ON a.id = c.article_id
                    WHERE c.embedding_dim = %(dim)s
                      AND (c.kind = 'expert' OR (a.status = 'active'
                           AND (%(category)s::text IS NULL OR a.category = %(category)s)))
                    ORDER BY c.embedding::{vt} <=> q.v
                    LIMIT %(candidates)s
                ) s
            ),
            fts AS (
                SELECT id, row_number() OVER (ORDER BY rank DESC) AS rnk
                FROM (
                    SELECT c.id, ts_rank_cd(c.tsv, q.tq, %(fts_norm)s) AS rank
                    FROM q, chunks c LEFT JOIN articles a ON a.id = c.article_id
                    WHERE c.tsv @@ q.tq
                      AND (c.kind = 'expert' OR (a.status = 'active'
                           AND (%(category)s::text IS NULL OR a.category = %(category)s)))
                    ORDER BY rank DESC
                    LIMIT %(candidates)s
                ) s
            ),
            fused AS (
                SELECT coalesce(vec.id, fts.id) AS id, vec.sim,
                       coalesce(%(vec_w)s / (%(k)s + vec.rnk), 0) + coalesce(%(fts_w)s / (%(k)s + fts.rnk), 0) AS rrf
                FROM vec FULL OUTER JOIN fts ON fts.id = vec.id
            )
            SELECT c.id AS chunk_id, c.kind, c.text, c.context_prefix, f.sim,
                   f.rrf * c.boost * CASE WHEN c.kind = 'expert' THEN %(expert_boost)s
                                         WHEN a.is_changelog THEN %(changelog_boost)s
                                         ELSE 1.0 END AS score,
                   coalesce(a.id, 'expert:' || g.id) AS article_id,
                   coalesce(a.title, 'Expert answer: ' || g.question) AS title,
                   coalesce(a.url, %(public_url)s || '/experts#' || g.id) AS url,
                   coalesce(a.category, 'Expert answers') AS category,
                   coalesce(a.folder, '') AS folder,
                   i.url AS image_url
            FROM fused f
            JOIN chunks c ON c.id = f.id
            LEFT JOIN articles a ON a.id = c.article_id
            LEFT JOIN gap_tickets g ON g.id = c.gap_id
            LEFT JOIN images i ON i.article_id = c.article_id AND i.id = c.image_id
            ORDER BY score DESC, chunk_id
            LIMIT %(top_k)s
            """
        ).format(vt=vec_type)
        params = {
            "qvec": to_vector_literal(query_vector),
            "qtext": query_text,
            "category": category,
            "dim": dim,
            "candidates": candidates,
            "k": RRF_K,
            "vec_w": VECTOR_WEIGHT,
            "fts_w": FTS_WEIGHT,
            "fts_norm": FTS_NORMALIZATION,
            "expert_boost": expert_boost,
            "changelog_boost": changelog_boost,
            "public_url": public_url.rstrip("/"),
            "top_k": top_k,
        }
        async with self.pool.connection() as conn, conn.transaction():
            # pgvector 0.8: keep scanning the HNSW graph when filters reject candidates.
            await conn.execute("SET LOCAL hnsw.iterative_scan = relaxed_order")
            # The candidate list is 50 deep; the default ef_search (40) would cap and blur it.
            await conn.execute("SET LOCAL hnsw.ef_search = 200")
            cur = await conn.execute(query, params)
            rows = await cur.fetchall()
        return [
            SearchHit(
                chunk_id=r["chunk_id"],
                article_id=r["article_id"],
                title=r["title"],
                url=r["url"],
                category=r["category"],
                folder=r["folder"],
                kind=r["kind"],
                text=r["text"],
                context_prefix=r["context_prefix"],
                score=float(r["score"]),
                similarity=None if r["sim"] is None else float(r["sim"]),
                image_url=r["image_url"],
            )
            for r in rows
        ]

    # ---- gaps -----------------------------------------------------------------------------------

    async def open_gap(self, question: str, context: str | None, source: str, bot_attempt: str | None) -> str:
        """Id of the open ticket for this question, creating it if needed (atomic via a unique index)."""
        async with self.pool.connection() as conn:
            cur = await conn.execute(
                """
                INSERT INTO gap_tickets (id, question, context, source, bot_attempt) VALUES (%s, %s, %s, %s, %s)
                ON CONFLICT (lower(trim(question))) WHERE status = 'open' DO NOTHING
                RETURNING id
                """,
                (new_id("gap"), question, context, source, bot_attempt),
            )
            row = await cur.fetchone()
            if row is not None:
                return str(row["id"])
            cur = await conn.execute(
                "SELECT id FROM gap_tickets WHERE status = 'open' AND lower(trim(question)) = lower(trim(%s))",
                (question,),
            )
            existing = await cur.fetchone()
            assert existing is not None
            return str(existing["id"])

    async def list_gaps(self, status: str | None) -> list[dict[str, Any]]:
        async with self.pool.connection() as conn:
            cur = await conn.execute(
                "SELECT id, question, context, source, status, bot_attempt, answer, expert, created_at "
                "FROM gap_tickets WHERE %(status)s::text IS NULL OR status = %(status)s "
                "ORDER BY created_at DESC",
                {"status": status},
            )
            return list(await cur.fetchall())

    async def get_gap(self, gap_id: str) -> dict[str, Any] | None:
        async with self.pool.connection() as conn:
            cur = await conn.execute("SELECT * FROM gap_tickets WHERE id = %s", (gap_id,))
            return await cur.fetchone()

    async def answer_gap(self, gap_id: str, answer: str, expert: str, chunk: NewChunk, embedding_model: str) -> None:
        async with self.pool.connection() as conn, conn.transaction():
            await conn.execute(
                "UPDATE gap_tickets SET status = 'answered', answer = %s, expert = %s, answered_at = now() "
                "WHERE id = %s",
                (answer, expert, gap_id),
            )
            await conn.execute("DELETE FROM chunks WHERE gap_id = %s", (gap_id,))
            await _insert_chunk(conn, chunk, embedding_model, article_id=None, gap_id=gap_id)

    # ---- crawl runs -----------------------------------------------------------------------------

    @asynccontextmanager
    async def exclusive_runs(self) -> AsyncIterator[bool]:
        """Session advisory lock: one crawl/sync at a time across API, scheduler and CLI processes."""
        async with self.pool.connection() as conn:
            cur = await conn.execute("SELECT pg_try_advisory_lock(%s) AS ok", (RUN_LOCK_KEY,))
            row = await cur.fetchone()
            acquired = bool(row and row["ok"])
            try:
                yield acquired
            finally:
                if acquired:
                    await conn.execute("SELECT pg_advisory_unlock(%s)", (RUN_LOCK_KEY,))

    async def create_run(self, kind: str, item_limit: int | None) -> str:
        run_id = new_id("run")
        async with self.pool.connection() as conn:
            await conn.execute(
                "INSERT INTO crawl_runs (id, kind, status, item_limit) VALUES (%s, %s, 'running', %s)",
                (run_id, kind, item_limit),
            )
        return run_id

    async def get_run(self, run_id: str) -> dict[str, Any] | None:
        async with self.pool.connection() as conn:
            cur = await conn.execute("SELECT * FROM crawl_runs WHERE id = %s", (run_id,))
            return await cur.fetchone()

    async def latest_resumable(self, kind: str) -> str | None:
        """Newest run of `kind` that stopped with work left: killed (still `running`) or `failed` midway."""
        async with self.pool.connection() as conn:
            cur = await conn.execute(
                """
                SELECT r.id FROM crawl_runs r
                WHERE r.kind = %s AND (r.status = 'running' OR (r.status = 'failed' AND r.planned))
                  AND (NOT r.planned OR EXISTS (
                      SELECT 1 FROM crawl_items i WHERE i.run_id = r.id AND i.status IN ('pending', 'in_progress')))
                ORDER BY r.started_at DESC LIMIT 1
                """,
                (kind,),
            )
            row = await cur.fetchone()
            return row["id"] if row else None

    async def plan_run(
        self,
        run_id: str,
        fetch: Sequence[SitemapEntry],
        remove: Sequence[str],
        skipped_unchanged: int,
    ) -> None:
        async with self.pool.connection() as conn, conn.transaction():
            rows: list[tuple[Any, ...]] = [
                (run_id, e.url, e.article_id, "fetch", e.lastmod, i) for i, e in enumerate(fetch)
            ]
            rows += [(run_id, f"removed:{aid}", aid, "remove", None, len(fetch) + i) for i, aid in enumerate(remove)]
            async with conn.cursor() as cur:
                await cur.executemany(
                    "INSERT INTO crawl_items (run_id, url, article_id, action, sitemap_lastmod, position) "
                    "VALUES (%s, %s, %s, %s, %s, %s) ON CONFLICT DO NOTHING",
                    rows,
                )
            await conn.execute(
                "UPDATE crawl_runs SET planned = true, skipped_unchanged = %s WHERE id = %s",
                (skipped_unchanged, run_id),
            )

    async def mark_running(self, run_id: str) -> None:
        async with self.pool.connection() as conn:
            await conn.execute(
                "UPDATE crawl_runs SET status = 'running', error = NULL, finished_at = NULL WHERE id = %s", (run_id,)
            )

    async def requeue_in_progress(self, run_id: str) -> int:
        """Items a crashed worker had claimed go back to pending."""
        async with self.pool.connection() as conn:
            cur = await conn.execute(
                "UPDATE crawl_items SET status = 'pending' WHERE run_id = %s AND status = 'in_progress'",
                (run_id,),
            )
            return cur.rowcount

    async def claim_next(self, run_id: str) -> WorkItem | None:
        async with self.pool.connection() as conn:
            # An item that keeps killing the worker would otherwise be retried on every resume.
            await conn.execute(
                "UPDATE crawl_items SET status = 'failed', outcome = 'error', "
                "error = 'gave up after ' || attempts || ' attempts', updated_at = now() "
                "WHERE run_id = %s AND status = 'pending' AND attempts >= %s",
                (run_id, MAX_ITEM_ATTEMPTS),
            )
            cur = await conn.execute(
                """
                UPDATE crawl_items SET status = 'in_progress', attempts = attempts + 1, updated_at = now()
                WHERE (run_id, url) = (
                    SELECT run_id, url FROM crawl_items
                    WHERE run_id = %s AND status = 'pending'
                    ORDER BY position LIMIT 1 FOR UPDATE SKIP LOCKED
                )
                RETURNING run_id, url, article_id, action, sitemap_lastmod
                """,
                (run_id,),
            )
            row = await cur.fetchone()
            return WorkItem(**row) if row else None

    async def finish_item(self, item: WorkItem, outcome: str, error: str | None = None) -> None:
        status = "failed" if outcome == "error" else "done"
        async with self.pool.connection() as conn:
            await conn.execute(
                "UPDATE crawl_items SET status = %s, outcome = %s, error = %s, updated_at = now() "
                "WHERE run_id = %s AND url = %s",
                (status, outcome, error, item.run_id, item.url),
            )

    async def finalize_run(self, run_id: str, status: str, error: str | None = None) -> RunCounts:
        async with self.pool.connection() as conn:
            cur = await conn.execute(
                "SELECT outcome, count(*) AS n FROM crawl_items WHERE run_id = %s AND outcome IS NOT NULL "
                "GROUP BY outcome",
                (run_id,),
            )
            counts = RunCounts()
            for row in await cur.fetchall():
                for _ in range(int(row["n"])):
                    counts.record(row["outcome"])
            cur = await conn.execute(
                """
                UPDATE crawl_runs SET status = %s, error = %s, finished_at = now(),
                    added = %s, updated = %s, removed = %s, errors = %s,
                    unchanged = skipped_unchanged + %s
                WHERE id = %s
                RETURNING unchanged
                """,
                (status, error, counts.added, counts.updated, counts.removed, counts.errors, counts.unchanged, run_id),
            )
            finished = await cur.fetchone()
            counts.unchanged = int(finished["unchanged"]) if finished else counts.unchanged
            return counts

    # ---- bookkeeping ----------------------------------------------------------------------------

    async def record_llm_call(self, record: CallRecord) -> None:
        async with self.pool.connection() as conn:
            await conn.execute(
                """
                INSERT INTO llm_calls (purpose, model, input_tokens, output_tokens, cache_read_tokens,
                    cache_write_tokens, cost_usd, latency_ms, stop_reason, request_id, error)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    record.purpose,
                    record.model,
                    record.input_tokens,
                    record.output_tokens,
                    record.cache_read_tokens,
                    record.cache_write_tokens,
                    record.cost_usd,
                    record.latency_ms,
                    record.stop_reason,
                    record.request_id,
                    record.error,
                ),
            )

    async def record_feedback(self, question: str, answer: str, helpful: bool) -> None:
        async with self.pool.connection() as conn:
            await conn.execute(
                "INSERT INTO answer_feedback (question, answer, helpful) VALUES (%s, %s, %s)",
                (question, answer, helpful),
            )


async def _insert_chunk(
    conn: AsyncConnection[DictRow],
    chunk: NewChunk,
    embedding_model: str,
    *,
    article_id: str | None,
    gap_id: str | None,
) -> None:
    d = chunk.draft
    await conn.execute(
        """
        INSERT INTO chunks (id, article_id, gap_id, image_id, kind, position, heading, text, context_prefix,
                            embedding, embedding_dim, embedding_model, boost)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s::vector, %s, %s, %s)
        ON CONFLICT (id) DO UPDATE SET position = EXCLUDED.position, embedding = EXCLUDED.embedding,
            embedding_dim = EXCLUDED.embedding_dim, embedding_model = EXCLUDED.embedding_model
        """,
        (
            chunk.id,
            article_id,
            gap_id,
            d.image_id,
            d.kind,
            d.position,
            d.heading,
            d.text,
            d.context_prefix,
            to_vector_literal(chunk.embedding),
            len(chunk.embedding),
            embedding_model,
            d.boost,
        ),
    )
