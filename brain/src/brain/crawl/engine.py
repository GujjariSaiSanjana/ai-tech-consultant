"""Crawl and sync share one engine: plan a run into crawl_items, then work the items one by one.

Every item's state is committed as it finishes, so a run that dies halfway resumes exactly
where it stopped (`requeue_in_progress` + the same loop).
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any, Literal

from brain.crawl.fetcher import FetchResult, RobotsDisallowedError
from brain.crawl.parser import ArticleParseError, parse_article
from brain.crawl.sitemap import diff_sitemap, parse_sitemap, sample_evenly
from brain.domain import RunCounts
from brain.ingest.indexer import Indexer
from brain.repo import Repository, WorkItem

log = logging.getLogger(__name__)
RunKind = Literal["crawl", "sync"]
Fetch = Callable[[str], Awaitable[FetchResult]]


class UnsafeRemovalError(RuntimeError):
    pass


class RunBusyError(RuntimeError):
    pass


class CrawlEngine:
    def __init__(
        self,
        repo: Repository,
        fetch: Fetch,
        indexer: Indexer,
        *,
        sitemap_url: str,
        max_removed_fraction: float = 0.2,
        removal_floor: int = 10,
        save_html_dir: Path | None = None,
    ) -> None:
        self._repo = repo
        self._fetch = fetch
        self._indexer = indexer
        self._sitemap_url = sitemap_url
        self._max_removed_fraction = max_removed_fraction
        self._removal_floor = removal_floor
        self._save_html_dir = save_html_dir

    async def start(self, kind: RunKind, *, limit: int | None = None) -> str:
        return await self._repo.create_run(kind, limit)

    async def execute(self, run_id: str, *, sitemap_xml: str | None = None) -> RunCounts:
        """Plan (once) and work a run to completion. Safe to call again on a run that crashed."""
        run = await self._repo.get_run(run_id)
        if run is None:
            raise KeyError(run_id)
        async with self._repo.exclusive_runs() as acquired:
            if not acquired:
                await self._repo.finalize_run(run_id, "failed", "another crawl or sync is running")
                raise RunBusyError("another crawl or sync is running")
            return await self._execute_locked(run_id, run, sitemap_xml)

    async def _execute_locked(self, run_id: str, run: dict[str, Any], sitemap_xml: str | None) -> RunCounts:
        try:
            if not run["planned"]:
                await self._plan(run_id, run["kind"], run["item_limit"], sitemap_xml)
            requeued = await self._repo.requeue_in_progress(run_id)
            if requeued:
                log.info("run %s: requeued %d interrupted item(s)", run_id, requeued)
            while (item := await self._repo.claim_next(run_id)) is not None:
                outcome, error = await self._work(item)
                await self._repo.finish_item(item, outcome, error)
                log.info("run %s: %s %s%s", run_id, outcome, item.url, f" ({error})" if error else "")
        except Exception as exc:
            await self._repo.finalize_run(run_id, "failed", f"{type(exc).__name__}: {exc}"[:1000])
            raise
        return await self._repo.finalize_run(run_id, "done")

    async def _plan(self, run_id: str, kind: str, limit: int | None, sitemap_xml: str | None) -> None:
        if sitemap_xml is None:
            response = await self._fetch(self._sitemap_url)
            if response.status != 200:
                raise RuntimeError(f"sitemap fetch failed: HTTP {response.status}")
            sitemap_xml = response.text
        entries = parse_sitemap(sitemap_xml)
        if not entries:
            raise RuntimeError("sitemap has no article URLs; refusing to plan")
        if kind == "crawl":
            await self._repo.plan_run(run_id, sample_evenly(entries, limit), [], 0)
            return

        known = await self._repo.known_states()
        diff = diff_sitemap(entries, known)
        active = sum(1 for status, _ in known.values() if status == "active")
        if len(diff.removed) > max(self._removal_floor, self._max_removed_fraction * active):
            raise UnsafeRemovalError(
                f"sitemap drops {len(diff.removed)} of {active} active articles; refusing (likely a broken sitemap)"
            )
        to_fetch = [*diff.added, *diff.changed]
        if limit is not None:
            to_fetch = to_fetch[:limit]
        await self._repo.touch_many(diff.unchanged, run_id)
        await self._repo.plan_run(run_id, to_fetch, diff.removed, len(diff.unchanged))

    async def _work(self, item: WorkItem) -> tuple[str, str | None]:
        if item.action == "remove":
            removed = await self._repo.mark_removed(item.article_id, item.run_id)
            return ("removed" if removed else "unchanged"), None
        try:
            response = await self._fetch(item.url)
        except RobotsDisallowedError:
            return "error", "disallowed by robots.txt"
        except Exception as exc:  # network trouble must not kill the run; the item records it
            return "error", f"{type(exc).__name__}: {exc}"[:500]
        if response.status in {404, 410}:
            removed = await self._repo.mark_removed(item.article_id, item.run_id)
            return ("removed" if removed else "unchanged"), None
        if response.status != 200:
            return "error", f"HTTP {response.status}"
        if self._save_html_dir is not None:
            self._save_html_dir.mkdir(parents=True, exist_ok=True)
            (self._save_html_dir / f"{item.article_id}.html").write_text(response.text)
        try:
            article = parse_article(response.text, item.url)
        except ArticleParseError as exc:
            return "error", str(exc)
        outcome = await self._indexer.index(article, lastmod=item.sitemap_lastmod, run_id=item.run_id)
        return outcome, None
