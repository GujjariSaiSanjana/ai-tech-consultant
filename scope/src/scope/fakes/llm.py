"""Fake LLM backend: deterministic responders per agent, no network, no cost.

The default responders (``scope.fakes.heuristics``) derive their answers from the transcript with
simple rules, so offline demos and mock evals exercise the real pipeline code on real inputs. They
are a weak baseline, not a stand-in for model quality; mock eval rows are marked ``mocked: true``.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping

from pydantic import BaseModel

from scope.llm.errors import LLMError, LLMSchemaError
from scope.llm.types import LLMRequest, LLMResult, TokenUsage, ToolBox

Responder = Callable[[LLMRequest, type[BaseModel]], BaseModel]


class FakeLLM:
    def __init__(
        self,
        responders: Mapping[str, Responder] | None = None,
        *,
        failures: Mapping[str, LLMError] | None = None,
    ) -> None:
        if responders is None:
            from scope.fakes.heuristics import DEFAULT_RESPONDERS

            responders = DEFAULT_RESPONDERS
        self.responders = dict(responders)
        self.failures = dict(failures or {})
        self.requests: list[LLMRequest] = []

    async def complete[T: BaseModel](
        self, req: LLMRequest, schema: type[T], tools: ToolBox | None
    ) -> LLMResult[T]:
        self.requests.append(req)
        if req.agent in self.failures:
            raise self.failures[req.agent]
        out = self.responders[req.agent](req, schema)
        if not isinstance(out, schema):
            raise LLMSchemaError(
                f"fake responder for {req.agent} returned "
                f"{type(out).__name__}, expected {schema.__name__}",
                agent=req.agent,
            )
        return LLMResult(
            output=out,
            model=f"mock/{req.route.model}",
            usage=TokenUsage(),
            latency_ms=0,
            stop_reason="end_turn",
            mocked=True,
        )
