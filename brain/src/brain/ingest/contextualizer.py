"""Anthropic-style contextual retrieval: a one-sentence situating prefix per chunk.

The whole article is sent once per chunk as a cached prefix, so only the first chunk of an
article pays full input price.
"""

from __future__ import annotations

from anthropic.types import MessageParam, TextBlockParam

from brain.llm.client import LLMClient

CONTEXT_PROMPT = """Here is a chunk from the article above:
<chunk>
{chunk}
</chunk>
Write one short sentence that situates this chunk within the whole article to improve search retrieval \
(what feature, which admin screen, which task). Reply with the sentence only."""


class Contextualizer:
    def __init__(self, llm: LLMClient) -> None:
        self._llm = llm

    async def situate(self, title: str, article_text: str, chunk_text: str) -> str:
        content: list[TextBlockParam] = [
            TextBlockParam(
                type="text",
                text=f'<article title="{title}">\n{article_text}\n</article>',
                cache_control={"type": "ephemeral"},
            ),
            TextBlockParam(type="text", text=CONTEXT_PROMPT.format(chunk=chunk_text)),
        ]
        messages: list[MessageParam] = [{"role": "user", "content": content}]
        message, _ = await self._llm.create("context", messages=messages)
        return " ".join(b.text for b in message.content if b.type == "text").strip()
