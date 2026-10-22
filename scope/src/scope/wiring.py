"""Dependency wiring: choose the real or fake LLM and Brain from settings."""

from __future__ import annotations

from dataclasses import dataclass

import anthropic
import httpx

from scope.brain.client import BrainClient, HttpBrainClient
from scope.brain.tools import BrainToolBox
from scope.config import Settings
from scope.fakes.brain import FakeBrain
from scope.fakes.llm import FakeLLM
from scope.llm.anthropic_backend import AnthropicBackend
from scope.llm.errors import LLMConfigError
from scope.llm.fallback_backend import FallbackBackend
from scope.llm.openai_compat_backend import OpenAICompatBackend
from scope.llm.pricing import price_for
from scope.llm.session import LLMSession
from scope.llm.types import CallRecorder, LLMBackend


@dataclass(frozen=True)
class Deps:
    backend: LLMBackend
    brain: BrainClient
    mocked: bool

    def session(
        self, settings: Settings, *, run_id: str | None = None, recorder: CallRecorder | None = None
    ) -> LLMSession:
        return LLMSession(
            self.backend,
            settings,
            tools=BrainToolBox(self.brain),
            recorder=recorder,
            run_id=run_id,
            mocked=self.mocked,
        )


def build_backend(
    settings: Settings, *, transport: httpx.AsyncBaseTransport | None = None
) -> LLMBackend:
    """Claude when keyed, wrapped in ``FallbackBackend`` when any fallback has a key."""
    prices = settings.price_table()
    fallbacks: list[LLMBackend] = []
    for provider in settings.fallback_providers():
        try:  # fail at startup, not after a paid call
            price_for(provider.model, prices)
        except KeyError as exc:
            raise LLMConfigError(
                f"{provider.name}: no price for {provider.model!r}; set "
                f"SCOPE_{provider.name.upper()}_PRICE"
            ) from exc
        fallbacks.append(
            OpenAICompatBackend(
                provider,
                max_tool_rounds=settings.max_tool_rounds,
                max_retries=settings.llm_max_retries,
                timeout_s=settings.fallback_timeout_s,
                transport=transport,
            )
        )
    if not fallbacks:  # Claude alone (with no key at all, the SDK reports it)
        return _anthropic(settings)
    primary = _anthropic(settings) if settings.anthropic_api_key else None
    return FallbackBackend(primary, fallbacks)


def _anthropic(settings: Settings) -> AnthropicBackend:
    client = anthropic.AsyncAnthropic(
        api_key=settings.anthropic_api_key, max_retries=settings.llm_max_retries
    )
    return AnthropicBackend(client, max_tool_rounds=settings.max_tool_rounds)


def build_deps(settings: Settings) -> Deps:
    backend: LLMBackend = FakeLLM() if settings.use_fake_llm() else build_backend(settings)
    brain: BrainClient
    if settings.use_fake_brain():
        brain = FakeBrain()
    else:
        brain = HttpBrainClient(
            settings.brain_url, settings.brain_api_key, timeout_s=settings.brain_timeout_s
        )
    return Deps(backend=backend, brain=brain, mocked=settings.use_fake_llm())
