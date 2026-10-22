from __future__ import annotations

from collections.abc import Sequence

import pytest

from brain.domain import Label, SearchHit
from brain.ingest.indexer import Indexer
from brain.llm.answerer import AnswerDraft, DraftCitation
from brain.llm.classifier import WIDGET_WARNING, Classification, finalize
from brain.llm.client import Usage
from brain.repo import Repository
from tests.conftest import ServiceFactory
from tests.helpers import fixture_article

pytestmark = pytest.mark.db


class QuotingAnswerer:
    """Stands in for Claude: answers from the first hit and cites it, or abstains when told to."""

    model = "fake-claude"

    def __init__(self, abstain: bool = False) -> None:
        self.abstain = abstain
        self.seen: list[list[SearchHit]] = []

    async def answer(self, question: str, hits: Sequence[SearchHit]) -> AnswerDraft:
        self.seen.append(list(hits))
        if self.abstain or not hits:
            return AnswerDraft("no_answer", "", [], self.model, Usage(100, 5, 0.0003))
        quote = hits[0].text.split("\n")[-1][:120]
        return AnswerDraft("answered", f"See: {quote}", [DraftCitation(0, quote)], self.model, Usage(100, 20, 0.001))


class FixedClassifier:
    def __init__(self, label: Label) -> None:
        self.label = label

    async def classify(
        self, request: str, answer: str | None, hits: Sequence[SearchHit]
    ) -> tuple[Classification, Usage]:
        result = Classification(label=self.label, setting_path=None, warnings=[], rationale="r", confidence=0.9)
        return finalize(result, hits), Usage(50, 10, 0.0001)


async def test_answer_has_citations_label_and_setting_path(indexer: Indexer, make_service: ServiceFactory) -> None:
    await indexer.index(fixture_article("5000558389"), lastmod=None, run_id=None)
    service = make_service(answerer=QuotingAnswerer(), classifier=FixedClassifier("setting"))
    result = await service.ask("Where do I add the website's Facebook link?")
    assert result.status == "answered"
    assert result.citations[0].article_id == "5000558389"
    assert result.citations[0].quote in result.answer
    assert result.label == "setting"
    assert result.setting_path == "Settings > General Settings > Social Media Tab"
    assert result.usage.input_tokens == 150 and result.usage.cost_usd == pytest.approx(0.0011)
    assert 0 < result.confidence <= 1 and result.gap_ticket_id is None


async def test_widget_customization_always_warns(indexer: Indexer, make_service: ServiceFactory) -> None:
    await indexer.index(fixture_article("5000558389"), lastmod=None, run_id=None)
    service = make_service(answerer=QuotingAnswerer(), classifier=FixedClassifier("widget_customization"))
    result = await service.ask("Change the footer widget that shows the social links")
    assert WIDGET_WARNING in result.warnings


async def test_abstain_creates_one_gap_ticket(indexer: Indexer, make_service: ServiceFactory, repo: Repository) -> None:
    await indexer.index(fixture_article("5000558389"), lastmod=None, run_id=None)
    service = make_service(answerer=QuotingAnswerer(abstain=True))
    first = await service.ask("Does it integrate with my Shopify store?")
    assert first.status == "no_answer" and first.answer == "" and first.citations == []
    assert first.gap_ticket_id is not None
    again = await service.ask("does it integrate with my shopify store? ")
    assert again.gap_ticket_id == first.gap_ticket_id
    gaps = await service.list_gaps("open")
    assert [g.id for g in gaps] == [first.gap_ticket_id]
    assert gaps[0].bot_attempt and gaps[0].bot_attempt.startswith("Bot abstained")


async def test_similarity_gate_abstains_without_calling_the_model(
    indexer: Indexer, make_service: ServiceFactory
) -> None:
    await indexer.index(fixture_article("5000558389"), lastmod=None, run_id=None)
    answerer = QuotingAnswerer()
    result = await make_service(answerer=answerer, min_similarity=0.99).ask("What is the capital of France?")
    assert result.status == "no_answer" and answerer.seen == []


async def test_offline_answerer_quotes_or_abstains(indexer: Indexer, make_service: ServiceFactory) -> None:
    await indexer.index(fixture_article("5000558389"), lastmod=None, run_id=None)
    service = make_service()
    answered = await service.ask("What URL format does the TikTok social media link need?")
    assert answered.status == "answered" and answered.model == "offline-extractive"
    assert any("tiktok.com" in c.quote.lower() for c in answered.citations)
    unknown = await service.ask("How do I bake sourdough bread at home?")
    assert unknown.status == "no_answer" and unknown.gap_ticket_id


async def test_expert_answer_becomes_retrievable_and_citable(indexer: Indexer, make_service: ServiceFactory) -> None:
    await indexer.index(fixture_article("5000558389"), lastmod=None, run_id=None)
    service = make_service(answerer=QuotingAnswerer(abstain=True))
    gap_id = (await service.ask("Can members pay with Klarna installments?")).gap_ticket_id
    assert gap_id
    answered = await service.answer_gap(gap_id, "No. Klarna is not a supported payment gateway; use Stripe.", "Ana")
    hits = await service.search("Can members pay with Klarna installments?")
    assert hits[0].chunk_id == answered.chunk_id and hits[0].kind == "expert"
    assert hits[0].article_id == f"expert:{gap_id}"

    citing = make_service(answerer=QuotingAnswerer(), classifier=FixedClassifier("unknown"))
    result = await citing.ask("Can members pay with Klarna installments?")
    assert result.status == "answered" and result.citations[0].article_id == f"expert:{gap_id}"
    assert [g.id for g in await service.list_gaps("answered")] == [gap_id]


async def test_removed_article_is_excluded_from_answers(
    indexer: Indexer, make_service: ServiceFactory, repo: Repository
) -> None:
    for aid in ("5000558389", "12000048777"):
        await indexer.index(fixture_article(aid), lastmod=None, run_id=None)
    answerer = QuotingAnswerer()
    service = make_service(answerer=answerer)
    await repo.mark_removed("5000558389")
    result = await service.ask("Where do I add the website's Facebook link?")
    assert all(c.article_id != "5000558389" for c in result.citations)
    assert all(h.article_id != "5000558389" for call in answerer.seen for h in call)


async def test_feasibility_returns_label_and_citations(indexer: Indexer, make_service: ServiceFactory) -> None:
    await indexer.index(fixture_article("5000558389"), lastmod=None, run_id=None)
    service = make_service(answerer=QuotingAnswerer(), classifier=FixedClassifier("setting"))
    result = await service.feasibility("Show our Instagram and TikTok profiles in the site footer")
    assert result.kind == "setting" and result.citations
    assert result.setting_path == "Settings > General Settings > Social Media Tab"
