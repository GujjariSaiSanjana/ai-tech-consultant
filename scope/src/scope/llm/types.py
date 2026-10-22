"""Interfaces shared by the real and fake LLM backends."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol

from pydantic import BaseModel

from scope.config import AgentName, Route


@dataclass(frozen=True)
class LLMRequest:
    """One agent call.

    ``context`` (the rendered transcripts) goes first in the system prompt and carries the cache
    breakpoint; ``instructions`` (the role prompt) comes after it; ``payload`` is the user turn.
    """

    agent: AgentName
    route: Route
    context: str
    instructions: str
    payload: str


@dataclass(frozen=True)
class TokenUsage:
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0

    def __add__(self, other: TokenUsage) -> TokenUsage:
        return TokenUsage(
            self.input_tokens + other.input_tokens,
            self.output_tokens + other.output_tokens,
            self.cache_read_tokens + other.cache_read_tokens,
            self.cache_write_tokens + other.cache_write_tokens,
        )


@dataclass(frozen=True)
class ToolCall:
    name: str
    input: dict[str, Any]
    output: str
    is_error: bool


@dataclass(frozen=True)
class LLMResult[T: BaseModel]:
    output: T
    model: str
    usage: TokenUsage
    latency_ms: int
    stop_reason: str | None
    request_id: str | None = None
    tool_calls: list[ToolCall] = field(default_factory=list)
    cost_usd: float = 0.0
    mocked: bool = False


class ToolBox(Protocol):
    """The tool list every agent sends (identical across agents, so the prefix caches)."""

    def definitions(self) -> list[dict[str, Any]]: ...

    async def execute(self, name: str, tool_input: dict[str, Any]) -> tuple[str, bool]: ...


class LLMBackend(Protocol):
    async def complete[T: BaseModel](
        self, req: LLMRequest, schema: type[T], tools: ToolBox | None
    ) -> LLMResult[T]: ...


@dataclass(frozen=True)
class CallRecord:
    run_id: str | None
    agent: str
    model: str
    usage: TokenUsage
    cost_usd: float
    latency_ms: int
    stop_reason: str | None
    request_id: str | None
    error: str | None
    mocked: bool


class CallRecorder(Protocol):
    async def record(self, call: CallRecord) -> None: ...
