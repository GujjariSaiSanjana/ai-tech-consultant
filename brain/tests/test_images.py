from __future__ import annotations

from typing import Any

from brain.domain import ImageRef
from brain.ingest.images import AltTextDescriber, ClaudeImageDescriber
from tests.test_llm import _client, _message


async def test_alt_text_describer_uses_caption_and_title() -> None:
    described = await AltTextDescriber().describe(ImageRef("1", "u", "k", 0, "Footer menu"), "Social Media Tab")
    assert described.described_by == "alt_text"
    assert "Social Media Tab" in described.text and "Footer menu" in described.text


async def test_vision_falls_back_to_alt_text_on_error() -> None:
    error = {"type": "error", "error": {"type": "invalid_request_error", "message": "cannot fetch image"}}
    llm, _ = _client(lambda body: (400, error), [])
    image = ImageRef("1", "https://s3/x.png?1", "1", 0, "Footer menu")
    described = await ClaudeImageDescriber(llm).describe(image, "Social Media Tab")
    assert described.described_by == "alt_text" and "Footer menu" in described.text


async def test_vision_sends_image_url_block() -> None:
    requests: list[dict[str, Any]] = []
    llm, _ = _client(
        lambda body: (
            200,
            _message([{"type": "text", "text": "Tabs: General, Social Media."}], model="claude-haiku-5-5"),
        ),
        requests,
    )
    described = await ClaudeImageDescriber(llm).describe(ImageRef("1", "https://s3/x.png?1", "1", 0, ""), "T")
    assert requests[0]["messages"][0]["content"][0] == {
        "type": "image",
        "source": {"type": "url", "url": "https://s3/x.png?1"},
    }
    assert described == described.__class__("Tabs: General, Social Media.", "claude")
