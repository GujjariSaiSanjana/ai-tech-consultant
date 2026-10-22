"""Per-run wrapper around a backend: budget cap, cost accounting and the llm_calls log."""

from __future__ import annotations

import asyncio
import dataclasses

from pydantic import BaseModel

from scope.config import Settings
from scope.llm.errors import BudgetExceededError, LLMError
from scope.llm.pricing import cost_usd
from scope.llm.types import (
    CallRecord,
    CallRecorder,
    LLMBackend,
    LLMRequest,
    LLMResult,
    TokenUsage,
    ToolBox,
)


class Budget:
    """Spending cap for one run. Checked before each call; concurrent calls may overshoot by at
    most the cost of the calls already in flight."""

    def __init__(self, cap_usd: float) -> None:
        self.cap_usd = cap_usd
        self.spent_usd = 0.0
        self._lock = asyncio.Lock()

    async def check(self, agent: str) -> None:
        async with self._lock:
            if self.spent_usd >= self.cap_usd:
                raise BudgetExceededError(
                    f"run budget ${self.cap_usd:.2f} reached (spent ${self.spent_usd:.4f})",
                    agent=agent,
                )

    async def add(self, amount: float) -> None:
        async with self._lock:
            self.spent_usd += amount


class LLMSession:
    def __init__(
        self,
        backend: LLMBackend,
        settings: Settings,
        *,
        tools: ToolBox | None,
        recorder: CallRecorder | None = None,
        run_id: str | None = None,
        mocked: bool = False,
    ) -> None:
        self.backend = backend
        self.settings = settings
        self.tools = tools
        self.recorder = recorder
        self.run_id = run_id
        self.mocked = mocked
        self.budget = Budget(settings.run_budget_usd)
        self.calls: list[CallRecord] = []

    async def parse[T: BaseModel](self, req: LLMRequest, schema: type[T]) -> LLMResult[T]:
        await self.budget.check(req.agent)
        try:
            result = await self.backend.complete(req, schema, self.tools)
        except LLMError as exc:
            await self._record(
                CallRecord(
                    run_id=self.run_id,
                    agent=req.agent,
                    model=req.route.model,
                    usage=TokenUsage(),
                    cost_usd=0.0,
                    latency_ms=0,
                    stop_reason=None,
                    request_id=None,
                    error=f"{type(exc).__name__}: {exc}",
                    mocked=self.mocked,
                )
            )
            raise
        cost = 0.0 if self.mocked else cost_usd(result.model, result.usage, self.settings.prices)
        result = dataclasses.replace(result, cost_usd=cost, mocked=self.mocked)
        await self.budget.add(cost)
        record = CallRecord(
            run_id=self.run_id,
            agent=req.agent,
            model=result.model,
            usage=result.usage,
            cost_usd=cost,
            latency_ms=result.latency_ms,
            stop_reason=result.stop_reason,
            request_id=result.request_id,
            error=None,
            mocked=self.mocked,
        )
        self.calls.append(record)
        await self._record(record)
        return result

    async def _record(self, record: CallRecord) -> None:
        if self.recorder is not None:
            await self.recorder.record(record)

    @property
    def total_usage(self) -> TokenUsage:
        total = TokenUsage()
        for c in self.calls:
            total = total + c.usage
        return total

    @property
    def total_cost_usd(self) -> float:
        return round(sum(c.cost_usd for c in self.calls), 6)

    @property
    def total_latency_ms(self) -> int:
        return sum(c.latency_ms for c in self.calls)
