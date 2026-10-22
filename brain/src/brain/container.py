"""Composition root: builds the object graph once per process from Settings."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path

import httpx

from brain.config import Settings
from brain.crawl.engine import CrawlEngine
from brain.crawl.fetcher import PoliteFetcher
from brain.db import Pool, open_pool
from brain.ingest.contextualizer import Contextualizer
from brain.ingest.embedder import Embedder, build_embedder
from brain.ingest.images import AltTextDescriber, ClaudeImageDescriber, ImageDescriber
from brain.ingest.indexer import Indexer
from brain.llm.answerer import Answerer, ClaudeAnswerer, OfflineAnswerer
from brain.llm.classifier import Classifier, ClaudeClassifier, RuleClassifier
from brain.llm.client import LLMClient, build_anthropic
from brain.repo import Repository
from brain.service import BrainService, RetrievalPolicy


@dataclass(frozen=True, slots=True)
class Container:
    settings: Settings
    pool: Pool
    repo: Repository
    embedder: Embedder
    service: BrainService
    indexer: Indexer
    site_http: httpx.AsyncClient
    llm: LLMClient | None

    @property
    def llm_live(self) -> bool:
        return self.llm is not None

    async def crawl_engine(self, *, save_html_dir: Path | None = None) -> CrawlEngine:
        fetcher = await PoliteFetcher.create(
            self.site_http,
            site_url=self.settings.site_url,
            user_agent=self.settings.user_agent,
            interval_s=self.settings.crawl_interval_s,
        )
        return CrawlEngine(
            self.repo,
            fetcher.get,
            self.indexer,
            sitemap_url=self.settings.sitemap_url,
            max_removed_fraction=self.settings.max_removed_fraction,
            save_html_dir=save_html_dir,
        )


@asynccontextmanager
async def build_container(settings: Settings | None = None) -> AsyncIterator[Container]:
    settings = settings or Settings()
    pool = await open_pool(settings.database_url)
    repo = Repository(pool)
    site_http = httpx.AsyncClient(headers={"User-Agent": settings.user_agent}, timeout=30.0, follow_redirects=True)
    api_http = httpx.AsyncClient(timeout=60.0)
    anthropic_client = build_anthropic(settings)
    try:
        embedder = build_embedder(settings, api_http)
        answerer: Answerer
        classifier: Classifier
        describer: ImageDescriber
        contextualizer: Contextualizer | None = None
        llm: LLMClient | None = None
        if anthropic_client is not None:
            llm = LLMClient(anthropic_client, settings, repo)
            answerer, classifier, describer = (
                ClaudeAnswerer(llm),
                ClaudeClassifier(llm),
                ClaudeImageDescriber(llm),
            )
            if settings.contextual_prefix:
                contextualizer = Contextualizer(llm)
        else:
            answerer, classifier, describer = OfflineAnswerer(), RuleClassifier(), AltTextDescriber()
        policy = RetrievalPolicy(
            min_similarity=(
                settings.min_similarity if settings.min_similarity is not None else embedder.default_min_similarity
            ),
            changelog_boost=settings.changelog_boost,
            expert_boost=settings.expert_boost,
            public_url=settings.public_url,
        )
        yield Container(
            settings=settings,
            pool=pool,
            repo=repo,
            embedder=embedder,
            service=BrainService(repo, embedder, answerer, classifier, policy),
            indexer=Indexer(repo, embedder, describer, contextualizer),
            site_http=site_http,
            llm=llm,
        )
    finally:
        await site_http.aclose()
        await api_http.aclose()
        if anthropic_client is not None:
            await anthropic_client.close()
        await pool.close()
