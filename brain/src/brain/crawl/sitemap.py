from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from datetime import UTC, datetime
from xml.etree import ElementTree as ET

from brain.domain import SitemapDiff, SitemapEntry

_NS = {"sm": "http://www.sitemaps.org/schemas/sitemap/0.9"}
_ARTICLE_RE = re.compile(r"/support/solutions/articles/(\d+)(?:-[^/?#]*)?$")


def article_id_from_url(url: str) -> str | None:
    match = _ARTICLE_RE.search(url)
    return match.group(1) if match else None


def parse_sitemap(xml: str) -> list[SitemapEntry]:
    """Article entries only; folder and category pages carry build-time lastmods and no content."""
    root = ET.fromstring(xml)  # sitemap from a known host, no entity expansion in stdlib ET
    entries: dict[str, SitemapEntry] = {}
    for node in root.findall("sm:url", _NS):
        loc = (node.findtext("sm:loc", default="", namespaces=_NS) or "").strip()
        article_id = article_id_from_url(loc)
        if article_id is None:
            continue
        raw_lastmod = (node.findtext("sm:lastmod", default="", namespaces=_NS) or "").strip()
        lastmod = datetime.fromisoformat(raw_lastmod) if raw_lastmod else None
        if lastmod is not None and lastmod.tzinfo is None:
            lastmod = lastmod.replace(tzinfo=UTC)  # W3C datetime without offset; compare like timestamptz
        entries[article_id] = SitemapEntry(article_id=article_id, url=loc, lastmod=lastmod)
    return list(entries.values())


def diff_sitemap(entries: Iterable[SitemapEntry], known: Mapping[str, tuple[str, datetime | None]]) -> SitemapDiff:
    """Compare the live sitemap against `known` = {article_id: (status, stored lastmod)}.

    lastmod is only a hint: a changed lastmod schedules a fetch and the body hash decides
    whether the article really changed.
    """
    diff = SitemapDiff()
    seen: set[str] = set()
    for entry in entries:
        seen.add(entry.article_id)
        state = known.get(entry.article_id)
        if state is None or state[0] == "removed":
            diff.added.append(entry)
        elif entry.lastmod is None or state[1] is None or entry.lastmod != state[1]:
            diff.changed.append(entry)
        else:
            diff.unchanged.append(entry)
    diff.removed.extend(sorted(aid for aid, (status, _) in known.items() if status == "active" and aid not in seen))
    return diff


def sample_evenly[T](items: list[T], limit: int | None) -> list[T]:
    """Spread a limited crawl across the whole sitemap instead of its first N ids."""
    if limit is None or limit >= len(items):
        return list(items)
    if limit <= 0:
        return []
    step = len(items) / limit
    return [items[int(i * step)] for i in range(limit)]
