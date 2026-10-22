from __future__ import annotations

from datetime import datetime

from brain.crawl.sitemap import article_id_from_url, diff_sitemap, parse_sitemap, sample_evenly
from brain.domain import SitemapEntry
from tests.helpers import FIXTURES, sitemap_xml


def test_parses_only_articles_from_real_sitemap_head() -> None:
    entries = parse_sitemap((FIXTURES / "sitemap_head.xml").read_text())
    assert len(entries) == 12
    assert all("/articles/" in e.url for e in entries)
    assert entries[0].article_id == "5000558357"
    assert entries[0].lastmod is not None and entries[0].lastmod.tzinfo is not None


def test_article_id_from_url() -> None:
    assert article_id_from_url("https://h/support/solutions/articles/12000110053-changelog-september-2026") == (
        "12000110053"
    )
    assert article_id_from_url("https://h/support/solutions/folders/12000005714") is None


def _entry(aid: str, lastmod: str) -> SitemapEntry:
    return SitemapEntry(aid, f"https://h/support/solutions/articles/{aid}", datetime.fromisoformat(lastmod))


def test_diff_added_changed_unchanged_removed() -> None:
    t1, t2 = "2026-06-01T00:00:00+00:00", "2026-07-01T00:00:00+00:00"
    known = {
        "1": ("active", datetime.fromisoformat(t1)),  # unchanged
        "2": ("active", datetime.fromisoformat(t1)),  # lastmod moved
        "3": ("active", datetime.fromisoformat(t1)),  # gone from sitemap
        "4": ("removed", datetime.fromisoformat(t1)),  # came back
    }
    diff = diff_sitemap([_entry("1", t1), _entry("2", t2), _entry("4", t1), _entry("5", t1)], known)
    assert [e.article_id for e in diff.unchanged] == ["1"]
    assert [e.article_id for e in diff.changed] == ["2"]
    assert sorted(e.article_id for e in diff.added) == ["4", "5"]
    assert diff.removed == ["3"]


def test_already_removed_articles_are_not_removed_again() -> None:
    known = {"9": ("removed", None)}
    assert diff_sitemap([], known).removed == []


def test_sample_evenly_spreads_across_list() -> None:
    assert sample_evenly(list(range(100)), 4) == [0, 25, 50, 75]
    assert sample_evenly([1, 2], 5) == [1, 2]
    assert sample_evenly([1, 2], None) == [1, 2]


def test_sitemap_helper_ignores_folder_pages() -> None:
    assert [e.article_id for e in parse_sitemap(sitemap_xml({"7": "2026-01-01T00:00:00+00:00"}))] == ["7"]
