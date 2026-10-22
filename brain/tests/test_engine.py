from __future__ import annotations

import psycopg
import pytest

from brain.crawl.engine import CrawlEngine, RunBusyError, UnsafeRemovalError
from brain.domain import ParsedArticle
from brain.ingest.indexer import Indexer, IndexOutcome
from brain.repo import Repository
from tests.helpers import FakeSite, fixture_html, sitemap_xml

pytestmark = pytest.mark.db
T1, T2 = "2026-06-01T00:00:00+00:00", "2026-07-01T00:00:00+00:00"
IDS = ["5000558389", "12000048777", "12000095723", "5000558357"]


def _publish(site: FakeSite, lastmods: dict[str, str]) -> None:
    site.sitemap = sitemap_xml(lastmods)
    for aid in lastmods:
        site.pages.setdefault(aid, fixture_html(aid))


async def _active(repo: Repository) -> set[str]:
    return {aid for aid, (status, _) in (await repo.known_states()).items() if status == "active"}


async def test_initial_crawl_indexes_every_sitemap_article(
    engine: CrawlEngine, site: FakeSite, repo: Repository
) -> None:
    _publish(site, dict.fromkeys(IDS, T1))
    counts = await engine.execute(await engine.start("crawl"))
    assert (counts.added, counts.errors) == (4, 0)
    assert await _active(repo) == set(IDS)
    article = await repo.get_article("5000558389")
    assert article is not None and len(article["images"]) == 2


async def test_crawl_limit_samples_the_sitemap(engine: CrawlEngine, site: FakeSite, repo: Repository) -> None:
    _publish(site, dict.fromkeys(IDS, T1))
    counts = await engine.execute(await engine.start("crawl", limit=2))
    assert counts.added == 2
    assert len(await _active(repo)) == 2


async def test_sync_detects_added_updated_unchanged_removed(
    engine: CrawlEngine, site: FakeSite, repo: Repository
) -> None:
    _publish(site, dict.fromkeys(IDS[:3], T1))
    await engine.execute(await engine.start("crawl"))

    # 5000558389 really changed, 12000048777 only had its lastmod bumped, 12000095723 left the sitemap,
    # 5000558357 is new.
    site.pages["5000558389"] = fixture_html("5000558389").replace("Social Media Accounts", "Social Profiles")
    _publish(site, {"5000558389": T2, "12000048777": T2, "5000558357": T1})
    site.requests.clear()
    counts = await engine.execute(await engine.start("sync"))

    assert (counts.added, counts.updated, counts.removed, counts.unchanged, counts.errors) == (1, 1, 1, 1, 0)
    assert await _active(repo) == {"5000558389", "12000048777", "5000558357"}
    removed = await repo.get_article("12000095723")
    assert removed is not None and removed["status"] == "removed"
    assert await repo.chunk_ids("12000095723", "hash") == set()

    # A third sync with nothing changed fetches no article pages at all (lastmod is the hint).
    site.requests.clear()
    counts = await engine.execute(await engine.start("sync"))
    assert (counts.unchanged, counts.added, counts.updated) == (3, 0, 0)
    assert all(url.endswith("sitemap.xml") for url in site.requests)


async def test_404_marks_article_removed(engine: CrawlEngine, site: FakeSite, repo: Repository) -> None:
    _publish(site, dict.fromkeys(IDS[:2], T1))
    await engine.execute(await engine.start("crawl"))
    _publish(site, {"5000558389": T1, "12000048777": T2})
    del site.pages["12000048777"]
    counts = await engine.execute(await engine.start("sync"))
    assert counts.removed == 1
    assert await _active(repo) == {"5000558389"}


async def test_refuses_mass_removal_from_a_broken_sitemap(repo: Repository, indexer: Indexer, site: FakeSite) -> None:
    engine = CrawlEngine(repo, site.fetch, indexer, sitemap_url="https://h/sitemap.xml", removal_floor=1)
    _publish(site, dict.fromkeys(IDS, T1))
    await engine.execute(await engine.start("crawl"))
    _publish(site, {"5000558389": T1})  # 3 of 4 vanish
    run_id = await engine.start("sync")
    with pytest.raises(UnsafeRemovalError):
        await engine.execute(run_id)
    assert await _active(repo) == set(IDS)
    run = await repo.get_run(run_id)
    assert run is not None and run["status"] == "failed"


class CrashingIndexer(Indexer):
    """Dies (like a killed process) after `survive` articles."""

    def __init__(self, inner: Indexer, survive: int) -> None:
        self._inner, self._left = inner, survive

    async def index(self, article: ParsedArticle, **kw: object) -> IndexOutcome:
        if self._left == 0:
            raise KeyboardInterrupt("simulated crash")
        self._left -= 1
        return await self._inner.index(article, **kw)  # type: ignore[arg-type]


async def test_resume_after_crash_continues_where_it_stopped(
    repo: Repository, indexer: Indexer, site: FakeSite
) -> None:
    _publish(site, dict.fromkeys(IDS, T1))
    url = "https://support.brilliantdirectories.com/support/sitemap.xml"
    crashing = CrawlEngine(repo, site.fetch, CrashingIndexer(indexer, survive=2), sitemap_url=url)
    run_id = await crashing.start("crawl")
    with pytest.raises(KeyboardInterrupt):
        await crashing.execute(run_id)
    assert len(await _active(repo)) == 2

    site.requests.clear()
    counts = await CrawlEngine(repo, site.fetch, indexer, sitemap_url=url).execute(run_id)
    assert counts.added == 4 and counts.errors == 0
    assert len(site.requests) == 2  # only the two unfinished articles were fetched again
    assert await _active(repo) == set(IDS)


async def test_only_one_run_at_a_time(engine: CrawlEngine, site: FakeSite, repo: Repository) -> None:
    _publish(site, dict.fromkeys(IDS[:1], T1))
    run_id = await engine.start("crawl")
    async with repo.exclusive_runs() as held:
        assert held
        with pytest.raises(RunBusyError):
            await engine.execute(run_id)


async def test_unchanged_chunks_keep_their_rows_on_update(
    engine: CrawlEngine, site: FakeSite, repo: Repository
) -> None:
    _publish(site, {"5000558389": T1})
    await engine.execute(await engine.start("crawl"))
    before = await repo.chunk_ids("5000558389", "hash")
    site.pages["5000558389"] = fixture_html("5000558389").replace("10. Snapchat", "10. Snap")
    _publish(site, {"5000558389": T2})
    await engine.execute(await engine.start("sync"))
    after = await repo.chunk_ids("5000558389", "hash")
    assert before & after, "untouched sections must be reused, not re-embedded"
    assert before != after


class FailingIndexer(Indexer):
    """Raises an ordinary error for one article, as an embedding API rejecting it would."""

    def __init__(self, inner: Indexer, bad_id: str) -> None:
        self._inner, self._bad = inner, bad_id

    async def index(self, article: ParsedArticle, **kw: object) -> IndexOutcome:
        if article.article_id == self._bad:
            raise RuntimeError("embedding API rejected the chunk")
        return await self._inner.index(article, **kw)  # type: ignore[arg-type]


async def test_one_bad_article_does_not_stop_the_run(repo: Repository, indexer: Indexer, site: FakeSite) -> None:
    _publish(site, dict.fromkeys(IDS, T1))
    engine = CrawlEngine(repo, site.fetch, FailingIndexer(indexer, IDS[1]), sitemap_url="https://h/sitemap.xml")
    counts = await engine.execute(await engine.start("crawl"))
    assert (counts.added, counts.errors) == (3, 1)
    assert await _active(repo) == set(IDS) - {IDS[1]}


async def test_mass_404_does_not_empty_the_index(repo: Repository, indexer: Indexer, site: FakeSite) -> None:
    _publish(site, dict.fromkeys(IDS, T1))
    engine = CrawlEngine(repo, site.fetch, indexer, sitemap_url="https://h/sitemap.xml", removal_floor=1)
    await engine.execute(await engine.start("crawl"))
    site.pages.clear()  # the site answers 404 for everything
    counts = await engine.execute(await engine.start("crawl"))
    assert counts.removed == 1 and counts.errors == 3
    assert len(await _active(repo)) == 3


async def test_busy_lock_leaves_a_resumed_run_alone(engine: CrawlEngine, site: FakeSite, repo: Repository) -> None:
    _publish(site, dict.fromkeys(IDS[:1], T1))
    run_id = await engine.start("crawl")
    async with repo.exclusive_runs():
        with pytest.raises(RunBusyError):
            await engine.execute(run_id, owner=False)
    run = await repo.get_run(run_id)
    assert run is not None and run["status"] == "running"


async def test_failed_run_with_work_left_is_resumable(repo: Repository, indexer: Indexer, site: FakeSite) -> None:
    _publish(site, dict.fromkeys(IDS, T1))
    url = "https://h/sitemap.xml"
    run_id = await CrawlEngine(repo, site.fetch, indexer, sitemap_url=url).start("crawl")

    class DatabaseDown(Indexer):
        async def index(self, article: ParsedArticle, **kw: object) -> IndexOutcome:
            raise psycopg.OperationalError("connection lost")

    with pytest.raises(psycopg.OperationalError):
        await CrawlEngine(
            repo, site.fetch, DatabaseDown(repo, indexer._embedder, indexer._describer), sitemap_url=url
        ).execute(run_id)
    assert await repo.latest_resumable("crawl") == run_id
    counts = await CrawlEngine(repo, site.fetch, indexer, sitemap_url=url).execute(run_id, owner=False)
    assert counts.added == 4
    run = await repo.get_run(run_id)
    assert run is not None and run["status"] == "done"


async def test_an_item_that_keeps_crashing_is_given_up(repo: Repository, indexer: Indexer, site: FakeSite) -> None:
    _publish(site, dict.fromkeys(IDS[:1], T1))
    url = "https://h/sitemap.xml"
    run_id = await CrawlEngine(repo, site.fetch, indexer, sitemap_url=url).start("crawl")
    crashing = CrawlEngine(repo, site.fetch, CrashingIndexer(indexer, survive=0), sitemap_url=url)
    for _ in range(3):
        with pytest.raises(KeyboardInterrupt):
            await crashing.execute(run_id, owner=False)
    counts = await crashing.execute(run_id, owner=False)
    assert counts.errors == 1
