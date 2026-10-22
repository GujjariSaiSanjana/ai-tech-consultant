"""Build the service graph (settings -> db -> deps -> service) for the CLI and the web app."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from scope.config import Settings
from scope.db.engine import create_schema, make_engine, session_factory
from scope.db.repo import Repo
from scope.service import ScopeService
from scope.wiring import Deps, build_deps


@asynccontextmanager
async def open_service(settings: Settings, deps: Deps | None = None) -> AsyncIterator[ScopeService]:
    engine = make_engine(settings.database_url)
    await create_schema(engine)
    try:
        yield ScopeService(Repo(session_factory(engine)), settings, deps or build_deps(settings))
    finally:
        await engine.dispose()
