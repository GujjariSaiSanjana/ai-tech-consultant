from __future__ import annotations

import hashlib
from collections.abc import Mapping

from brain.domain import ChunkDraft, ParsedArticle, Section

MIN_TOKENS = 300
MAX_TOKENS = 600
# An alt-text "description" mostly repeats the surrounding text chunk; rank it below real descriptions.
ALT_TEXT_IMAGE_BOOST = 0.6


def estimate_tokens(text: str) -> int:
    """~4 characters per token for English prose; close enough to size chunks without a tokenizer."""
    return max(1, len(text) // 4)


def context_prefix(article: ParsedArticle) -> str:
    return " > ".join(part for part in (article.category, article.folder, article.title) if part)


def chunk_article(article: ParsedArticle, image_descriptions: Mapping[str, tuple[str, str]]) -> list[ChunkDraft]:
    """Text chunks follow H2/H3 sections, merged up to MIN_TOKENS and split above MAX_TOKENS.

    Every screenshot becomes its own `image` chunk so a question answered only by a screenshot
    can still be retrieved and cited with its image URL. `image_descriptions` maps an image id
    to (description, described_by).
    """
    prefix = context_prefix(article)
    drafts: list[ChunkDraft] = []
    for heading, text in _pack(article.sections):
        drafts.append(ChunkDraft(kind="text", position=len(drafts), heading=heading, text=text, context_prefix=prefix))
    headings = {image_id: s.heading for s in article.sections for image_id in s.image_ids}
    for image in article.images:
        description, described_by = image_descriptions.get(image.id, ("", "alt_text"))
        if description.strip():
            heading = headings.get(image.id, "")
            drafts.append(image_chunk(image.id, heading, description, described_by, prefix, position=len(drafts)))
    return drafts


def image_chunk(
    image_id: str, heading: str, description: str, described_by: str, prefix: str, *, position: int
) -> ChunkDraft:
    label = f"Screenshot in section '{heading}'" if heading else "Screenshot"
    return ChunkDraft(
        kind="image",
        position=position,
        heading=heading,
        text=f"{label}: {description.strip()}",
        context_prefix=prefix,
        image_id=image_id,
        boost=ALT_TEXT_IMAGE_BOOST if described_by == "alt_text" else 1.0,
    )


def _render(section: Section) -> str:
    return f"{section.heading}\n{section.text}".strip() if section.heading else section.text.strip()


def _pack(sections: tuple[Section, ...]) -> list[tuple[str, str]]:
    pieces: list[tuple[str, str]] = []
    for section in sections:
        rendered = _render(section)
        if not rendered:
            continue
        if estimate_tokens(rendered) <= MAX_TOKENS:
            pieces.append((section.heading, rendered))
        else:
            pieces.extend((section.heading, part) for part in _split(rendered))

    packed: list[tuple[str, str]] = []
    for heading, text in pieces:
        if packed:
            last_heading, last_text = packed[-1]
            merged = f"{last_text}\n\n{text}"
            if estimate_tokens(last_text) < MIN_TOKENS and estimate_tokens(merged) <= MAX_TOKENS:
                packed[-1] = (last_heading or heading, merged)
                continue
        packed.append((heading, text))
    return packed


def _split(text: str) -> list[str]:
    parts: list[str] = []
    current: list[str] = []
    for line in text.split("\n"):
        for piece in _hard_wrap(line):
            candidate = "\n".join([*current, piece])
            if current and estimate_tokens(candidate) > MAX_TOKENS:
                parts.append("\n".join(current))
                current = [piece]
            else:
                current.append(piece)
    if current:
        parts.append("\n".join(current))
    return parts


def _hard_wrap(line: str) -> list[str]:
    limit = MAX_TOKENS * 4
    if len(line) <= limit:
        return [line]
    words, out, buf = line.split(" "), [], ""
    for word in words:
        if buf and len(buf) + len(word) + 1 > limit:
            out.append(buf)
            buf = word
        else:
            buf = f"{buf} {word}" if buf else word
    if buf:
        out.append(buf)
    return out


def chunk_id(owner_id: str, draft: ChunkDraft, seen: set[str]) -> str:
    """Content-derived, so re-indexing an unchanged chunk is a no-op that keeps its embedding."""
    base = f"{owner_id}|{draft.kind}|{draft.image_id or ''}|{draft.context_prefix}|{draft.text}"
    for salt in range(1000):
        candidate = hashlib.sha256(f"{base}|{salt}".encode()).hexdigest()[:24]
        if candidate not in seen:
            seen.add(candidate)
            return candidate
    raise RuntimeError("chunk id space exhausted")
