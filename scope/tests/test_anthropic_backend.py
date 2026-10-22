"""AnthropicBackend against a stub SDK client: tool loop, stop reasons and error mapping."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, cast

import anthropic
import httpx2
import pytest
from anthropic.types import Usage
from pydantic import BaseModel

from scope.agents.base import request
from scope.brain.tools import BrainToolBox
from scope.config import Settings
from scope.fakes.brain import FakeBrain
from scope.llm.anthropic_backend import AnthropicBackend, translate_error
from scope.llm.errors import (
    LLMConfigError,
    LLMRefusalError,
    LLMSchemaError,
    LLMTruncatedError,
    LLMUnavailableError,
)


class Out(BaseModel):
    answer: str


@dataclass
class Block:
    type: str
    id: str = "tu_1"
    name: str = ""
    input: dict[str, Any] = field(default_factory=dict)
    text: str = ""

    def to_dict(self, exclude_none: bool = False) -> dict[str, Any]:
        if self.type == "tool_use":
            return {"type": "tool_use", "id": self.id, "name": self.name, "input": self.input}
        return {"type": "text", "text": self.text}


@dataclass
class StopDetails:
    category: str | None


@dataclass
class Resp:
    stop_reason: str
    content: list[Block]
    parsed_output: Out | None = None
    usage: Usage = field(default_factory=lambda: Usage(input_tokens=10, output_tokens=5,
                                                       cache_read_input_tokens=100,
                                                       cache_creation_input_tokens=0))
    model: str = "claude-sonnet-5-5"
    stop_details: StopDetails | None = None
    _request_id: str = "req_1"


class StubMessages:
    def __init__(self, responses: list[Resp | Exception]) -> None:
        self.responses = responses
        self.calls: list[dict[str, Any]] = []

    async def parse(self, **kwargs: Any) -> Resp:
        self.calls.append({**kwargs, "messages": list(kwargs["messages"])})
        nxt = self.responses.pop(0)
        if isinstance(nxt, Exception):
            raise nxt
        return nxt


class StubClient:
    def __init__(self, responses: list[Resp | Exception]) -> None:
        self.messages = StubMessages(responses)


def backend(responses: list[Resp | Exception], rounds: int = 2) -> tuple[AnthropicBackend,
                                                                       StubClient]:
    client = StubClient(responses)
    return AnthropicBackend(cast(anthropic.AsyncAnthropic, client), max_tool_rounds=rounds), client


async def test_parsed_output_usage_and_parse_arguments(settings: Settings) -> None:
    be, client = backend([Resp("end_turn", [Block("text")], Out(answer="hi"))])
    res = await be.complete(request("extractor", settings, "T", "{}"), Out, None)
    assert res.output.answer == "hi" and res.request_id == "req_1"
    assert res.usage.cache_read_tokens == 100
    call = client.messages.calls[0]
    assert call["output_format"] is Out
    assert call["system"][0]["cache_control"] == {"type": "ephemeral"}
    assert "temperature" not in call and "tool_choice" not in call


async def test_tool_loop_executes_brain_and_continues(settings: Settings) -> None:
    tool = Block("tool_use", name="brain_feasibility", input={"requirement": "radius by zip"})
    be, client = backend([Resp("tool_use", [tool]),
                          Resp("end_turn", [Block("text")], Out(answer="done"))])
    brain = FakeBrain()
    res = await be.complete(request("feasibility", settings, "T", "{}"), Out, BrainToolBox(brain))
    assert res.output.answer == "done"
    assert brain.calls == [("feasibility", "radius by zip")]
    assert res.tool_calls[0].name == "brain_feasibility" and not res.tool_calls[0].is_error
    second = client.messages.calls[1]["messages"]
    assert second[1]["role"] == "assistant" and second[1]["content"][0]["type"] == "tool_use"
    assert second[2]["content"][0]["type"] == "tool_result"
    assert second[2]["content"][0]["tool_use_id"] == "tu_1"
    assert res.usage.input_tokens == 20  # summed over both rounds
    assert [t["name"] for t in client.messages.calls[0]["tools"]] == \
        [t["name"] for t in client.messages.calls[1]["tools"]]


async def test_brain_down_inside_tool_returns_error_result(settings: Settings) -> None:
    tool = Block("tool_use", name="brain_search", input={"query": "x"})
    be, _ = backend([Resp("tool_use", [tool]), Resp("end_turn", [], Out(answer="ok"))])
    res = await be.complete(request("feasibility", settings, "T", "{}"), Out,
                            BrainToolBox(FakeBrain(down=True)))
    assert res.tool_calls[0].is_error


async def test_tool_rounds_are_bounded(settings: Settings) -> None:
    tool = Block("tool_use", name="brain_search", input={"query": "x"})
    be, _ = backend([Resp("tool_use", [tool]) for _ in range(3)], rounds=2)
    with pytest.raises(LLMSchemaError):
        await be.complete(request("feasibility", settings, "T", "{}"), Out,
                          BrainToolBox(FakeBrain()))


@pytest.mark.parametrize(
    ("resp", "exc"),
    [
        (Resp("refusal", [], stop_details=StopDetails("cyber")), LLMRefusalError),
        (Resp("max_tokens", []), LLMTruncatedError),
        (Resp("model_context_window_exceeded", []), LLMTruncatedError),
        (Resp("end_turn", [], None), LLMSchemaError),
    ],
)
async def test_stop_reasons(settings: Settings, resp: Resp, exc: type[Exception]) -> None:
    be, _ = backend([resp])
    with pytest.raises(exc):
        await be.complete(request("extractor", settings, "T", "{}"), Out, None)


def _status(cls: type[anthropic.APIStatusError], code: int) -> anthropic.APIStatusError:
    resp = httpx2.Response(code, request=httpx2.Request("POST", "https://api.example/v1/messages"))
    return cls("boom", response=resp, body=None)


@pytest.mark.parametrize(
    ("exc", "want"),
    [
        (_status(anthropic.AuthenticationError, 401), LLMConfigError),
        (_status(anthropic.NotFoundError, 404), LLMConfigError),
        (_status(anthropic.BadRequestError, 400), LLMConfigError),
        (_status(anthropic.RateLimitError, 429), LLMUnavailableError),
        (_status(anthropic.InternalServerError, 529), LLMUnavailableError),
        (anthropic.APIConnectionError(request=httpx2.Request("POST", "https://x")),
         LLMUnavailableError),
    ],
)
def test_error_translation(exc: Exception, want: type[Exception]) -> None:
    assert isinstance(translate_error(exc, "extractor"), want)


async def test_sdk_errors_are_raised_typed(settings: Settings) -> None:
    be, _ = backend([_status(anthropic.RateLimitError, 429)])
    with pytest.raises(LLMUnavailableError):
        await be.complete(request("extractor", settings, "T", "{}"), Out, None)
