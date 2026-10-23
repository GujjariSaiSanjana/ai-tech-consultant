"""Gemini/NVIDIA fallback: OpenAICompatBackend over httpx.MockTransport, FallbackBackend, wiring."""

from __future__ import annotations

import json
from collections.abc import Sequence
from typing import Any

import httpx
import pytest
from pydantic import BaseModel

from scope.agents.base import request
from scope.brain.tools import BrainToolBox
from scope.config import Price, Provider, Settings
from scope.fakes.brain import FakeBrain
from scope.fakes.llm import FakeLLM
from scope.llm.anthropic_backend import AnthropicBackend
from scope.llm.errors import (
    BudgetExceededError,
    LLMConfigError,
    LLMError,
    LLMRefusalError,
    LLMSchemaError,
    LLMTruncatedError,
    LLMUnavailableError,
)
from scope.llm.fallback_backend import FallbackBackend
from scope.llm.openai_compat_backend import OpenAICompatBackend
from scope.llm.session import LLMSession
from scope.llm.types import CallRecord, LLMRequest, LLMResult, ToolBox
from scope.wiring import build_backend, build_deps

GEMINI = Provider("gemini", "https://gemini.test/v1beta/openai/", "g-key", "gemini-2.5-flash")
NVIDIA = Provider("nvidia", "https://nvidia.test/v1", "n-key", "openai/gpt-oss-20b")


class Out(BaseModel):
    answer: str


def completion(
    content: str | None = '{"answer": "ok"}',
    *,
    finish: str = "stop",
    tool_calls: list[dict[str, Any]] | None = None,
    usage: tuple[int, int] = (10, 5),
    refusal: str | None = None,
) -> httpx.Response:
    msg: dict[str, Any] = {"role": "assistant", "content": content}
    if tool_calls:
        msg["tool_calls"] = tool_calls
    if refusal:
        msg["refusal"] = refusal
    return httpx.Response(
        200,
        json={
            "id": "chatcmpl-1",
            "choices": [{"index": 0, "message": msg, "finish_reason": finish}],
            "usage": {"prompt_tokens": usage[0], "completion_tokens": usage[1]},
        },
    )


def tool_call(name: str, args: dict[str, Any], call_id: str = "call_1") -> dict[str, Any]:
    return {
        "id": call_id,
        "type": "function",
        "function": {"name": name, "arguments": json.dumps(args)},
    }


class Server:
    """Scripted OpenAI-compatible endpoint; records every request body."""

    def __init__(self, responses: Sequence[httpx.Response | Exception]) -> None:
        self.responses = list(responses)
        self.requests: list[httpx.Request] = []

    @property
    def bodies(self) -> list[dict[str, Any]]:
        return [json.loads(r.content) for r in self.requests]

    def __call__(self, req: httpx.Request) -> httpx.Response:
        self.requests.append(req)
        nxt = self.responses.pop(0)
        if isinstance(nxt, Exception):
            raise nxt
        return nxt

    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self)


def compat(
    server: Server, provider: Provider = GEMINI, *, rounds: int = 2, retries: int = 0
) -> OpenAICompatBackend:
    return OpenAICompatBackend(
        provider,
        max_tool_rounds=rounds,
        max_retries=retries,
        timeout_s=5,
        backoff_s=0,
        transport=server.transport(),
    )


def down(agent: str = "extractor") -> FakeLLM:
    return FakeLLM(failures={agent: LLMUnavailableError("claude overloaded")})


class Recorder:
    def __init__(self) -> None:
        self.calls: list[CallRecord] = []

    async def record(self, call: CallRecord) -> None:
        self.calls.append(call)


class Spy:
    """A backend that must not be reached."""

    def __init__(self) -> None:
        self.called = False

    async def complete[T: BaseModel](
        self, req: LLMRequest, schema: type[T], tools: ToolBox | None
    ) -> LLMResult[T]:
        self.called = True
        raise AssertionError("fallback must not be called")


# ---- OpenAICompatBackend ------------------------------------------------------------------


async def test_request_shape_usage_and_model(settings: Settings) -> None:
    server = Server([completion(usage=(100, 20))])
    res = await compat(server).complete(request("extractor", settings, "T", "{}"), Out, None)
    assert res.output.answer == "ok" and res.model == "gemini-2.5-flash"
    assert res.usage.input_tokens == 100 and res.usage.output_tokens == 20
    assert res.request_id == "chatcmpl-1" and res.stop_reason == "stop"
    req = server.requests[0]
    assert str(req.url) == "https://gemini.test/v1beta/openai/chat/completions"
    assert req.headers["Authorization"] == "Bearer g-key"
    body = server.bodies[0]
    assert body["model"] == "gemini-2.5-flash"
    assert body["response_format"]["type"] == "json_schema"
    assert body["response_format"]["json_schema"]["strict"] is True
    assert body["response_format"]["json_schema"]["schema"] == Out.model_json_schema()
    assert body["messages"][0]["role"] == "system" and "T" in body["messages"][0]["content"]
    assert body["messages"][1] == {"role": "user", "content": "{}"}
    assert not {"temperature", "top_p", "top_k", "tools"} & body.keys()


async def test_json_object_retry_after_400_and_then_sticks(settings: Settings) -> None:
    server = Server(
        [
            httpx.Response(400, json={"error": {"message": "json_schema not supported"}}),
            completion('```json\n{"answer": "fenced"}\n```'),
            completion(),
        ]
    )
    be = compat(server, NVIDIA)
    req = request("extractor", settings, "T", "{}")
    res = await be.complete(req, Out, None)
    assert res.output.answer == "fenced" and res.model == NVIDIA.model
    retry = server.bodies[1]
    assert retry["response_format"] == {"type": "json_object"}
    assert '"answer"' in retry["messages"][0]["content"]  # schema moved into the system prompt
    await be.complete(req, Out, None)
    assert server.bodies[2]["response_format"] == {"type": "json_object"}
    assert len(server.requests) == 3


async def test_second_400_is_a_config_error(settings: Settings) -> None:
    bad = httpx.Response(400, json={"error": {"message": "bad request"}})
    server = Server([bad, httpx.Response(400, json={"error": {"message": "still bad"}})])
    with pytest.raises(LLMConfigError):
        await compat(server).complete(request("extractor", settings, "T", "{}"), Out, None)


@pytest.mark.parametrize(
    "response", [completion('{"answer": 3, "extra": 1'), completion('{"wrong": "x"}')]
)
async def test_schema_error(settings: Settings, response: httpx.Response) -> None:
    with pytest.raises(LLMSchemaError):
        await compat(Server([response])).complete(
            request("extractor", settings, "T", "{}"), Out, None
        )


@pytest.mark.parametrize(
    ("response", "exc"),
    [
        (completion(None, finish="content_filter"), LLMRefusalError),
        (completion(None, refusal="I can't help with that"), LLMRefusalError),
        (completion('{"answer": "o', finish="length"), LLMTruncatedError),
        (httpx.Response(401, text="bad key"), LLMConfigError),
        (httpx.Response(404, text="no such model"), LLMConfigError),
        (httpx.Response(429, text="slow down"), LLMUnavailableError),
        (httpx.Response(503, text="overloaded"), LLMUnavailableError),
        (httpx.Response(200, text="<html>"), LLMUnavailableError),
    ],
)
async def test_stop_reasons_and_errors(
    settings: Settings, response: httpx.Response, exc: type[Exception]
) -> None:
    with pytest.raises(exc):
        await compat(Server([response])).complete(
            request("extractor", settings, "T", "{}"), Out, None
        )


async def test_retries_transient_errors_then_succeeds(settings: Settings) -> None:
    server = Server(
        [httpx.ConnectError("refused"), httpx.Response(502, text="bad gateway"), completion()]
    )
    res = await compat(server, retries=2).complete(
        request("extractor", settings, "T", "{}"), Out, None
    )
    assert res.output.answer == "ok" and len(server.requests) == 3


async def test_tool_loop_on_fallback_backend(settings: Settings) -> None:
    server = Server(
        [
            completion(
                None,
                finish="tool_calls",
                tool_calls=[tool_call("brain_feasibility", {"requirement": "radius by zip"})],
            ),
            completion('{"answer": "done"}'),
        ]
    )
    brain = FakeBrain()
    res = await compat(server).complete(
        request("feasibility", settings, "T", "{}"), Out, BrainToolBox(brain)
    )
    assert res.output.answer == "done"
    assert brain.calls == [("feasibility", "radius by zip")]
    assert res.tool_calls[0].name == "brain_feasibility" and not res.tool_calls[0].is_error
    assert res.tool_calls[0].input == {"requirement": "radius by zip"}
    assert res.usage.input_tokens == 20  # summed over both rounds
    first, second = server.bodies
    assert [t["function"]["name"] for t in first["tools"]] == ["brain_feasibility", "brain_search"]
    assert first["tools"][0]["type"] == "function"
    assert first["tools"][0]["function"]["parameters"]["required"] == ["requirement"]
    assert second["messages"][2]["role"] == "assistant"
    assert second["messages"][2]["tool_calls"][0]["id"] == "call_1"
    assert second["messages"][3]["role"] == "tool"
    assert second["messages"][3]["tool_call_id"] == "call_1"
    assert second["tools"] == first["tools"]


async def test_bad_tool_arguments_and_brain_down_are_error_results(settings: Settings) -> None:
    broken = {
        "id": "c2",
        "type": "function",
        "function": {"name": "brain_search", "arguments": "{"},
    }
    server = Server(
        [
            completion(None, finish="tool_calls", tool_calls=[broken]),
            completion(None, tool_calls=[tool_call("brain_search", {"query": "x"})]),
            completion(),
        ]
    )
    res = await compat(server).complete(
        request("feasibility", settings, "T", "{}"), Out, BrainToolBox(FakeBrain(down=True))
    )
    assert [c.is_error for c in res.tool_calls] == [True, True]


async def test_tool_rounds_are_bounded(settings: Settings) -> None:
    loop = [
        completion(
            None, finish="tool_calls", tool_calls=[tool_call("brain_search", {"query": "x"})]
        )
        for _ in range(3)
    ]
    with pytest.raises(LLMSchemaError):
        await compat(Server(loop), rounds=2).complete(
            request("feasibility", settings, "T", "{}"), Out, BrainToolBox(FakeBrain())
        )


# ---- FallbackBackend ----------------------------------------------------------------------


async def test_falls_back_when_claude_is_unavailable(settings: Settings) -> None:
    server = Server([completion()])
    res = await FallbackBackend(down(), [compat(server)]).complete(
        request("extractor", settings, "T", "{}"), Out, None
    )
    assert res.model == "gemini-2.5-flash" and len(server.requests) == 1


async def test_order_skips_unavailable_and_reraises_original(settings: Settings) -> None:
    gemini = Server([httpx.Response(503, text="down")])
    nvidia = Server([completion()])
    req = request("extractor", settings, "T", "{}")
    res = await FallbackBackend(down(), [compat(gemini), compat(nvidia, NVIDIA)]).complete(
        req, Out, None
    )
    assert res.model == NVIDIA.model and len(gemini.requests) == 1

    all_down = [
        compat(Server([httpx.Response(429)])),
        compat(Server([httpx.Response(500)]), NVIDIA),
    ]
    with pytest.raises(LLMUnavailableError, match="claude overloaded"):
        await FallbackBackend(down(), all_down).complete(req, Out, None)


@pytest.mark.parametrize(
    "error",
    [
        LLMRefusalError("refused", category="cyber"),
        LLMSchemaError("bad output"),
        LLMConfigError("bad request"),
        LLMTruncatedError("cut off"),
    ],
)
async def test_non_transient_claude_errors_do_not_fall_back(
    settings: Settings, error: LLMError
) -> None:
    spy = Spy()
    primary = FakeLLM(failures={"extractor": error})
    with pytest.raises(type(error)):
        await FallbackBackend(primary, [spy]).complete(
            request("extractor", settings, "T", "{}"), Out, None
        )
    assert not spy.called


async def test_fallback_refusal_is_not_passed_on(settings: Settings) -> None:
    nvidia = Server([completion()])
    gemini = Server([completion(None, finish="content_filter")])
    with pytest.raises(LLMRefusalError):
        await FallbackBackend(down(), [compat(gemini), compat(nvidia, NVIDIA)]).complete(
            request("extractor", settings, "T", "{}"), Out, None
        )
    assert not nvidia.requests


async def test_cost_model_and_budget_through_session(settings: Settings) -> None:
    rec = Recorder()
    gemini = Server([completion(usage=(1_000_000, 1_000_000)) for _ in range(2)])
    s = LLMSession(
        FallbackBackend(down("scoper"), [compat(gemini)]),
        settings.model_copy(update={"run_budget_usd": 2.0}),
        tools=None,
        recorder=rec,
    )
    req = request("scoper", settings, "t", "{}")
    res = await s.parse(req, Out)
    assert res.model == "gemini-2.5-flash" and res.cost_usd == pytest.approx(0.30 + 2.50)
    assert rec.calls[0].model == "gemini-2.5-flash" and rec.calls[0].cost_usd == res.cost_usd
    with pytest.raises(BudgetExceededError):
        await s.parse(req, Out)

    nvidia = Server([completion(usage=(1_000_000, 1_000_000))])
    free = LLMSession(FallbackBackend(None, [compat(nvidia, NVIDIA)]), settings, tools=None)
    res = await free.parse(req, Out)
    assert res.model == NVIDIA.model and res.cost_usd == 0.0


async def test_gemini_price_override(settings: Settings) -> None:
    custom = keys(gemini_model="gemini-2.5-pro", gemini_price=Price(input=1.25, output=10))
    server = Server([completion(usage=(1_000_000, 0))])
    pro = Provider("gemini", GEMINI.base_url, "g", "gemini-2.5-pro")
    res = await LLMSession(compat(server, pro), custom, tools=None).parse(
        request("scoper", settings, "t", "{}"), Out
    )
    assert res.cost_usd == pytest.approx(1.25)


# ---- settings and wiring ------------------------------------------------------------------


def keys(**kw: Any) -> Settings:
    base: dict[str, Any] = {
        "llm": "auto",
        "brain": "auto",
        "anthropic_api_key": None,
        "gemini_api_key": None,
        "nvidia_api_key": None,
        "llm_fallback_order": "gemini,nvidia",
    }
    return Settings(**(base | kw))


def test_fallback_order_skips_keyless_and_can_be_disabled() -> None:
    s = keys(gemini_api_key="g", nvidia_api_key="n", llm_fallback_order=" NVIDIA , gemini ")
    assert [p.name for p in s.fallback_providers()] == ["nvidia", "gemini"]
    assert [p.name for p in keys(nvidia_api_key="n").fallback_providers()] == ["nvidia"]
    assert keys(gemini_api_key="g", llm_fallback_order="").fallback_providers() == []
    with pytest.raises(ValueError, match="unknown fallback"):
        keys(llm_fallback_order="gemini,openai")


@pytest.mark.parametrize(
    ("kw", "fake"),
    [
        ({}, True),
        ({"anthropic_api_key": "a"}, False),
        ({"gemini_api_key": "g"}, False),
        ({"nvidia_api_key": "n"}, False),
        ({"gemini_api_key": "g", "llm": "fake"}, True),
    ],
)
def test_use_fake_llm_needs_no_key_at_all(kw: dict[str, Any], fake: bool) -> None:
    s = keys(**kw)
    assert s.use_fake_llm() is fake and s.use_fake_brain() is fake


async def test_wiring_with_only_gemini_key(settings: Settings) -> None:
    s = keys(gemini_api_key="g-only")
    deps = build_deps(s)
    assert not deps.mocked and isinstance(deps.backend, FallbackBackend)
    assert deps.backend.primary is None
    assert [b.name for b in deps.backend.fallbacks if isinstance(b, OpenAICompatBackend)] == [
        "gemini"
    ]

    server = Server([completion()])
    backend = build_backend(s, transport=server.transport())
    res = await backend.complete(request("extractor", settings, "T", "{}"), Out, None)
    assert res.model == "gemini-2.5-flash"
    url = str(server.requests[0].url)
    assert url == "https://generativelanguage.googleapis.com/v1beta/openai/chat/completions"
    assert server.requests[0].headers["Authorization"] == "Bearer g-only"


def test_wiring_claude_alone_and_claude_first() -> None:
    assert isinstance(build_backend(keys(anthropic_api_key="a")), AnthropicBackend)
    both = build_backend(keys(anthropic_api_key="a", nvidia_api_key="n"))
    assert isinstance(both, FallbackBackend) and isinstance(both.primary, AnthropicBackend)


def test_wiring_rejects_unpriced_fallback_model() -> None:
    with pytest.raises(LLMConfigError, match="SCOPE_GEMINI_PRICE"):
        build_backend(keys(gemini_api_key="g", gemini_model="gemini-9-ultra"))
