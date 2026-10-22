"""The single gateway to the Claude API: model routing, cost accounting, typed errors, call logging.

API constraints this module encodes (Messages API version 2026-10-21, 5.5-generation models):
no temperature/top_p/top_k, no thinking budget_tokens, no forced tool_choice; JSON comes from
structured outputs (`messages.parse`), depth from `output_config.effort`.

Retryable Claude failures (and a missing ANTHROPIC_API_KEY) fall back to `llm.fallback`; batches do not.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable, Iterable, Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal, Protocol

import anthropic
from anthropic.types import Message, MessageParam, OutputConfigParam, ParsedMessage, TextBlockParam
from anthropic.types.message_create_params import MessageCreateParamsNonStreaming
from anthropic.types.messages.batch_create_params import Request
from pydantic import BaseModel

from brain.config import Settings

if TYPE_CHECKING:
    from brain.llm.fallback import FallbackChain

log = logging.getLogger(__name__)

Purpose = Literal["answer", "classify", "vision", "context"]
Effort = Literal["low", "medium", "high"]


@dataclass(frozen=True, slots=True)
class Price:
    """USD per million tokens."""

    input: float
    output: float
    cache_read: float
    cache_write: float


PRICES: dict[str, Price] = {
    "claude-haiku-5-5": Price(input=0.10, output=0.50, cache_read=0.01, cache_write=0.125),
    "claude-sonnet-5-5": Price(input=2.0, output=10.0, cache_read=0.20, cache_write=2.5),
    "claude-opus-5-5": Price(input=4.0, output=20.0, cache_read=0.20, cache_write=5.0),
}


BATCH_DISCOUNT = 0.5  # Message Batches bill every token at half price


@dataclass(frozen=True, slots=True)
class Route:
    model: str
    max_tokens: int
    effort: Effort


def routes(settings: Settings) -> dict[Purpose, Route]:
    return {
        "answer": Route(settings.answer_model, max_tokens=4096, effort="medium"),
        "classify": Route(settings.classify_model, max_tokens=2048, effort="low"),
        "vision": Route(settings.vision_model, max_tokens=2048, effort="low"),
        "context": Route(settings.classify_model, max_tokens=1024, effort="low"),
    }


@dataclass(slots=True)
class Usage:
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float = 0.0

    def add(self, other: Usage) -> None:
        self.input_tokens += other.input_tokens
        self.output_tokens += other.output_tokens
        self.cost_usd += other.cost_usd


@dataclass(frozen=True, slots=True)
class CallRecord:
    purpose: Purpose
    model: str
    input_tokens: int
    output_tokens: int
    cache_read_tokens: int
    cache_write_tokens: int
    cost_usd: float
    latency_ms: int
    stop_reason: str | None
    request_id: str | None
    error: str | None


class CallRecorder(Protocol):
    async def record_llm_call(self, record: CallRecord) -> None: ...


class LLMError(Exception):
    """A Claude call failed after the SDK's own retries; `retryable` says whether trying later can help."""

    def __init__(self, message: str, *, retryable: bool) -> None:
        super().__init__(message)
        self.retryable = retryable


class LLMRefusalError(LLMError):
    def __init__(self, category: str | None) -> None:
        super().__init__(f"model refused (category={category})", retryable=False)
        self.category = category


def cost_usd(model: str, message: Message) -> float:
    price = PRICES.get(model)
    if price is None:
        return 0.0
    u = message.usage
    return (
        u.input_tokens * price.input
        + u.output_tokens * price.output
        + (u.cache_read_input_tokens or 0) * price.cache_read
        + (u.cache_creation_input_tokens or 0) * price.cache_write
    ) / 1_000_000


class LLMClient:
    """Claude first; `fallback` serves retryable Claude failures, or everything when `client` is None."""

    def __init__(
        self,
        client: anthropic.AsyncAnthropic | None,
        settings: Settings,
        recorder: CallRecorder,
        fallback: FallbackChain | None = None,
    ) -> None:
        self._client = client
        self._routes = routes(settings)
        self._recorder = recorder
        self._fallback = fallback

    def model_for(self, purpose: Purpose) -> str:
        if self._client is None and self._fallback is not None and (model := self._fallback.model_for(purpose)):
            return model
        return self._routes[purpose].model

    def can_serve(self, purpose: Purpose) -> bool:
        return self._client is not None or (self._fallback is not None and bool(self._fallback.serving(purpose)))

    def _keyless_fallback(self) -> FallbackChain:
        if self._fallback is None:
            raise LLMError("no ANTHROPIC_API_KEY and no fallback provider configured", retryable=False)
        return self._fallback

    async def create(
        self,
        purpose: Purpose,
        *,
        messages: Iterable[MessageParam],
        system: str | list[TextBlockParam] | None = None,
    ) -> tuple[Message, Usage]:
        route = self._routes[purpose]
        messages = list(messages)
        if self._client is None:
            return await self._keyless_fallback().create(
                purpose, route, messages=messages, system=system, original=None
            )
        started = time.perf_counter()
        try:
            message = await self._client.messages.create(
                model=route.model,
                max_tokens=route.max_tokens,
                messages=messages,
                output_config=OutputConfigParam(effort=route.effort),
                system=system if system is not None else anthropic.omit,
            )
        except anthropic.APIError as exc:
            await self._record_failure(purpose, route.model, started, exc)
            error = _translate(exc)
            if self._fallback is None or not error.retryable:  # never on a bad request or schema problem
                raise error from exc
            return await self._fallback.create(purpose, route, messages=messages, system=system, original=error)
        return await self._finish(purpose, route.model, started, message)

    async def parse[T: BaseModel](
        self,
        purpose: Purpose,
        output_format: type[T],
        *,
        messages: Iterable[MessageParam],
        system: str | None = None,
    ) -> tuple[T, Usage]:
        route = self._routes[purpose]
        messages = list(messages)
        if self._client is None:
            return await self._keyless_fallback().parse(
                purpose, route, output_format, messages=messages, system=system, original=None
            )
        started = time.perf_counter()
        try:
            parsed: ParsedMessage[T] = await self._client.messages.parse(
                model=route.model,
                max_tokens=route.max_tokens,
                messages=messages,
                output_format=output_format,
                output_config=OutputConfigParam(effort=route.effort),
                system=system if system is not None else anthropic.omit,
            )
        except anthropic.APIError as exc:
            await self._record_failure(purpose, route.model, started, exc)
            error = _translate(exc)
            if self._fallback is None or not error.retryable:
                raise error from exc
            return await self._fallback.parse(
                purpose, route, output_format, messages=messages, system=system, original=error
            )
        _, usage = await self._finish(purpose, route.model, started, parsed)
        if parsed.parsed_output is None:
            raise LLMError(f"no structured output (stop_reason={parsed.stop_reason})", retryable=False)
        return parsed.parsed_output, usage

    async def submit_batch(self, purpose: Purpose, requests: Sequence[tuple[str, list[MessageParam]]]) -> str:
        """Queue many same-purpose requests as one Message Batch; returns the batch id. Claude only."""
        route = self._routes[purpose]
        client = self._batch_client()
        try:
            batch = await client.messages.batches.create(
                requests=[
                    Request(
                        custom_id=custom_id,
                        params=MessageCreateParamsNonStreaming(
                            model=route.model,
                            max_tokens=route.max_tokens,
                            messages=messages,
                            output_config=OutputConfigParam(effort=route.effort),
                        ),
                    )
                    for custom_id, messages in requests
                ]
            )
        except anthropic.APIError as exc:
            raise _translate(exc) from exc
        return batch.id

    async def collect_batch(
        self,
        purpose: Purpose,
        batch_id: str,
        *,
        poll_s: float = 30.0,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> dict[str, Message]:
        """Wait for a batch to end; succeeded, non-refused messages by custom_id. Each result is logged."""
        model = self._routes[purpose].model
        client = self._batch_client()
        try:
            while (await client.messages.batches.retrieve(batch_id)).processing_status != "ended":
                await sleep(poll_s)
            out: dict[str, Message] = {}
            async for item in await client.messages.batches.results(batch_id):
                if item.result.type != "succeeded":
                    await self._recorder.record_llm_call(
                        CallRecord(
                            purpose, model, 0, 0, 0, 0, 0.0, 0, None, item.custom_id, f"batch {item.result.type}"
                        )
                    )
                    continue
                message = item.result.message
                refused = message.stop_reason == "refusal"
                await self._recorder.record_llm_call(
                    CallRecord(
                        purpose=purpose,
                        model=model,
                        input_tokens=message.usage.input_tokens,
                        output_tokens=message.usage.output_tokens,
                        cache_read_tokens=message.usage.cache_read_input_tokens or 0,
                        cache_write_tokens=message.usage.cache_creation_input_tokens or 0,
                        cost_usd=cost_usd(model, message) * BATCH_DISCOUNT,
                        latency_ms=0,
                        stop_reason=message.stop_reason,
                        request_id=item.custom_id,
                        error="refusal" if refused else None,
                    )
                )
                if not refused:
                    out[item.custom_id] = message
        except anthropic.APIError as exc:
            raise _translate(exc) from exc
        return out

    def _batch_client(self) -> anthropic.AsyncAnthropic:
        if self._client is None:
            raise LLMError("Message Batches need ANTHROPIC_API_KEY (no fallback for batches)", retryable=False)
        return self._client

    async def _finish(self, purpose: Purpose, model: str, started: float, message: Message) -> tuple[Message, Usage]:
        latency_ms = int((time.perf_counter() - started) * 1000)
        usage = Usage(message.usage.input_tokens, message.usage.output_tokens, cost_usd(model, message))
        refusal = message.stop_reason == "refusal"
        category = message.stop_details.category if refusal and message.stop_details else None
        await self._recorder.record_llm_call(
            CallRecord(
                purpose=purpose,
                model=model,
                input_tokens=usage.input_tokens,
                output_tokens=usage.output_tokens,
                cache_read_tokens=message.usage.cache_read_input_tokens or 0,
                cache_write_tokens=message.usage.cache_creation_input_tokens or 0,
                cost_usd=usage.cost_usd,
                latency_ms=latency_ms,
                stop_reason=message.stop_reason,
                request_id=message._request_id,
                error=f"refusal:{category}" if refusal else None,
            )
        )
        if refusal:
            raise LLMRefusalError(category)
        return message, usage

    async def _record_failure(self, purpose: Purpose, model: str, started: float, exc: Exception) -> None:
        await self._recorder.record_llm_call(
            CallRecord(
                purpose=purpose,
                model=model,
                input_tokens=0,
                output_tokens=0,
                cache_read_tokens=0,
                cache_write_tokens=0,
                cost_usd=0.0,
                latency_ms=int((time.perf_counter() - started) * 1000),
                stop_reason=None,
                request_id=getattr(exc, "request_id", None),
                error=f"{type(exc).__name__}: {exc}"[:500],
            )
        )


def _translate(exc: anthropic.APIError) -> LLMError:
    """Most specific first; the SDK has already retried 408/409/429/5xx/connection errors."""
    match exc:
        case anthropic.AuthenticationError() | anthropic.PermissionDeniedError():
            return LLMError(f"Claude credentials rejected: {exc.message}", retryable=False)
        case anthropic.NotFoundError():
            return LLMError(f"unknown model or endpoint: {exc.message}", retryable=False)
        case anthropic.BadRequestError() | anthropic.UnprocessableEntityError():
            return LLMError(f"request rejected by Claude API: {exc.message}", retryable=False)
        case anthropic.RateLimitError():
            return LLMError("Claude API rate limit reached", retryable=True)
        case anthropic.APIStatusError() if exc.status_code >= 500:
            return LLMError(f"Claude API unavailable ({exc.status_code})", retryable=True)
        case anthropic.APIStatusError():
            return LLMError(f"Claude API error {exc.status_code}: {exc.message}", retryable=False)
        case anthropic.APIConnectionError():
            return LLMError("could not reach the Claude API", retryable=True)
        case _:
            return LLMError(str(exc), retryable=False)


def build_anthropic(settings: Settings) -> anthropic.AsyncAnthropic | None:
    if not settings.anthropic_api_key:
        return None
    return anthropic.AsyncAnthropic(api_key=settings.anthropic_api_key, max_retries=3, timeout=120.0)
