from __future__ import annotations

import pytest
from pydantic import BaseModel

from scope.agents.base import request
from scope.config import Settings, default_prices
from scope.fakes.llm import FakeLLM
from scope.llm.errors import BudgetExceededError, LLMUnavailableError
from scope.llm.pricing import cost_usd
from scope.llm.session import LLMSession
from scope.llm.types import CallRecord, LLMRequest, LLMResult, TokenUsage, ToolBox


class Out(BaseModel):
    x: int


class PricedBackend:
    """Reports real-looking usage so the session prices it."""

    async def complete[T: BaseModel](
        self, req: LLMRequest, schema: type[T], tools: ToolBox | None
    ) -> LLMResult[T]:
        out = schema.model_validate({"x": 1})
        usage = TokenUsage(
            input_tokens=1_000_000,
            output_tokens=100_000,
            cache_read_tokens=1_000_000,
            cache_write_tokens=0,
        )
        return LLMResult(
            output=out,
            model="claude-opus-5-5-20260901",
            usage=usage,
            latency_ms=5,
            stop_reason="end_turn",
        )


class Recorder:
    def __init__(self) -> None:
        self.calls: list[CallRecord] = []

    async def record(self, call: CallRecord) -> None:
        self.calls.append(call)


def test_pricing_per_model_including_cache() -> None:
    p = default_prices()
    u = TokenUsage(
        input_tokens=1_000_000,
        output_tokens=1_000_000,
        cache_read_tokens=1_000_000,
        cache_write_tokens=1_000_000,
    )
    assert cost_usd("claude-haiku-5-5", u, p) == pytest.approx(0.10 + 0.50 + 0.01 + 0.125)
    assert cost_usd("claude-sonnet-5-5", u, p) == pytest.approx(2 + 10 + 0.2 + 2.5)
    assert cost_usd("claude-opus-5-5", u, p) == pytest.approx(4 + 20 + 0.2 + 5)
    with pytest.raises(KeyError):
        cost_usd("gpt-9", u, p)


async def test_session_prices_records_and_enforces_budget(settings: Settings) -> None:
    rec = Recorder()
    s = LLMSession(
        PricedBackend(),
        settings.model_copy(update={"run_budget_usd": 5.0}),
        tools=None,
        recorder=rec,
        run_id="7",
    )
    req = request("scoper", settings, "t", "{}")
    res = await s.parse(req, Out)
    assert res.cost_usd == pytest.approx(4 + 2 + 0.2)  # dated snapshot id priced by prefix
    assert rec.calls[0].run_id == "7" and rec.calls[0].cost_usd == res.cost_usd
    with pytest.raises(BudgetExceededError):  # the first call already spent past the cap
        await s.parse(req, Out)
    assert s.total_cost_usd == pytest.approx(6.2)
    assert len(rec.calls) == 1


async def test_failed_calls_are_logged(settings: Settings) -> None:
    rec = Recorder()
    fake = FakeLLM(failures={"scoper": LLMUnavailableError("overloaded")})
    s = LLMSession(fake, settings, tools=None, recorder=rec, mocked=True)
    with pytest.raises(LLMUnavailableError):
        await s.parse(request("scoper", settings, "t", "{}"), Out)
    assert rec.calls[0].error and "overloaded" in rec.calls[0].error and rec.calls[0].mocked
