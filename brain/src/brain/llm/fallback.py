"""Fallback LLMs behind Claude: OpenAI-compatible Chat Completions (Gemini, NVIDIA NIM) over plain httpx.

Used only when Claude fails with a retryable error after the SDK's own retries, or when no
ANTHROPIC_API_KEY is set. Never on a refusal, a bad request or a schema problem. Anthropic-style
requests are flattened to text (cache_control dropped). Document blocks become numbered `[doc N]`
excerpts answered as JSON; only quotes found verbatim in their document survive, and come back as
`char_location` citations on an ordinary `Message`, so callers cannot tell the difference.
Vision falls back to providers that take `image_url` parts (Gemini). Message Batches stay Claude-only.
"""

from __future__ import annotations

import json
import logging
import re
import time
from collections.abc import Awaitable, Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

import httpx
from anthropic.types import Message, MessageParam, TextBlockParam
from pydantic import BaseModel, ValidationError

from brain.config import Settings
from brain.llm.answerer import NO_ANSWER
from brain.llm.client import CallRecord, CallRecorder, LLMError, LLMRefusalError, Price, Purpose, Route, Usage

log = logging.getLogger(__name__)

CITED_INSTRUCTIONS = f"""The documents are given as numbered excerpts, each starting with [doc N].
Reply with one JSON object only: {{"answer_text": "...", "citations": [{{"document_index": N, \
"cited_text": "..."}}]}}.
- cited_text is copied verbatim from document N: an exact substring of it, a sentence or line, never paraphrased.
- Cite every document the answer relies on.
- If you must reply {NO_ANSWER}, set answer_text to exactly {NO_ANSWER} and citations to []."""


@dataclass(frozen=True, slots=True)
class ProviderConfig:
    name: str
    api_key: str
    model: str
    base_url: str
    price: Price
    vision: bool  # accepts image_url content parts


def provider_configs(settings: Settings) -> list[ProviderConfig]:
    """Providers in LLM_FALLBACK_ORDER; keyless, unknown and repeated names are skipped."""
    known = {
        "gemini": ProviderConfig(
            "gemini",
            settings.gemini_api_key or "",
            settings.gemini_model,
            settings.gemini_base_url,
            Price(settings.gemini_input_price, settings.gemini_output_price, 0.0, 0.0),
            vision=True,
        ),
        "nvidia": ProviderConfig(
            "nvidia",
            settings.nvidia_api_key or "",
            settings.nvidia_model,
            settings.nvidia_base_url,
            Price(settings.nvidia_input_price, settings.nvidia_output_price, 0.0, 0.0),
            vision=False,
        ),
    }
    out: list[ProviderConfig] = []
    for name in dict.fromkeys(settings.fallback_order):
        config = known.get(name)
        if config is None:
            log.warning("LLM_FALLBACK_ORDER: unknown provider %r ignored", name)
        elif config.api_key:
            out.append(config)
    return out


class ProviderError(LLMError):
    """A fallback provider call failed; `status` is the HTTP status when there was one."""

    def __init__(self, message: str, *, retryable: bool, status: int | None = None) -> None:
        super().__init__(message, retryable=retryable)
        self.status = status


@dataclass(frozen=True, slots=True)
class Completion:
    text: str
    finish_reason: str | None
    refused: bool
    input_tokens: int
    output_tokens: int
    request_id: str | None


class OpenAICompatProvider:
    """POST {base_url}/chat/completions with Bearer auth. No retries: the next provider is the retry."""

    def __init__(self, config: ProviderConfig, http: httpx.AsyncClient) -> None:
        self.config = config
        self._http = http

    async def complete(
        self, messages: list[dict[str, Any]], *, max_tokens: int, response_format: dict[str, Any] | None = None
    ) -> Completion:
        name = self.config.name
        body: dict[str, Any] = {"model": self.config.model, "messages": messages, "max_tokens": max_tokens}
        if response_format is not None:
            body["response_format"] = response_format
        try:
            response = await self._http.post(
                f"{self.config.base_url.rstrip('/')}/chat/completions",
                json=body,
                headers={"Authorization": f"Bearer {self.config.api_key}"},
            )
        except httpx.TimeoutException as exc:
            raise ProviderError(f"{name} timed out", retryable=True) from exc
        except httpx.HTTPError as exc:
            raise ProviderError(f"could not reach {name}: {type(exc).__name__}", retryable=True) from exc
        status = response.status_code
        if status >= 400:
            retryable = status in (408, 409, 429) or status >= 500
            raise ProviderError(f"{name} error {status}: {response.text[:300]}", retryable=retryable, status=status)
        try:
            data = response.json()
            choice = data["choices"][0]
            message = choice.get("message") or {}
            usage = data.get("usage") or {}
            return Completion(
                text=_content_text(message.get("content")),
                finish_reason=choice.get("finish_reason"),
                refused=choice.get("finish_reason") == "content_filter" or bool(message.get("refusal")),
                input_tokens=int(usage.get("prompt_tokens") or 0),
                output_tokens=int(usage.get("completion_tokens") or 0),
                request_id=data.get("id"),
            )
        except (ValueError, KeyError, IndexError, TypeError, AttributeError) as exc:
            raise ProviderError(f"{name} sent an unreadable response", retryable=False, status=status) from exc


class FallbackChain:
    """Tries each provider in order; any provider failure moves on, a refusal stops the chain."""

    def __init__(self, providers: Sequence[OpenAICompatProvider], recorder: CallRecorder) -> None:
        self._providers = list(providers)
        self._recorder = recorder

    def serving(self, purpose: Purpose) -> list[OpenAICompatProvider]:
        return [p for p in self._providers if p.config.vision or purpose != "vision"]

    def model_for(self, purpose: Purpose) -> str | None:
        serving = self.serving(purpose)
        return serving[0].config.model if serving else None

    async def create(
        self,
        purpose: Purpose,
        route: Route,
        *,
        messages: Sequence[MessageParam],
        system: str | Iterable[TextBlockParam] | None,
        original: LLMError | None,
    ) -> tuple[Message, Usage]:
        docs = cited_documents(messages)

        async def attempt(provider: OpenAICompatProvider) -> tuple[Message, Usage]:
            if not docs:
                chat = chat_messages(system, messages)
                text, completion, usage = await self._call(
                    provider, purpose, route, chat, response_format=None, read=lambda c: c.text
                )
                return to_message(provider.config.model, completion, text, []), usage
            answer, completion, usage = await self._structured(
                provider, purpose, route, CitedAnswer, chat_messages(system, messages, extra_system=CITED_INSTRUCTIONS)
            )
            if answer.answer_text.strip().startswith(NO_ANSWER):
                return to_message(provider.config.model, completion, NO_ANSWER, []), usage
            citations = grounded_citations(answer, docs)
            return to_message(provider.config.model, completion, answer.answer_text, citations), usage

        return await self._run(purpose, attempt, original)

    async def parse[T: BaseModel](
        self,
        purpose: Purpose,
        route: Route,
        output_format: type[T],
        *,
        messages: Sequence[MessageParam],
        system: str | None,
        original: LLMError | None,
    ) -> tuple[T, Usage]:
        async def attempt(provider: OpenAICompatProvider) -> tuple[T, Usage]:
            result, _, usage = await self._structured(
                provider, purpose, route, output_format, chat_messages(system, messages)
            )
            return result, usage

        return await self._run(purpose, attempt, original)

    async def _run[R](
        self, purpose: Purpose, attempt: Callable[[OpenAICompatProvider], Awaitable[R]], original: LLMError | None
    ) -> R:
        last: LLMError | None = None
        for provider in self.serving(purpose):
            try:
                result = await attempt(provider)
            except LLMRefusalError:
                raise
            except LLMError as exc:
                log.warning("fallback %s failed for %s: %s", provider.config.name, purpose, exc)
                last = exc
                continue
            log.info("%s served by fallback %s (%s)", purpose, provider.config.name, original or "no Claude key")
            return result
        if original is not None:
            raise original
        raise last or LLMError(f"no LLM provider configured for {purpose}", retryable=False)

    async def _structured[T: BaseModel](
        self,
        provider: OpenAICompatProvider,
        purpose: Purpose,
        route: Route,
        output_format: type[T],
        chat: list[dict[str, Any]],
    ) -> tuple[T, Completion, Usage]:
        """json_schema first; a provider that rejects it (400) gets one json_object retry, schema in the prompt."""
        schema = output_format.model_json_schema()

        def read(completion: Completion) -> T:
            return validate_json(provider.config.name, completion.text, output_format)

        strict = {
            "type": "json_schema",
            "json_schema": {"name": output_format.__name__, "schema": schema, "strict": True},
        }
        try:
            return await self._call(provider, purpose, route, chat, response_format=strict, read=read)
        except ProviderError as exc:
            if exc.status != 400:
                raise
        hint = f"Reply with one JSON object only, matching this JSON Schema:\n{json.dumps(schema)}"
        retry = with_system(chat, hint)
        return await self._call(provider, purpose, route, retry, response_format={"type": "json_object"}, read=read)

    async def _call[R](
        self,
        provider: OpenAICompatProvider,
        purpose: Purpose,
        route: Route,
        chat: list[dict[str, Any]],
        *,
        response_format: dict[str, Any] | None,
        read: Callable[[Completion], R],
    ) -> tuple[R, Completion, Usage]:
        """One HTTP call, logged whatever happens; `read` turns the completion into the caller's value."""
        started = time.perf_counter()
        completion: Completion | None = None
        try:
            completion = await provider.complete(chat, max_tokens=route.max_tokens, response_format=response_format)
            if completion.refused:
                raise LLMRefusalError(f"{provider.config.name}:content_filter")
            result = read(completion)
        except LLMError as exc:
            await self._record(purpose, provider.config, started, completion=completion, exc=exc)
            raise
        usage = await self._record(purpose, provider.config, started, completion=completion, exc=None)
        return result, completion, usage

    async def _record(
        self,
        purpose: Purpose,
        config: ProviderConfig,
        started: float,
        *,
        completion: Completion | None,
        exc: LLMError | None,
    ) -> Usage:
        price = config.price
        tokens_in = completion.input_tokens if completion else 0
        tokens_out = completion.output_tokens if completion else 0
        usage = Usage(tokens_in, tokens_out, (tokens_in * price.input + tokens_out * price.output) / 1_000_000)
        error = None
        if isinstance(exc, LLMRefusalError):
            error = f"refusal:{exc.category}"
        elif exc is not None:
            error = f"{type(exc).__name__}: {exc}"[:500]
        await self._recorder.record_llm_call(
            CallRecord(
                purpose=purpose,
                model=config.model,
                input_tokens=tokens_in,
                output_tokens=tokens_out,
                cache_read_tokens=0,
                cache_write_tokens=0,
                cost_usd=usage.cost_usd,
                latency_ms=int((time.perf_counter() - started) * 1000),
                stop_reason=completion.finish_reason if completion else None,
                request_id=completion.request_id if completion else None,
                error=error,
            )
        )
        return usage


def build_fallback(settings: Settings, http: httpx.AsyncClient, recorder: CallRecorder) -> FallbackChain | None:
    configs = provider_configs(settings)
    return FallbackChain([OpenAICompatProvider(c, http) for c in configs], recorder) if configs else None


# ---- request and response mapping ------------------------------------------------------------------


class CitedQuote(BaseModel):
    document_index: int
    cited_text: str


class CitedAnswer(BaseModel):
    answer_text: str
    citations: list[CitedQuote]


@dataclass(frozen=True, slots=True)
class CitedDocument:
    title: str
    text: str


def _block(block: Any) -> Mapping[str, Any]:
    return block if isinstance(block, Mapping) else block.model_dump()


def cited_documents(messages: Sequence[MessageParam]) -> list[CitedDocument]:
    """Text document blocks with citations enabled, in order: their index is Claude's `document_index`."""
    out: list[CitedDocument] = []
    for message in messages:
        content = message["content"]
        if isinstance(content, str):
            continue
        for raw in content:
            block = _block(raw)
            if block.get("type") == "document" and (block.get("citations") or {}).get("enabled"):
                source = block.get("source") or {}
                out.append(CitedDocument(str(block.get("title") or ""), str(source.get("data") or "")))
    return out


def chat_messages(
    system: str | Iterable[TextBlockParam] | None, messages: Sequence[MessageParam], *, extra_system: str = ""
) -> list[dict[str, Any]]:
    """Anthropic request -> Chat Completions messages. Text-only content collapses to one string."""
    system_text = system if isinstance(system, str) else "\n\n".join(_block(b)["text"] for b in system or [])
    out: list[dict[str, Any]] = []
    if prompt := "\n\n".join(p for p in (system_text, extra_system) if p):
        out.append({"role": "system", "content": prompt})
    doc_index = 0
    for message in messages:
        content = message["content"]
        if isinstance(content, str):
            out.append({"role": message["role"], "content": content})
            continue
        parts: list[dict[str, Any]] = []
        for raw in content:
            block = _block(raw)
            kind = block.get("type")
            if kind == "text":
                parts.append({"type": "text", "text": block["text"]})
            elif kind == "document" and (block.get("source") or {}).get("type") == "text":
                context = f"\n({block['context']})" if block.get("context") else ""
                text = f"[doc {doc_index}] {block.get('title') or ''}{context}\n{block['source']['data']}"
                parts.append({"type": "text", "text": text})
                doc_index += 1
            elif kind == "image":
                parts.append({"type": "image_url", "image_url": {"url": _image_url(block["source"])}})
            else:
                raise ProviderError(f"content block {kind!r} has no fallback mapping", retryable=False)
        if all(p["type"] == "text" for p in parts):
            out.append({"role": message["role"], "content": "\n\n".join(p["text"] for p in parts)})
        else:
            out.append({"role": message["role"], "content": parts})
    return out


def with_system(chat: list[dict[str, Any]], extra: str) -> list[dict[str, Any]]:
    if chat and chat[0]["role"] == "system":
        return [{"role": "system", "content": f"{chat[0]['content']}\n\n{extra}"}, *chat[1:]]
    return [{"role": "system", "content": extra}, *chat]


def _image_url(source: Mapping[str, Any]) -> str:
    if source.get("type") == "url":
        return str(source["url"])
    if source.get("type") == "base64":
        return f"data:{source['media_type']};base64,{source['data']}"
    raise ProviderError(f"image source {source.get('type')!r} has no fallback mapping", retryable=False)


def _content_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):  # some servers return content parts
        return "".join(str(p.get("text", "")) for p in content if isinstance(p, dict))
    return ""


_FENCE = re.compile(r"^```(?:json)?\s*|\s*```$")


def validate_json[T: BaseModel](provider: str, text: str, output_format: type[T]) -> T:
    """Pydantic has the last word on every fallback structured output; a mismatch is not retryable."""
    try:
        return output_format.model_validate_json(_FENCE.sub("", text.strip()))
    except ValidationError as exc:
        raise ProviderError(
            f"{provider} output failed {output_format.__name__} validation: {exc.error_count()} error(s)",
            retryable=False,
        ) from exc


def grounded_citations(answer: CitedAnswer, docs: Sequence[CitedDocument]) -> list[dict[str, Any]]:
    """Keep a quote only if it really is a substring of the document it names."""
    out: list[dict[str, Any]] = []
    for cite in answer.citations:
        quote = cite.cited_text.strip()
        if not quote or not 0 <= cite.document_index < len(docs):
            continue
        doc = docs[cite.document_index]
        start = doc.text.find(quote)
        if start < 0:
            continue
        out.append(
            {
                "type": "char_location",
                "cited_text": quote,
                "document_index": cite.document_index,
                "document_title": doc.title,
                "start_char_index": start,
                "end_char_index": start + len(quote),
            }
        )
    return out


def to_message(model: str, completion: Completion, text: str, citations: list[dict[str, Any]]) -> Message:
    return Message.model_validate(
        {
            "id": completion.request_id or "fallback",
            "type": "message",
            "role": "assistant",
            "model": model,
            "content": [{"type": "text", "text": text, "citations": citations or None}],
            "stop_reason": "max_tokens" if completion.finish_reason == "length" else "end_turn",
            "stop_sequence": None,
            "usage": {"input_tokens": completion.input_tokens, "output_tokens": completion.output_tokens},
        }
    )
