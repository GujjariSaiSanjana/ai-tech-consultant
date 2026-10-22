"""Runtime configuration. Everything comes from environment variables (``SCOPE_*``) or defaults."""

from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic import BaseModel, Field
from pydantic_settings import BaseSettings, SettingsConfigDict

from scope.domain.models import Role

Effort = Literal["low", "medium", "high", "xhigh", "max"]
AgentName = Literal[
    "extractor", "questioner", "feasibility", "scoper", "reviewer", "proposal_writer", "reconciler"
]
AGENTS: tuple[AgentName, ...] = (
    "extractor",
    "questioner",
    "feasibility",
    "scoper",
    "reviewer",
    "proposal_writer",
    "reconciler",
)

SONNET = "claude-sonnet-5-5"
OPUS = "claude-opus-5-5"
HAIKU = "claude-haiku-5-5"


class Route(BaseModel):
    model: str
    effort: Effort
    max_tokens: int


def default_routes() -> dict[AgentName, Route]:
    return {
        "extractor": Route(model=SONNET, effort="high", max_tokens=16000),
        "questioner": Route(model=OPUS, effort="high", max_tokens=12000),
        "feasibility": Route(model=SONNET, effort="medium", max_tokens=12000),
        "scoper": Route(model=OPUS, effort="high", max_tokens=16000),
        "reviewer": Route(model=OPUS, effort="high", max_tokens=8000),
        "proposal_writer": Route(model=SONNET, effort="medium", max_tokens=12000),
        "reconciler": Route(model=OPUS, effort="high", max_tokens=16000),
    }


class Price(BaseModel):
    """USD per million tokens. Cache writes (5-minute TTL) bill 1.25x input."""

    input: float
    output: float
    cache_read: float
    cache_write_multiplier: float = 1.25


def default_prices() -> dict[str, Price]:
    return {
        HAIKU: Price(input=0.10, output=0.50, cache_read=0.01),
        SONNET: Price(input=2.0, output=10.0, cache_read=0.20),
        # Opus 5.5 cache reads are 0.05x input per the API pricing notes.
        OPUS: Price(input=4.0, output=20.0, cache_read=0.20),
    }


def default_rate_card() -> dict[Role, float]:
    return {
        "consultant": 140.0,
        "project_manager": 120.0,
        "developer": 110.0,
        "designer": 95.0,
        "qa": 80.0,
    }


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="SCOPE_", env_file=".env", extra="ignore")

    database_url: str = "sqlite+aiosqlite:///./data/runtime/scope.db"
    export_dir: str = "./data/runtime/exports"
    # "auto": real Claude when ANTHROPIC_API_KEY is set, otherwise the offline fake.
    llm: Literal["auto", "anthropic", "fake"] = "auto"
    # "auto": the HTTP Brain when using real Claude, the offline fake Brain with the fake LLM.
    brain: Literal["auto", "http", "fake"] = "auto"
    brain_url: str = Field(default="http://localhost:8000", validation_alias="BRAIN_URL")
    brain_api_key: str = Field(default="dev-brain-key", validation_alias="BRAIN_API_KEY")
    brain_timeout_s: float = 20.0
    brain_concurrency: int = 4
    anthropic_api_key: str | None = Field(default=None, validation_alias="ANTHROPIC_API_KEY")
    llm_max_retries: int = 3
    run_budget_usd: float = 5.0
    max_review_loops: int = 2
    max_tool_rounds: int = 4
    # Above this many (estimated) transcript tokens, extraction runs per segment and merges.
    long_transcript_tokens: int = 150_000
    segment_tokens: int = 100_000
    setting_max_hours: float = 6.0
    routes: dict[AgentName, Route] = Field(default_factory=default_routes)
    prices: dict[str, Price] = Field(default_factory=default_prices)
    rate_card: dict[Role, float] = Field(default_factory=default_rate_card)

    def use_fake_llm(self) -> bool:
        return self.llm == "fake" or (self.llm == "auto" and not self.anthropic_api_key)

    def use_fake_brain(self) -> bool:
        return self.brain == "fake" or (self.brain == "auto" and self.use_fake_llm())

    def with_model(self, model: str) -> Settings:
        """Copy with every agent routed to ``model`` (used by evals to compare models)."""
        routes = {k: v.model_copy(update={"model": model}) for k, v in self.routes.items()}
        return self.model_copy(update={"routes": routes})


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
