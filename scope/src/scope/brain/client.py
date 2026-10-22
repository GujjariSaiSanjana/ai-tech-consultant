"""Typed HTTP client for the Knowledge Brain. Never imports Brain code."""

from __future__ import annotations

from typing import Protocol

import httpx
from pydantic import BaseModel, ValidationError

from scope.domain.models import Citation, Kind


class BrainUnavailableError(Exception):
    """The Brain could not answer (down, timeout, 5xx, bad payload). Callers must not guess."""


class FeasibilityAnswer(BaseModel):
    kind: Kind
    setting_path: str | None
    rationale: str
    confidence: float
    citations: list[Citation]


class SearchHit(BaseModel):
    chunk_id: str
    article_id: str
    title: str
    url: str
    category: str
    folder: str
    kind: str
    text: str
    score: float


class SearchAnswer(BaseModel):
    results: list[SearchHit]


class BrainClient(Protocol):
    async def feasibility(self, requirement: str) -> FeasibilityAnswer: ...

    async def search(self, query: str, top_k: int = 5) -> SearchAnswer: ...


class HttpBrainClient:
    def __init__(
        self,
        base_url: str,
        api_key: str,
        *,
        timeout_s: float,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._http = httpx.AsyncClient(
            base_url=base_url.rstrip("/"),
            headers={"X-API-Key": api_key},
            timeout=timeout_s,
            transport=transport,
        )

    async def _post[M: BaseModel](self, path: str, body: dict[str, object], model: type[M]) -> M:
        try:
            resp = await self._http.post(path, json=body)
        except httpx.HTTPError as exc:
            raise BrainUnavailableError(f"{path}: {type(exc).__name__}: {exc}") from exc
        if resp.status_code != 200:
            try:
                err = resp.json().get("error", {})
                detail = f"{err.get('code')}: {err.get('message')}"
            except ValueError:
                detail = resp.text[:200]
            raise BrainUnavailableError(f"{path}: HTTP {resp.status_code} {detail}")
        try:
            return model.model_validate(resp.json())
        except (ValueError, ValidationError) as exc:
            raise BrainUnavailableError(f"{path}: response does not match contract: {exc}") from exc

    async def feasibility(self, requirement: str) -> FeasibilityAnswer:
        return await self._post("/v1/feasibility", {"requirement": requirement}, FeasibilityAnswer)

    async def search(self, query: str, top_k: int = 5) -> SearchAnswer:
        return await self._post("/v1/search", {"query": query, "top_k": top_k}, SearchAnswer)

    async def aclose(self) -> None:
        await self._http.aclose()
