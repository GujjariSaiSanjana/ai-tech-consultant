from __future__ import annotations

import json
from pathlib import Path

from scope.config import Settings
from scope.evals import scoring
from scope.evals.gold import load_gold, matches
from scope.evals.run import CASES, Sink, load_cases, run
from tests.factories import req

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


def test_cases_file_shape() -> None:
    cases = load_cases(CASES, "all")
    assert len(cases) >= 20
    assert len({c.case_id for c in cases}) == len(cases)
    heldout = [c for c in cases if c.split == "heldout"]
    assert 0.15 <= len(heldout) / len(cases) <= 0.3
    tags = {t for c in cases for t in c.tags}
    assert {"injection", "empty", "huge", "garbled", "follow_up", "feasibility"} <= tags


def test_gold_keyword_matching() -> None:
    gold = load_gold("trailhead")
    g05 = gold.by_id("G05")
    assert matches(g05, req("Suspend guides automatically when their card payment fails."))
    assert not matches(g05, req("Guides can upload photos."))


def test_requirement_f1_counts_traps() -> None:
    gold = load_gold("trailhead")
    reqs = [req("Guides are suspended when payment fails."), req("A community forum for guides.")]
    s, d = scoring.requirement_f1([gold.by_id("G05")], gold.not_requirements, reqs)
    assert d["recall"] == 1 and d["precision"] == 0.5 and d["matched_not_requirements"]
    assert 0 < s < 1


def test_mock_run_writes_contract_rows(settings: Settings, tmp_path: Path) -> None:
    import asyncio

    cases = [
        c
        for c in load_cases(CASES, "all")
        if c.case_id in ("schema-trailhead", "estimate-sanity-trailhead", "robust-empty")
    ]
    sink = Sink(tmp_path / "out", tmp_path / "raw")
    asyncio.run(run(cases, models=[None], reps=1, mock=True, base=settings, sink=sink))
    sink.close()
    path = sink.path
    rows = [json.loads(line) for line in path.read_text().splitlines()]
    assert len(rows) == 3
    for r in rows:
        assert set(r) == CONTRACT_KEYS
        assert r["task"] == "scope" and r["mocked"] is True and r["model"].startswith("mock/")
        assert r["cost_usd"] == 0 and r["error"] is None
    shared = [r for r in rows if "shared_run" in r["tags"]]
    assert len(shared) == 1  # two cases scored one pipeline run
    assert (tmp_path / "raw" / path.name).exists()
