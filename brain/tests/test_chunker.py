from __future__ import annotations

from brain.domain import ParsedArticle, Section
from brain.ingest.chunker import (
    ALT_TEXT_IMAGE_BOOST,
    MAX_TOKENS,
    chunk_article,
    chunk_id,
    context_prefix,
    estimate_tokens,
)
from tests.helpers import fixture_article


def _article(*sections: Section) -> ParsedArticle:
    return ParsedArticle("1", "https://x", "Title", "Cat", "Folder", tuple(sections), ())


def test_prefix_is_category_folder_title() -> None:
    article = fixture_article("5000558389")
    assert context_prefix(article) == "Settings > General Settings > Settings » General Settings » Social Media Tab"


def test_small_sections_merge_and_large_ones_split() -> None:
    small = [Section(f"H{i}", "word " * 60) for i in range(4)]  # ~75 tokens each
    big = Section("Big", "\n".join("sentence " * 30 for _ in range(40)))  # ~1500 tokens
    chunks = chunk_article(_article(*small, big), {})
    assert all(estimate_tokens(c.text) <= MAX_TOKENS for c in chunks)
    assert chunks[0].text.count("H") >= 4  # the four small sections share one chunk
    assert len(chunks) >= 4


def test_image_chunks_carry_image_id_and_alt_text_boost() -> None:
    article = fixture_article("5000558389")
    chunks = chunk_article(article, {"12132162075": ("Footer with social icons", "alt_text")})
    images = [c for c in chunks if c.kind == "image"]
    assert len(images) == 1
    assert images[0].image_id == "12132162075"
    assert images[0].boost == ALT_TEXT_IMAGE_BOOST
    assert "Where Do These Links Appear?" in images[0].text
    claude = chunk_article(article, {"12132162075": ("Footer with social icons", "claude")})
    assert next(c for c in claude if c.kind == "image").boost == 1.0


def test_chunk_ids_are_stable_and_unique() -> None:
    article = _article(Section("A", "same text " * 100), Section("B", "same text " * 100))
    drafts = chunk_article(article, {})
    first = [chunk_id("1", d, set()) for d in drafts]
    seen: set[str] = set()
    ids = [chunk_id("1", d, seen) for d in drafts]
    assert len(set(ids)) == len(ids)
    assert ids[0] == first[0]
