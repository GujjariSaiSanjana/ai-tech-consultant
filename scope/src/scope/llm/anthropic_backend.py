"""Real Claude backend: structured outputs via ``messages.parse`` with a bounded tool loop."""

from __future__ import annotations

import time
from typing import Any, cast

import anthropic
from pydantic import BaseModel, ValidationError

from scope.llm.errors import (
    LLMConfigError,
    LLMError,
    LLMRefusalError,
    LLMSchemaError,
    LLMTruncatedError,
    LLMUnavailableError,
)
from scope.llm.request import build_request
from scope.llm.types import LLMRequest, LLMResult, TokenUsage, ToolBox, ToolCall


def _usage(u: anthropic.types.Usage) -> TokenUsage:
    return TokenUsage(
        input_tokens=u.input_tokens,
        output_tokens=u.output_tokens,
        cache_read_tokens=u.cache_read_input_tokens or 0,
        cache_write_tokens=u.cache_creation_input_tokens or 0,
    )


def translate_error(exc: Exception, agent: str) -> LLMError:
    """Map SDK exceptions (most specific first) onto the typed chain."""
    match exc:
        case anthropic.AuthenticationError() | anthropic.PermissionDeniedError():
            return LLMConfigError(f"credentials rejected: {exc}", agent=agent)
        case anthropic.NotFoundError():
            return LLMConfigError(f"unknown model or endpoint: {exc}", agent=agent)
        case anthropic.RateLimitError():
            return LLMUnavailableError(f"rate limited after retries: {exc}", agent=agent)
        case anthropic.BadRequestError() | anthropic.UnprocessableEntityError():
            return LLMConfigError(f"request rejected: {exc}", agent=agent)
        case anthropic.APIStatusError() if exc.status_code >= 500:
            return LLMUnavailableError(f"server error {exc.status_code}: {exc}", agent=agent)
        case anthropic.APIStatusError():
            return LLMConfigError(f"API error {exc.status_code}: {exc}", agent=agent)
        case anthropic.APIConnectionError():  # includes APITimeoutError
            return LLMUnavailableError(f"network error: {exc}", agent=agent)
        case _:
            return LLMError(f"unexpected error: {exc!r}", agent=agent)


class AnthropicBackend:
    def __init__(self, client: anthropic.AsyncAnthropic, *, max_tool_rounds: int) -> None:
        self._client = client
        self._max_tool_rounds = max_tool_rounds

    async def complete[T: BaseModel](
        self, req: LLMRequest, schema: type[T], tools: ToolBox | None
    ) -> LLMResult[T]:
        body = build_request(req, tools)
        messages: list[dict[str, Any]] = body.pop("messages")
        usage = TokenUsage()
        calls: list[ToolCall] = []
        started = time.perf_counter()
        for _round in range(self._max_tool_rounds + 1):
            try:
                resp = await self._client.messages.parse(
                    **body, messages=cast(Any, messages), output_format=schema
                )
            except anthropic.APIError as exc:
                raise translate_error(exc, req.agent) from exc
            usage = usage + _usage(resp.usage)
            if resp.stop_reason == "refusal":
                category = resp.stop_details.category if resp.stop_details else None
                raise LLMRefusalError(
                    f"model refused ({category})", agent=req.agent, category=category
                )
            if resp.stop_reason in ("max_tokens", "model_context_window_exceeded"):
                raise LLMTruncatedError(f"output truncated: {resp.stop_reason}", agent=req.agent)
            if resp.stop_reason == "tool_use" and tools is not None:
                results: list[dict[str, Any]] = []
                for block in resp.content:
                    if block.type != "tool_use":
                        continue
                    tool_input = cast(dict[str, Any], block.input)
                    out, is_error = await tools.execute(block.name, tool_input)
                    calls.append(ToolCall(block.name, tool_input, out, is_error))
                    results.append(
                        {
                            "type": "tool_result",
                            "tool_use_id": block.id,
                            "content": out,
                            "is_error": is_error,
                        }
                    )
                messages.append(
                    {
                        "role": "assistant",
                        "content": [b.to_dict(exclude_none=True) for b in resp.content],
                    }
                )
                messages.append({"role": "user", "content": results})
                continue
            try:
                parsed = resp.parsed_output
            except ValidationError as exc:
                raise LLMSchemaError(f"invalid structured output: {exc}", agent=req.agent) from exc
            if parsed is None:
                raise LLMSchemaError(
                    f"no structured output (stop_reason={resp.stop_reason})", agent=req.agent
                )
            return LLMResult(
                output=parsed,
                model=resp.model,
                usage=usage,
                latency_ms=int((time.perf_counter() - started) * 1000),
                stop_reason=resp.stop_reason,
                request_id=resp._request_id,
                tool_calls=calls,
            )
        raise LLMSchemaError(
            f"no answer after {self._max_tool_rounds} tool rounds", agent=req.agent
        )
