"""Turn a Freshdesk article page into sections and in-body screenshots."""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from urllib.parse import urlsplit

from selectolax.lexbor import LexborHTMLParser, LexborNode

from brain.crawl.sitemap import article_id_from_url
from brain.domain import ImageRef, ParsedArticle, Section

_SECTION_TAGS = {"h2", "h3"}
_LEAF_TAGS = {"p", "h4", "h5", "h6", "pre", "blockquote", "dt", "dd", "figcaption"}
_SKIP_TAGS = {"script", "style", "noscript", "iframe", "button", "form"}
_WS = re.compile(r"\s+")
_CHANGELOG = re.compile(r"software updates?|change ?log|release notes", re.IGNORECASE)


class ArticleParseError(ValueError):
    pass


def clean(text: str) -> str:
    return _WS.sub(" ", text.replace("\xa0", " ")).strip()


@dataclass
class _Builder:
    sections: list[tuple[str, list[str], list[str]]] = field(default_factory=lambda: [("", [], [])])
    images: list[dict[str, str | int]] = field(default_factory=list)
    seen_images: set[str] = field(default_factory=set)

    def heading(self, text: str) -> None:
        if text:
            self.sections.append((text, [], []))

    def line(self, text: str) -> None:
        if text:
            self.sections[-1][1].append(text)

    def image(self, node: LexborNode) -> None:
        image_id = node.attributes.get("data-id")
        src = node.attributes.get("src")
        if not image_id or not src or image_id in self.seen_images:
            return
        self.seen_images.add(image_id)
        heading, lines, image_ids = self.sections[-1]
        image_ids.append(image_id)
        self.images.append(
            {
                "id": image_id,
                "url": src,
                "alt": clean(node.attributes.get("alt") or ""),
                "heading": heading,
                "before": lines[-1] if lines else "",
                "section": len(self.sections) - 1,
                "line": len(lines),
            }
        )


def parse_article(html: str, url: str) -> ParsedArticle:
    tree = LexborHTMLParser(html)
    body = tree.css_first("article.article-body#article-body")
    title_node = tree.css_first("article#article-body h1.post-title.heading")
    if body is None or title_node is None:
        raise ArticleParseError(f"no article body at {url}")
    title = clean(title_node.text())

    id_node = tree.css_first("[data-article-id]")
    article_id = (id_node.attributes.get("data-article-id") if id_node else None) or article_id_from_url(url)
    if not article_id:
        raise ArticleParseError(f"no article id at {url}")

    crumbs = [clean(a.text()) for a in tree.css("div.breadcrumb a")]
    category = crumbs[1].removeprefix("Section:").strip() if len(crumbs) > 1 else ""
    folder = crumbs[2] if len(crumbs) > 2 else ""

    for chrome in body.css("h1.post-title, p.the-article-link"):
        chrome.decompose()
    builder = _Builder()
    _walk(body, builder)
    return _assemble(builder, article_id=article_id, url=url, title=title, category=category, folder=folder)


def _walk(node: LexborNode, out: _Builder) -> None:
    inline: list[str] = []

    def flush() -> None:
        out.line(clean(" ".join(inline)))
        inline.clear()

    for child in node.iter(include_text=True):
        tag = child.tag
        if tag == "-text":
            inline.append(child.text(deep=False) or "")
        elif tag in _SKIP_TAGS:
            continue
        elif tag in _SECTION_TAGS:
            flush()
            out.heading(clean(child.text()))
            _images_in(child, out)
        elif tag == "img":
            flush()
            out.image(child)
        elif tag in _LEAF_TAGS:
            flush()
            out.line(clean(child.text(separator=" ")))
            _images_in(child, out)
        elif tag in {"ul", "ol"}:
            flush()
            for item in child.css("li"):
                if item.parent is not None and item.parent.mem_id == child.mem_id:
                    out.line("- " + clean(item.text(separator=" ")))
            _images_in(child, out)
        elif tag == "table":
            flush()
            for row in child.css("tr"):
                cells = [clean(cell.text(separator=" ")) for cell in row.css("th, td")]
                out.line(" | ".join(c for c in cells if c))
            _images_in(child, out)
        elif tag == "br":
            flush()
        elif tag in {
            "strong",
            "b",
            "em",
            "i",
            "a",
            "span",
            "code",
            "u",
            "sup",
            "sub",
            "small",
            "mark",
        } and not (child.css_first("img, p, div, ul, ol, table, h2, h3")):
            inline.append(child.text(separator=" "))
        else:
            flush()
            _walk(child, out)
    flush()


def _images_in(node: LexborNode, out: _Builder) -> None:
    for img in node.css("img.fr-fic[data-id], img[data-id]"):
        out.image(img)


def _assemble(b: _Builder, *, article_id: str, url: str, title: str, category: str, folder: str) -> ParsedArticle:
    sections = tuple(
        Section(heading=heading, text="\n".join(lines), image_ids=tuple(image_ids))
        for heading, lines, image_ids in b.sections
        if lines or image_ids or heading
    )
    images: list[ImageRef] = []
    for position, raw in enumerate(b.images):
        lines = b.sections[int(raw["section"])][1]
        after_index = int(raw["line"])
        after = lines[after_index] if after_index < len(lines) else ""
        caption_parts = [str(raw["alt"]), str(raw["heading"]), str(raw["before"]), after]
        caption = clean(" | ".join(p for p in caption_parts if p))[:600]
        images.append(
            ImageRef(
                id=str(raw["id"]),
                url=str(raw["url"]),
                change_key=image_change_key(str(raw["url"])),
                position=position,
                caption=caption,
            )
        )
    return ParsedArticle(
        article_id=article_id,
        url=url,
        title=title,
        category=category,
        folder=folder,
        sections=sections,
        images=tuple(images),
    )


def image_change_key(src: str) -> str:
    """Freshdesk appends the upload timestamp as the query string; a new upload gets a new key."""
    parts = urlsplit(src)
    return parts.query or hashlib.sha256(parts.path.encode()).hexdigest()[:16]


def content_hash(article: ParsedArticle) -> str:
    material = "\n".join(
        [article.title, article.category, article.folder, article.body_text]
        + [f"{img.id}:{img.change_key}" for img in article.images]
    )
    return hashlib.sha256(clean(material).encode()).hexdigest()


def is_changelog(article: ParsedArticle) -> bool:
    return any(_CHANGELOG.search(text) for text in (article.title, article.category, article.folder))


_SEGMENT = r"[A-Z][\w'/+-]*(?: (?:[A-Z][\w'/+-]*|&|of))*"
_PATH = re.compile(rf"{_SEGMENT}(?:\s*(?:»|>>|>)\s*{_SEGMENT}){{1,6}}")
_LEADING_VERBS = re.compile(r"^(?:Go to|Open|Navigate to|Click|Visit|Select)\s+")


def setting_path_from_text(text: str) -> str | None:
    """First "A » B » C" / "A >> B" / "A > B" click path in prose, normalised to " > "."""
    match = _PATH.search(text)
    if match is None:
        return None
    parts = [clean(part) for part in re.split(r"»|>>|>", match.group(0))]
    parts[0] = _LEADING_VERBS.sub("", parts[0])
    return " > ".join(p for p in parts if p)


def setting_path_from_title(title: str) -> str | None:
    """Titles like "Settings » General Settings » Social Media Tab" already are the click path."""
    parts = [clean(p) for p in title.split("»")]
    return " > ".join(parts) if len(parts) >= 2 and all(parts) else None
