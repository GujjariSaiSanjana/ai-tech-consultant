from __future__ import annotations

import os
from collections.abc import AsyncIterator, Callable

import psycopg
import pytest

from brain.crawl.engine import CrawlEngine
from brain.db import Pool, open_pool
from brain.ingest.embedder import HashEmbedder
from brain.ingest.images import AltTextDescriber
from brain.ingest.indexer import Indexer
from brain.llm.answerer import Answerer, OfflineAnswerer
from brain.llm.classifier import Classifier, RuleClassifier
from brain.migrate import migrate
from brain.repo import Repository
from brain.service import BrainService, RetrievalPolicy
from tests.helpers import SITE, FakeSite

TEST_DB = os.environ.get("BRAIN_TEST_DATABASE_URL", "postgresql://postgres:postgres@localhost:5432/brain_test")
TRUNCATE = "TRUNCATE answer_feedback, llm_calls, chunks, images, gap_tickets, articles, crawl_items, crawl_runs CASCADE"


@pytest.fixture(scope="session")
async def db_pool() -> AsyncIterator[Pool]:
    try:
        async with await psycopg.AsyncConnection.connect(TEST_DB, autocommit=True) as conn:
            await conn.execute("DROP SCHEMA IF EXISTS public CASCADE")
            await conn.execute("CREATE SCHEMA public")
    except psycopg.OperationalError as exc:
        pytest.skip(f"test database unavailable: {exc}")
    await migrate(TEST_DB)
    pool = await open_pool(TEST_DB, max_size=5)
    yield pool
    await pool.close()


@pytest.fixture
async def repo(db_pool: Pool) -> Repository:
    async with db_pool.connection() as conn:
        await conn.execute(TRUNCATE)
    return Repository(db_pool)


@pytest.fixture
def embedder() -> HashEmbedder:
    return HashEmbedder()


@pytest.fixture
def indexer(repo: Repository, embedder: HashEmbedder) -> Indexer:
    return Indexer(repo, embedder, AltTextDescriber())


ServiceFactory = Callable[..., BrainService]


@pytest.fixture
def make_service(repo: Repository, embedder: HashEmbedder) -> ServiceFactory:
    def build(
        answerer: Answerer | None = None, classifier: Classifier | None = None, min_similarity: float = 0.05
    ) -> BrainService:
        policy = RetrievalPolicy(
            min_similarity=min_similarity, changelog_boost=0.5, expert_boost=1.5, public_url="http://brain.test"
        )
        return BrainService(repo, embedder, answerer or OfflineAnswerer(), classifier or RuleClassifier(), policy)

    return build


@pytest.fixture
def site() -> FakeSite:
    return FakeSite()


@pytest.fixture
def engine(repo: Repository, indexer: Indexer, site: FakeSite) -> CrawlEngine:
    return CrawlEngine(repo, site.fetch, indexer, sitemap_url=f"{SITE}/support/sitemap.xml")
