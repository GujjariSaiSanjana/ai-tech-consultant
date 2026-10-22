from __future__ import annotations

import json
from pathlib import Path

import pytest

from brain.config import Settings
from brain.evals.cases import CASES_PATH, EvalCase, load_cases
from brain.evals.run import run_cases, summarize, write_rows
from brain.evals.scoring import judge, reference_recall
from brain.ingest.indexer import Indexer
from brain.models import AskResponse, Citation, UsageOut
from tests.conftest import TEST_DB
from tests.helpers import fixture_article, make_settings

CONTRACT_KEYS = {
    "task",
    "run_id",
    "case_id",
    "split",
    "tags",
    "model",
    "rep",
    "passed",
    "score",
    "cost_usd",
    "latency_ms",
    "input_tokens",
    "output_tokens",
    "error",
    "mocked",
}


def _response(
    status: str = "answered",
    article_id: str = "1",
    label: str = "setting",
    image: str | None = None,
    answer: str = "Go to Settings and add the Facebook link",
) -> AskResponse:
    citations = [Citation(article_id=article_id, title="t", url="u", quote="q", image_url=image)]
    return AskResponse.model_validate(
        {
            "status": status,
            "answer": answer if status == "answered" else "",
            "label": label,
            "setting_path": None,
            "warnings": [],
            "citations": citations if status == "answered" else [],
            "confidence": 0.5,
            "gap_ticket_id": None if status == "answered" else "gap_1",
            "model": "m",
            "usage": UsageOut(input_tokens=1, output_tokens=1, cost_usd=0.0, latency_ms=1),
        }
    )


def _case(**kw: object) -> EvalCase:
    base: dict[str, object] = {
        "id": "c",
        "split": "dev",
        "tags": ["answerable"],
        "question": "q",
        "expect": "answer",
        "gold_article_ids": ["1"],
    }
    return EvalCase.model_validate(base | kw)


def test_answerable_needs_gold_citation_and_label() -> None:
    assert judge(_case(expected_label="setting"), _response()).passed
    assert not judge(_case(expected_label="addon"), _response()).passed
    assert not judge(_case(), _response(article_id="2")).passed
    assert not judge(_case(), _response(status="no_answer")).passed


def test_image_only_needs_a_cited_screenshot() -> None:
    case = _case(tags=["image_only"])
    assert not judge(case, _response()).passed
    assert judge(case, _response(image="https://s3/x.png")).passed


def test_unanswerable_needs_abstain_with_gap() -> None:
    case = _case(expect="no_answer", gold_article_ids=[], tags=["out_of_domain"])
    assert judge(case, _response(status="no_answer")).passed
    assert not judge(case, _response()).passed


def test_removed_article_citation_always_fails() -> None:
    case = _case(tags=["removed"], expect="no_answer", gold_article_ids=[], setup={"remove_article_ids": ["1"]})
    assert not judge(case, _response()).passed


def test_reference_recall() -> None:
    assert reference_recall("Settings > Social Media tab", "open the Social Media tab in Settings") == 1.0
    assert reference_recall("", "anything") == 1.0


def test_shipped_cases_are_valid_and_cover_the_required_kinds() -> None:
    cases = load_cases(CASES_PATH)
    assert len(cases) >= 25
    tags = {t for c in cases for t in c.tags}
    assert {"answerable", "image_only", "removed", "out_of_domain", "near_miss", "label"} <= tags
    heldout = sum(c.split == "heldout" for c in cases) / len(cases)
    assert 0.15 <= heldout <= 0.3


@pytest.mark.db
async def test_mock_run_writes_contract_rows(indexer: Indexer, tmp_path: Path) -> None:
    await indexer.index(fixture_article("5000558389"), lastmod=None, run_id=None)
    cases = [
        _case(
            id="tiktok",
            question="What URL format does the TikTok social media link need?",
            gold_article_ids=["5000558389"],
        ),
        _case(
            id="bread",
            question="How do I bake sourdough bread?",
            expect="no_answer",
            gold_article_ids=[],
            tags=["out_of_domain"],
        ),
    ]
    settings: Settings = make_settings(BRAIN_DATABASE_URL=TEST_DB)
    rows = await run_cases(settings, cases, run_id="brain-test", reps=1, force_mock=True)
    assert [r["passed"] for r in rows] == [True, True]
    assert all(set(r) == CONTRACT_KEYS and r["mocked"] for r in rows)
    path = write_rows(rows, "brain-test", tmp_path)
    assert [json.loads(line)["case_id"] for line in path.read_text().splitlines()] == ["tiktok", "bread"]
    assert "pass 2/2" in summarize(rows)
