from __future__ import annotations

from pathlib import Path
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

PROJECT_DIR = Path(__file__).resolve().parents[2]
REPO_DIR = PROJECT_DIR.parent

EmbedderName = Literal["voyage", "local", "hash"]
DEFAULT_API_KEY = "dev-brain-key"


class Settings(BaseSettings):
    """All runtime configuration, read from the environment (and `.env` files if present)."""

    model_config = SettingsConfigDict(
        env_file=(REPO_DIR / ".env", PROJECT_DIR / ".env"), extra="ignore", populate_by_name=True
    )

    database_url: str = Field("postgresql://postgres:postgres@localhost:5432/brain", alias="BRAIN_DATABASE_URL")
    api_key: str = Field(DEFAULT_API_KEY, alias="BRAIN_API_KEY")
    public_url: str = Field("http://localhost:8000", alias="BRAIN_URL")
    host: str = Field("127.0.0.1", alias="BRAIN_HOST")
    port: int = Field(8000, alias="BRAIN_PORT")

    anthropic_api_key: str | None = Field(None, alias="ANTHROPIC_API_KEY")
    voyage_api_key: str | None = Field(None, alias="VOYAGE_API_KEY")

    # Fallback LLMs behind Claude (OpenAI-compatible Chat Completions). Shared contract across the three
    # apps, so no BRAIN_ prefix. Prices are USD per million tokens.
    gemini_api_key: str | None = Field(None, alias="GEMINI_API_KEY")
    gemini_model: str = Field("gemini-2.5-flash", alias="GEMINI_MODEL")
    gemini_base_url: str = Field("https://generativelanguage.googleapis.com/v1beta/openai/", alias="GEMINI_BASE_URL")
    gemini_input_price: float = Field(0.30, alias="GEMINI_INPUT_PRICE_PER_MTOK")
    gemini_output_price: float = Field(2.50, alias="GEMINI_OUTPUT_PRICE_PER_MTOK")
    nvidia_api_key: str | None = Field(None, alias="NVIDIA_API_KEY")
    nvidia_model: str = Field("openai/gpt-oss-20b", alias="NVIDIA_MODEL")
    nvidia_base_url: str = Field("https://integrate.api.nvidia.com/v1", alias="NVIDIA_BASE_URL")
    nvidia_input_price: float = Field(0.0, alias="NVIDIA_INPUT_PRICE_PER_MTOK")
    nvidia_output_price: float = Field(0.0, alias="NVIDIA_OUTPUT_PRICE_PER_MTOK")
    llm_fallback_order: str = Field(
        "gemini,nvidia", alias="LLM_FALLBACK_ORDER", description="Comma-separated; empty disables fallback."
    )

    embedder: EmbedderName | None = Field(
        None, alias="BRAIN_EMBEDDER", description="Defaults to voyage when VOYAGE_API_KEY is set, else local."
    )
    answer_model: str = Field("claude-sonnet-5-5", alias="BRAIN_ANSWER_MODEL")
    classify_model: str = Field("claude-haiku-5-5", alias="BRAIN_CLASSIFY_MODEL")
    vision_model: str = Field("claude-haiku-5-5", alias="BRAIN_VISION_MODEL")
    contextual_prefix: bool = Field(False, alias="BRAIN_CONTEXTUAL_PREFIX")

    min_similarity: float | None = Field(
        None,
        alias="BRAIN_MIN_SIMILARITY",
        description="Abstain gate on the best cosine hit; per-embedder default.",
    )
    changelog_boost: float = Field(0.5, alias="BRAIN_CHANGELOG_BOOST")
    expert_boost: float = Field(1.5, alias="BRAIN_EXPERT_BOOST")

    site_url: str = Field("https://support.brilliantdirectories.com", alias="BRAIN_SITE_URL")
    user_agent: str = Field(
        "AITechConsultantBrain/0.1 (+https://github.com/GujjariSaiSanjana/ai-tech-consultant)",
        alias="BRAIN_USER_AGENT",
    )
    crawl_interval_s: float = Field(1.0, alias="BRAIN_CRAWL_INTERVAL_S")
    sync_hour: int = Field(3, alias="BRAIN_SYNC_HOUR")
    max_removed_fraction: float = Field(
        0.2,
        alias="BRAIN_MAX_REMOVED_FRACTION",
        description="Refuse removals above this share (broken sitemap).",
    )

    @property
    def sitemap_url(self) -> str:
        return f"{self.site_url}/support/sitemap.xml"

    @property
    def fallback_order(self) -> list[str]:
        return [name.strip().lower() for name in self.llm_fallback_order.split(",") if name.strip()]

    @property
    def resolved_embedder(self) -> EmbedderName:
        if self.embedder is not None:
            return self.embedder
        return "voyage" if self.voyage_api_key else "local"
