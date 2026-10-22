from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from brain.api.errors import install_error_handlers
from brain.api.routes import public, v1
from brain.api.ui import ui
from brain.config import DEFAULT_API_KEY, Settings
from brain.container import Container, build_container
from brain.migrate import migrate

log = logging.getLogger(__name__)
DESCRIPTION = """Memory for the Brilliant Directories Solution Center: hybrid retrieval, cited answers that say
when they do not know, a solution label (`setting | addon | widget_customization | custom_dev | unknown`), and an
expert loop that feeds human answers back in.

Send `X-API-Key: $BRAIN_API_KEY` on every `/v1` request. Errors are `{"error": {"code", "message"}}`.
Contract: `docs/contracts/brain-api.md`."""


def create_app(container: Container | None = None, settings: Settings | None = None) -> FastAPI:
    """`container` is injected by tests; otherwise one is built from `settings` at startup."""

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        app.state.background = set()
        if container is not None:
            app.state.container = container
            yield
        else:
            resolved = settings or Settings()
            if resolved.api_key == DEFAULT_API_KEY:
                log.warning("BRAIN_API_KEY is the development default; set a real secret before exposing the API")
            await migrate(resolved.database_url)
            async with build_container(resolved) as built:
                app.state.container = built
                yield
        pending: set[asyncio.Task[None]] = app.state.background
        for task in pending:
            task.cancel()

    app = FastAPI(title="Knowledge Brain API", version="1.0.0", description=DESCRIPTION, lifespan=lifespan)
    install_error_handlers(app)
    app.include_router(public)
    app.include_router(v1)
    app.include_router(ui)
    return app
