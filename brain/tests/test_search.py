from __future__ import annotations

from dataclasses import replace

import pytest

from brain.ingest.indexer import Indexer
from brain.repo import Repository
from tests.conftest import ServiceFactory
from tests.helpers import fixture_article

pytestmark = pytest.mark.db


async def _index(indexer: Indexer, *ids: str) -> None:
    for aid in ids:
        await indexer.index(fixture_article(aid), lastmod=None, run_id=None)


async def test_hybrid_search_ranks_the_right_article_first(indexer: Indexer, make_service: ServiceFactory) -> None:
    await _index(indexer, "5000558389", "12000048777", "12000095723", "12000007994", "12000036189")
    service = make_service()
    hits = await service.search("Where do I add the Facebook and Instagram links for the website?")
    assert hits[0].article_id == "5000558389"
    hits = await service.search("API query parameters to find a member by email, with pagination")
    assert hits[0].article_id == "12000095723"
    assert all(h.similarity is None or -1 <= h.similarity <= 1 for h in hits)
    assert hits == sorted(hits, key=lambda h: h.score, reverse=True)


async def test_changelog_is_down_weighted(indexer: Indexer, make_service: ServiceFactory, repo: Repository) -> None:
    await _index(indexer, "12000102080", "5000558389")
    service = make_service()
    query = "software updates"
    hits = await service.search(query)
    changelog = [h for h in hits if h.article_id == "12000102080"]
    assert changelog
    async with repo.pool.connection() as conn:
        await conn.execute("UPDATE articles SET is_changelog = false")
    unboosted = {h.chunk_id: h.score for h in await service.search(query)}
    assert changelog[0].score == pytest.approx(unboosted[changelog[0].chunk_id] * 0.5)


async def test_removed_articles_never_surface(indexer: Indexer, make_service: ServiceFactory, repo: Repository) -> None:
    await _index(indexer, "5000558389", "12000048777")
    service = make_service()
    assert any(h.article_id == "5000558389" for h in await service.search("social media facebook links"))
    await repo.mark_removed("5000558389")
    assert all(h.article_id != "5000558389" for h in await service.search("social media facebook links"))


async def test_category_filter(indexer: Indexer, make_service: ServiceFactory) -> None:
    await _index(indexer, "5000558389", "12000095723")
    hits = await make_service().search("members API website", category="Developer Hub")
    assert hits and {h.category for h in hits} == {"Developer Hub"}


async def test_screenshot_shared_by_two_articles_survives_removal_of_one(
    indexer: Indexer, make_service: ServiceFactory, repo: Repository
) -> None:
    """The live help center reuses some attachments across articles; each article owns its copy."""
    owner = fixture_article("5000558389")
    shared = owner.images[1]  # "Where Do These Links Appear?" footer screenshot
    other = fixture_article("12000048777")
    borrowed = replace(other, images=(replace(other.images[0], id=shared.id, url=shared.url),))
    borrowed = replace(
        borrowed,
        sections=tuple(
            replace(s, image_ids=tuple(shared.id if i == other.images[0].id else i for i in s.image_ids))
            for s in borrowed.sections
        ),
    )
    await indexer.index(owner, lastmod=None, run_id=None)
    await indexer.index(borrowed, lastmod=None, run_id=None)
    await repo.mark_removed("5000558389")

    article = await repo.get_article("12000048777")
    assert article is not None and shared.url in [i["url"] for i in article["images"]]
    hits = await make_service().search("Screenshot website phone number header", top_k=20)
    image_hits = [h for h in hits if h.kind == "image" and h.article_id == "12000048777"]
    assert image_hits and all(h.image_url for h in image_hits)
