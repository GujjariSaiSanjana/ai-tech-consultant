"""Claude first, then OpenAI-compatible fallbacks, for transient failures only."""

from __future__ import annotations

from collections.abc import Sequence

from pydantic import BaseModel

from scope.llm.errors import LLMConfigError, LLMUnavailableError
from scope.llm.types import LLMBackend, LLMRequest, LLMResult, ToolBox


class FallbackBackend:
    """Try ``primary``, then each fallback in order, moving on only on ``LLMUnavailableError``.

    Refusals, schema, truncation and config errors are the answer and are raised as they are.
    ``primary`` is None when Claude has no key. If every backend is unavailable, the first
    error (Claude's when it ran) is re-raised.
    """

    def __init__(self, primary: LLMBackend | None, fallbacks: Sequence[LLMBackend]) -> None:
        self.primary = primary
        self.fallbacks = tuple(fallbacks)

    async def complete[T: BaseModel](
        self, req: LLMRequest, schema: type[T], tools: ToolBox | None
    ) -> LLMResult[T]:
        original: LLMUnavailableError | None = None
        for backend in (self.primary, *self.fallbacks):
            if backend is None:
                continue
            try:
                return await backend.complete(req, schema, tools)
            except LLMUnavailableError as exc:
                original = original or exc
        if original is None:
            raise LLMConfigError("no LLM backend configured", agent=req.agent)
        raise original
