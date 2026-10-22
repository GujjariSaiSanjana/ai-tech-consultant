from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Literal, Protocol

from anthropic.types import ImageBlockParam, MessageParam, TextBlockParam

from brain.domain import ImageRef
from brain.llm.client import LLMClient, LLMError

log = logging.getLogger(__name__)
DescribedBy = Literal["alt_text", "claude"]

VISION_PROMPT = """This screenshot is from the Brilliant Directories help article "{title}".
Nearby text in the article: {caption}

Describe it so someone searching the help center can find it. Include, as plain text:
- every readable UI label, button, tab and menu item, verbatim;
- the menu or navigation path shown (for example "Settings > Members > ..."), if any;
- field names with their visible values or toggle states;
- which step of the article this screenshot proves.
Do not guess at anything you cannot read. Four to eight short sentences, no preamble."""


@dataclass(frozen=True, slots=True)
class ImageDescription:
    text: str
    described_by: DescribedBy


class ImageDescriber(Protocol):
    @property
    def kind(self) -> DescribedBy: ...

    async def describe(self, image: ImageRef, article_title: str) -> ImageDescription: ...


def alt_text_description(image: ImageRef, article_title: str) -> ImageDescription:
    context = image.caption or "no caption"
    return ImageDescription(f"Screenshot from '{article_title}'. Context: {context}", "alt_text")


class AltTextDescriber:
    """No vision model available: alt text plus the surrounding caption."""

    @property
    def kind(self) -> DescribedBy:
        return "alt_text"

    async def describe(self, image: ImageRef, article_title: str) -> ImageDescription:
        return alt_text_description(image, article_title)


class ClaudeImageDescriber:
    def __init__(self, llm: LLMClient) -> None:
        self._llm = llm

    @property
    def kind(self) -> DescribedBy:
        return "claude"

    async def describe(self, image: ImageRef, article_title: str) -> ImageDescription:
        content: list[ImageBlockParam | TextBlockParam] = [
            ImageBlockParam(type="image", source={"type": "url", "url": image.url}),
            TextBlockParam(type="text", text=VISION_PROMPT.format(title=article_title, caption=image.caption)),
        ]
        messages: list[MessageParam] = [{"role": "user", "content": content}]
        try:
            message, _ = await self._llm.create("vision", messages=messages)
        except LLMError as exc:
            log.warning("vision failed for image %s (%s); using alt text", image.id, exc)
            return alt_text_description(image, article_title)
        text = " ".join(b.text for b in message.content if b.type == "text").strip()
        return ImageDescription(text, "claude") if text else alt_text_description(image, article_title)
