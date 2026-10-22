from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from brain.crawl.parser import (
    ArticleParseError,
    content_hash,
    image_change_key,
    is_changelog,
    parse_article,
    setting_path_from_text,
    setting_path_from_title,
)
from tests.helpers import FIXTURES, fixture_article


def test_reads_title_breadcrumb_and_id() -> None:
    article = fixture_article("5000558389")
    assert article.article_id == "5000558389"
    assert article.title == "Settings » General Settings » Social Media Tab"
    assert (article.category, article.folder) == ("Settings", "General Settings")


def test_strips_page_chrome_from_body() -> None:
    body = fixture_article("5000558389").body_text
    assert "Link:" not in body
    assert not body.startswith("Settings » General Settings")
    assert "Was this article helpful" not in body
    assert "https://www.facebook.com will be accepted" in body


def test_sections_follow_h2_h3() -> None:
    headings = [s.heading for s in fixture_article("5000558389").sections]
    assert "Social Media Accounts" in headings
    assert "1. Facebook" in headings


def test_in_body_screenshots_only_with_change_keys() -> None:
    article = fixture_article("5000558389")
    assert [img.id for img in article.images] == ["12132171349", "12132162075"]
    assert article.images[0].change_key == "1696295524"
    assert all("cdn.freshdesk.com" in img.url for img in article.images)
    assert "Footer Menu" in article.images[1].caption


@pytest.mark.parametrize("path", sorted(FIXTURES.glob("*.html")), ids=lambda p: p.stem)
def test_every_fixture_parses(path: Path) -> None:
    article = parse_article(path.read_text(), f"https://x/support/solutions/articles/{path.stem}-x")
    assert article.article_id == path.stem
    assert article.title and article.category


def test_rejects_pages_without_article_body() -> None:
    with pytest.raises(ArticleParseError):
        parse_article("<html><body><p>Folder listing</p></body></html>", "https://x/support/solutions/1")


def test_changelog_detection() -> None:
    assert is_changelog(fixture_article("12000102080"))
    assert not is_changelog(fixture_article("5000558389"))


def test_setting_path_from_breadcrumb_title() -> None:
    assert setting_path_from_title("Settings » General Settings » Social Media Tab") == (
        "Settings > General Settings > Social Media Tab"
    )
    assert setting_path_from_title("How to Login As Member") is None


def test_hash_is_stable_and_tracks_screenshot_uploads() -> None:
    article = fixture_article("5000558389")
    assert content_hash(article) == content_hash(fixture_article("5000558389"))
    reuploaded = replace(article, images=(replace(article.images[0], change_key="1799999999"), *article.images[1:]))
    assert content_hash(reuploaded) != content_hash(article)


def test_change_key_falls_back_to_path_hash() -> None:
    assert image_change_key("https://cdn/x.png?1696295524") == "1696295524"
    assert len(image_change_key("https://cdn/x.png")) == 16


@pytest.mark.parametrize(
    ("text", "path"),
    [
        (
            "Go to: Settings » General Settings » General » Website Phone Number",
            "Settings > General Settings > General > Website Phone Number",
        ),
        ("First, navigate to Toolbox >> Menu Manager .", "Toolbox > Menu Manager"),
        ("Open My Content > Media Manager and click +New Folder .", "My Content > Media Manager"),
        ("No click path in this sentence.", None),
    ],
)
def test_setting_path_from_prose(text: str, path: str | None) -> None:
    assert setting_path_from_text(text) == path
