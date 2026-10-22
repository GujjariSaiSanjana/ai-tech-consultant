from __future__ import annotations

import asyncio
import logging
import secrets
from collections.abc import Awaitable
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Header, HTTPException, Request, status

from brain.container import Container
from brain.models import (
    ArticleResponse,
    AskRequest,
    AskResponse,
    ErrorResponse,
    FeasibilityRequest,
    FeasibilityResponse,
    GapAnswered,
    GapAnswerRequest,
    GapCreated,
    GapCreateRequest,
    GapList,
    HealthResponse,
    SearchRequest,
    SearchResponse,
    SyncRun,
    SyncStarted,
)
from brain.service import BrainService, NotFoundError

log = logging.getLogger(__name__)
ERRORS: dict[int | str, dict[str, object]] = {
    401: {"model": ErrorResponse, "description": "Missing or wrong X-API-Key"},
    422: {"model": ErrorResponse, "description": "Invalid request body"},
}


def container(request: Request) -> Container:
    c: Container = request.app.state.container
    return c


def service(c: Annotated[Container, Depends(container)]) -> BrainService:
    return c.service


def require_api_key(
    c: Annotated[Container, Depends(container)],
    x_api_key: Annotated[str | None, Header(description="Shared secret, `BRAIN_API_KEY`")] = None,
) -> None:
    if x_api_key is None or not secrets.compare_digest(x_api_key, c.settings.api_key):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "missing or invalid X-API-Key header")


Svc = Annotated[BrainService, Depends(service)]
public = APIRouter()
v1 = APIRouter(prefix="/v1", dependencies=[Depends(require_api_key)], responses=ERRORS)


@public.get("/health", response_model=HealthResponse, tags=["ops"])
async def health(svc: Svc) -> HealthResponse:
    """Liveness plus index size. Needs no API key so container health checks can call it."""
    articles, chunks, last_sync = await svc.health()
    return HealthResponse(status="ok", articles=articles, chunks=chunks, last_sync=last_sync)


@v1.post("/ask", response_model=AskResponse, tags=["answers"])
async def ask(body: AskRequest, svc: Svc) -> AskResponse:
    """Answer from the help center with citations, a solution label and warnings.

    When the documentation does not contain the answer, `status` is `no_answer` and a gap ticket
    for a human expert is opened (its id is in `gap_ticket_id`).
    """
    return await svc.ask(body.question, body.top_k, body.category)


@v1.post("/search", response_model=SearchResponse, tags=["answers"])
async def search(body: SearchRequest, svc: Svc) -> SearchResponse:
    """Hybrid (vector + full-text, RRF-fused) retrieval over active articles and expert answers."""
    return SearchResponse(results=await svc.search_results(body.query, body.top_k, body.category))


@v1.post("/feasibility", response_model=FeasibilityResponse, tags=["answers"])
async def feasibility(body: FeasibilityRequest, svc: Svc) -> FeasibilityResponse:
    """Is a requirement an existing setting, an add-on, a widget customization or custom development?"""
    return await svc.feasibility(body.requirement)


@v1.get(
    "/articles/{article_id}",
    response_model=ArticleResponse,
    tags=["articles"],
    responses={404: {"model": ErrorResponse, "description": "Unknown article"}},
)
async def article(article_id: str, svc: Svc) -> ArticleResponse:
    return await svc.article(article_id)


@v1.post("/gaps", response_model=GapCreated, tags=["experts"])
async def create_gap(body: GapCreateRequest, svc: Svc) -> GapCreated:
    """Open an expert task. An identical open question is reused rather than duplicated."""
    gap_id = await svc.create_gap(body.question, body.context, body.source)
    return GapCreated(id=gap_id, status="open")


@v1.get("/gaps", response_model=GapList, tags=["experts"])
async def list_gaps(svc: Svc, status: Literal["open", "answered"] | None = None) -> GapList:
    return GapList(items=await svc.list_gaps(status))


@v1.post(
    "/gaps/{gap_id}/answer",
    response_model=GapAnswered,
    tags=["experts"],
    responses={404: {"model": ErrorResponse, "description": "Unknown gap"}},
)
async def answer_gap(gap_id: str, body: GapAnswerRequest, svc: Svc) -> GapAnswered:
    """Store the expert's answer as a curated `expert` chunk, retrievable immediately."""
    return await svc.answer_gap(gap_id, body.answer, body.expert)


@v1.post("/sync/run", response_model=SyncStarted, status_code=status.HTTP_202_ACCEPTED, tags=["sync"])
async def run_sync(request: Request, c: Annotated[Container, Depends(container)]) -> SyncStarted:
    """Start an incremental sync in the background; poll `GET /v1/sync/runs/{run_id}`.

    If another crawl or sync holds the run lock, the new run finishes as `failed` with that reason.
    """
    engine = await c.crawl_engine()
    run_id = await engine.start("sync")
    task = asyncio.create_task(_run_in_background(engine.execute(run_id)))
    tasks: set[asyncio.Task[None]] = request.app.state.background
    tasks.add(task)
    task.add_done_callback(tasks.discard)
    return SyncStarted(run_id=run_id)


@v1.get(
    "/sync/runs/{run_id}",
    response_model=SyncRun,
    tags=["sync"],
    responses={404: {"model": ErrorResponse, "description": "Unknown run"}},
)
async def sync_status(run_id: str, c: Annotated[Container, Depends(container)]) -> SyncRun:
    run = await c.repo.get_run(run_id)
    if run is None:
        raise NotFoundError(f"run {run_id} not found")
    return SyncRun(run_id=run["id"], **{k: run[k] for k in SyncRun.model_fields if k != "run_id"})


async def _run_in_background(work: Awaitable[object]) -> None:
    try:
        await work
    except Exception:
        log.exception("background sync failed")
