"""Dependency wiring: choose the real or fake LLM and Brain from settings."""

from __future__ import annotations

from dataclasses import dataclass

import anthropic

from scope.brain.client import BrainClient, HttpBrainClient
from scope.brain.tools import BrainToolBox
from scope.config import Settings
from scope.fakes.brain import FakeBrain
from scope.fakes.llm import FakeLLM
from scope.llm.anthropic_backend import AnthropicBackend
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


def build_deps(settings: Settings) -> Deps:
    if settings.use_fake_llm():
        backend: LLMBackend = FakeLLM()
    else:
        client = anthropic.AsyncAnthropic(
            api_key=settings.anthropic_api_key, max_retries=settings.llm_max_retries
        )
        backend = AnthropicBackend(client, max_tool_rounds=settings.max_tool_rounds)
    brain: BrainClient
    if settings.use_fake_brain():
        brain = FakeBrain()
    else:
        brain = HttpBrainClient(
            settings.brain_url, settings.brain_api_key, timeout_s=settings.brain_timeout_s
        )
    return Deps(backend=backend, brain=brain, mocked=settings.use_fake_llm())
