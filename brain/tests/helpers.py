"""Pure test helpers (no database): fixture pages, sitemaps, settings and a fake site."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import httpx

from brain.config import Settings
from brain.crawl.fetcher import FetchResult
from brain.crawl.parser import parse_article
from brain.domain import ParsedArticle

FIXTURES = Path(__file__).parent / "fixtures"
SITE = "https://support.brilliantdirectories.com"


def fixture_html(article_id: str) -> str:
    return (FIXTURES / f"{article_id}.html").read_text()


def article_url(article_id: str) -> str:
    return f"{SITE}/support/solutions/articles/{article_id}-slug"


def fixture_article(article_id: str) -> ParsedArticle:
    return parse_article(fixture_html(article_id), article_url(article_id))


def sitemap_xml(entries: dict[str, str]) -> str:
    """{article_id: lastmod iso} -> sitemap with one folder page, which must be ignored."""
    urls = [f"<url><loc>{SITE}/support/solutions/12000001522</loc><lastmod>2026-10-18T08:00:54+00:00</lastmod></url>"]
    urls += [f"<url><loc>{article_url(aid)}</loc><lastmod>{lm}</lastmod></url>" for aid, lm in entries.items()]
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">' + "".join(urls) + "</urlset>"
    )


def make_settings(**overrides: Any) -> Settings:
    values: dict[str, Any] = {
        "BRAIN_EMBEDDER": "hash",
        "BRAIN_API_KEY": "test-key",
        "ANTHROPIC_API_KEY": None,
        "VOYAGE_API_KEY": None,
        "BRAIN_URL": "http://brain.test",
    }
    values.update(overrides)
    return Settings(_env_file=None, **values)


class FakeSite:
    """Serves fixture pages by article id; tests edit `pages` and `sitemap` between runs."""

    def __init__(self) -> None:
        self.pages: dict[str, str] = {}
        self.sitemap = ""
        self.requests: list[str] = []

    async def fetch(self, url: str) -> FetchResult:
        self.requests.append(url)
        if url.endswith("sitemap.xml"):
            return FetchResult(url, 200, self.sitemap)
        article_id = url.rsplit("/", 1)[-1].split("-", 1)[0]
        html = self.pages.get(article_id)
        return FetchResult(url, 200, html) if html is not None else FetchResult(url, 404, "")

    def transport(self) -> httpx.MockTransport:
        async def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/robots.txt":
                return httpx.Response(200, text="User-agent: *\nDisallow: /support/search\n")
            result = await self.fetch(str(request.url))
            return httpx.Response(result.status, text=result.text)

        return httpx.MockTransport(handler)
