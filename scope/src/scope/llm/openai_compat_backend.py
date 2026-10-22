"""Fallback backend for OpenAI-compatible Chat Completions APIs (Gemini, NVIDIA NIM) over httpx.

Mirrors ``AnthropicBackend``: structured output validated with pydantic, the same bounded tool
loop and the same typed errors. No sampling parameters are sent. ``response_format`` is a strict
``json_schema``; a provider that rejects it with 400 gets one retry as ``json_object`` with the
schema in the system prompt, and later calls skip straight to ``json_object``.
"""

from __future__ import annotations

import asyncio
import json
import re
import time
from typing import Any

import httpx
from pydantic import BaseModel, ValidationError

from scope.config import Provider
from scope.llm.errors import (
    LLMConfigError,
    LLMError,
    LLMRefusalError,
    LLMSchemaError,
    LLMTruncatedError,
    LLMUnavailableError,
)
from scope.llm.request import system_blocks
from scope.llm.types import LLMRequest, LLMResult, TokenUsage, ToolBox, ToolCall


class RequestRejectedError(LLMConfigError):
    """HTTP 400/422: the provider rejected the request body."""


def translate_status(status: int, detail: str, agent: str) -> LLMError:
    """Map an HTTP error status onto the typed chain."""
    if status in (401, 403):
        return LLMConfigError(f"credentials rejected ({status}): {detail}", agent=agent)
    if status == 404:
        return LLMConfigError(f"unknown model or endpoint: {detail}", agent=agent)
    if status in (400, 422):
        return RequestRejectedError(f"request rejected ({status}): {detail}", agent=agent)
    if status == 429:
        return LLMUnavailableError(f"rate limited after retries: {detail}", agent=agent)
    if status >= 500:
        return LLMUnavailableError(f"server error {status}: {detail}", agent=agent)
    return LLMConfigError(f"API error {status}: {detail}", agent=agent)


def openai_tools(tools: ToolBox) -> list[dict[str, Any]]:
    """ToolBox definitions (Anthropic shape) as OpenAI ``tools``, sorted by name."""
    return [
        {
            "type": "function",
            "function": {
                "name": d["name"],
                "description": d.get("description", ""),
                "parameters": d["input_schema"],
            },
        }
        for d in sorted(tools.definitions(), key=lambda t: str(t["name"]))
    ]


def json_schema_format(schema: type[BaseModel]) -> dict[str, Any]:
    return {
        "type": "json_schema",
        "json_schema": {
            "name": re.sub(r"[^a-zA-Z0-9_-]", "_", schema.__name__)[:64],
            "schema": schema.model_json_schema(),
            "strict": True,
        },
    }


def schema_instruction(schema: type[BaseModel]) -> str:
    return (
        "\n\nReply with one JSON object, and nothing else, that validates against this "
        f"JSON Schema:\n{json.dumps(schema.model_json_schema(), sort_keys=True)}"
    )


def _usage(data: dict[str, Any]) -> TokenUsage:
    u = data.get("usage") or {}
    return TokenUsage(
        input_tokens=int(u.get("prompt_tokens") or 0),
        output_tokens=int(u.get("completion_tokens") or 0),
    )


def _strip_fences(text: str) -> str:
    text = text.strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[1] if "\n" in text else ""
        text = text.removesuffix("```").strip()
    return text


class OpenAICompatBackend:
    def __init__(
        self,
        provider: Provider,
        *,
        max_tool_rounds: int,
        max_retries: int,
        timeout_s: float,
        backoff_s: float = 0.5,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.provider = provider
        self._max_tool_rounds = max_tool_rounds
        self._max_retries = max_retries
        self._backoff_s = backoff_s
        self._json_schema = True
        self._http = httpx.AsyncClient(
            base_url=provider.base_url,
            headers={"Authorization": f"Bearer {provider.api_key}"},
            timeout=timeout_s,
            transport=transport,
        )

    @property
    def name(self) -> str:
        return self.provider.name

    async def complete[T: BaseModel](
        self, req: LLMRequest, schema: type[T], tools: ToolBox | None
    ) -> LLMResult[T]:
        system = "\n\n".join(b["text"] for b in system_blocks(req))
        messages: list[dict[str, Any]] = [
            {"role": "system", "content": system},
            {"role": "user", "content": req.payload},
        ]
        tool_defs = openai_tools(tools) if tools is not None else None
        usage = TokenUsage()
        calls: list[ToolCall] = []
        started = time.perf_counter()
        for _round in range(self._max_tool_rounds + 1):
            data = await self._chat(req, schema, messages, tool_defs)
            usage = usage + _usage(data)
            try:
                choice = data["choices"][0]
                msg: dict[str, Any] = choice["message"]
            except (KeyError, IndexError, TypeError) as exc:
                raise LLMUnavailableError(
                    f"{self.name}: malformed response: {exc!r}", agent=req.agent
                ) from exc
            finish = choice.get("finish_reason")
            if msg.get("refusal") or finish == "content_filter":
                raise LLMRefusalError(
                    f"{self.name} refused ({finish})", agent=req.agent, category=finish
                )
            if finish == "length":
                raise LLMTruncatedError(f"output truncated: {finish}", agent=req.agent)
            tool_calls: list[dict[str, Any]] = msg.get("tool_calls") or []
            if tool_calls and tools is not None:
                messages.append(
                    {"role": "assistant", "content": msg.get("content"), "tool_calls": tool_calls}
                )
                for tc in tool_calls:
                    name = str(tc.get("function", {}).get("name", ""))
                    tool_input, out, is_error = await self._run_tool(tools, tc)
                    calls.append(ToolCall(name, tool_input, out, is_error))
                    messages.append(
                        {"role": "tool", "tool_call_id": tc.get("id", ""), "content": out}
                    )
                continue
            try:
                parsed = schema.model_validate_json(_strip_fences(msg.get("content") or ""))
            except ValidationError as exc:
                raise LLMSchemaError(f"invalid structured output: {exc}", agent=req.agent) from exc
            return LLMResult(
                output=parsed,
                model=self.provider.model,
                usage=usage,
                latency_ms=int((time.perf_counter() - started) * 1000),
                stop_reason=finish,
                request_id=data.get("id"),
                tool_calls=calls,
            )
        raise LLMSchemaError(
            f"no answer after {self._max_tool_rounds} tool rounds", agent=req.agent
        )

    @staticmethod
    async def _run_tool(tools: ToolBox, tc: dict[str, Any]) -> tuple[dict[str, Any], str, bool]:
        fn = tc.get("function", {})
        try:
            tool_input = json.loads(fn.get("arguments") or "{}")
        except ValueError:
            tool_input = None
        if not isinstance(tool_input, dict):
            msg = f"arguments are not a JSON object: {fn.get('arguments')!r}"
            return {}, json.dumps({"error": "bad_input", "message": msg}), True
        out, is_error = await tools.execute(str(fn.get("name", "")), tool_input)
        return tool_input, out, is_error

    async def _chat(
        self,
        req: LLMRequest,
        schema: type[BaseModel],
        messages: list[dict[str, Any]],
        tool_defs: list[dict[str, Any]] | None,
    ) -> dict[str, Any]:
        body: dict[str, Any] = {
            "model": self.provider.model,
            "messages": messages,
            "max_tokens": req.route.max_tokens,
        }
        if tool_defs:
            body["tools"] = tool_defs
        if self._json_schema:
            try:
                return await self._post(
                    {**body, "response_format": json_schema_format(schema)}, req
                )
            except RequestRejectedError:
                pass  # json_schema unsupported here (or the body is bad): retry once below
        system = {**messages[0], "content": messages[0]["content"] + schema_instruction(schema)}
        data = await self._post(
            {
                **body,
                "messages": [system, *messages[1:]],
                "response_format": {"type": "json_object"},
            },
            req,
        )
        self._json_schema = False
        return data

    async def _post(self, body: dict[str, Any], req: LLMRequest) -> dict[str, Any]:
        """POST with retries on 429, 5xx and network errors."""
        err: LLMError = LLMUnavailableError(f"{self.name}: no attempt made", agent=req.agent)
        for attempt in range(self._max_retries + 1):
            if attempt:
                await asyncio.sleep(min(self._backoff_s * 2 ** (attempt - 1), 8.0))
            try:
                resp = await self._http.post("chat/completions", json=body)
            except httpx.TransportError as exc:  # includes timeouts
                err = LLMUnavailableError(f"{self.name}: network error: {exc!r}", agent=req.agent)
                continue
            if resp.status_code == 200:
                try:
                    data = resp.json()
                except ValueError as exc:
                    raise LLMUnavailableError(
                        f"{self.name}: response is not JSON", agent=req.agent
                    ) from exc
                if not isinstance(data, dict):
                    raise LLMUnavailableError(f"{self.name}: malformed response", agent=req.agent)
                return data
            err = translate_status(resp.status_code, f"{self.name}: {resp.text[:300]}", req.agent)
            if not isinstance(err, LLMUnavailableError):
                raise err
        raise err

    async def aclose(self) -> None:
        await self._http.aclose()
