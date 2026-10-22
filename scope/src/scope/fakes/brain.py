"""Keyword-matching stand-in for the Knowledge Brain, backed by a small fictional fixture."""

from __future__ import annotations

import json
from importlib.resources import files

from pydantic import BaseModel

from scope.brain.client import BrainUnavailableError, FeasibilityAnswer, SearchAnswer, SearchHit
from scope.domain.models import Citation, Kind


class FixtureArticle(BaseModel):
    article_id: str
    title: str
    url: str
    kind: Kind
    setting_path: str | None
    keywords: list[str]
    quote: str


def load_fixture() -> list[FixtureArticle]:
    raw = json.loads(files("scope.fakes").joinpath("brain_fixture.json").read_text("utf-8"))
    return [FixtureArticle.model_validate(a) for a in raw["articles"]]


class FakeBrain:
    def __init__(self, articles: list[FixtureArticle] | None = None, *, down: bool = False) -> None:
        self.articles = articles if articles is not None else load_fixture()
        self.down = down
        self.calls: list[tuple[str, str]] = []

    def _rank(self, text: str) -> list[tuple[int, FixtureArticle]]:
        low = text.lower()
        scored = [(sum(1 for k in a.keywords if k in low), a) for a in self.articles]
        return sorted((s for s in scored if s[0] > 0), key=lambda s: -s[0])

    async def feasibility(self, requirement: str) -> FeasibilityAnswer:
        self.calls.append(("feasibility", requirement))
        if self.down:
            raise BrainUnavailableError("fake brain is down")
        ranked = self._rank(requirement)
        if not ranked:
            return FeasibilityAnswer(
                kind="unknown",
                setting_path=None,
                confidence=0.1,
                rationale="No help-center article matched.",
                citations=[],
            )
        hits, best = ranked[0]
        return FeasibilityAnswer(
            kind=best.kind,
            setting_path=best.setting_path,
            rationale=best.quote,
            confidence=min(0.95, 0.5 + 0.15 * hits),
            citations=[
                Citation(
                    article_id=best.article_id, title=best.title, url=best.url, quote=best.quote
                )
            ],
        )

    async def search(self, query: str, top_k: int = 5) -> SearchAnswer:
        self.calls.append(("search", query))
        if self.down:
            raise BrainUnavailableError("fake brain is down")
        return SearchAnswer(
            results=[
                SearchHit(
                    chunk_id=f"{a.article_id}-0",
                    article_id=a.article_id,
                    title=a.title,
                    url=a.url,
                    category="fixture",
                    folder="fixture",
                    kind="text",
                    text=a.quote,
                    score=float(s),
                )
                for s, a in self._rank(query)[:top_k]
            ]
        )
