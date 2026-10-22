"""Internal value types shared by the crawler, indexer and retrieval layers."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Literal

ChunkKind = Literal["text", "image", "expert"]
Label = Literal["setting", "addon", "widget_customization", "custom_dev", "unknown"]


@dataclass(frozen=True, slots=True)
class SitemapEntry:
    article_id: str
    url: str
    lastmod: datetime | None


@dataclass(frozen=True, slots=True)
class ImageRef:
    id: str
    url: str
    change_key: str
    position: int
    caption: str  # nearest heading plus surrounding text; the offline description


@dataclass(frozen=True, slots=True)
class Section:
    heading: str
    text: str
    image_ids: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class ParsedArticle:
    article_id: str
    url: str
    title: str
    category: str
    folder: str
    sections: tuple[Section, ...]
    images: tuple[ImageRef, ...]

    @property
    def body_text(self) -> str:
        parts = [f"{s.heading}\n{s.text}" if s.heading else s.text for s in self.sections]
        return "\n\n".join(p for p in parts if p.strip())


@dataclass(frozen=True, slots=True)
class ChunkDraft:
    kind: ChunkKind
    position: int
    heading: str
    text: str
    context_prefix: str
    image_id: str | None = None
    boost: float = 1.0


@dataclass(frozen=True, slots=True)
class SearchHit:
    chunk_id: str
    article_id: str
    title: str
    url: str
    category: str
    folder: str
    kind: ChunkKind
    text: str
    context_prefix: str
    score: float
    similarity: float | None
    image_url: str | None


@dataclass(slots=True)
class RunCounts:
    added: int = 0
    updated: int = 0
    removed: int = 0
    unchanged: int = 0
    errors: int = 0

    def record(self, outcome: str) -> None:
        if outcome == "error":
            self.errors += 1
        else:
            setattr(self, outcome, getattr(self, outcome) + 1)


@dataclass(frozen=True, slots=True)
class SitemapDiff:
    added: list[SitemapEntry] = field(default_factory=list)
    changed: list[SitemapEntry] = field(default_factory=list)  # lastmod moved: fetch, hash decides
    unchanged: list[SitemapEntry] = field(default_factory=list)
    removed: list[str] = field(default_factory=list)  # article ids
