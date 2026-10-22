"""Full pipeline on the fake LLM and fake Brain, initial and follow-up."""

from __future__ import annotations

from pydantic import BaseModel

from scope.brain.tools import BrainToolBox
from scope.config import Settings
from scope.domain.diff import OUT_OF_SCOPE_FLAG
from scope.domain.pert import summarize
from scope.domain.transcript import parse
from scope.fakes.brain import FakeBrain
from scope.fakes.llm import FakeLLM
from scope.llm.session import LLMSession
from scope.orchestrator import Orchestrator, SavedStages, Stage
from tests.conftest import read
from tests.scripted import fake_with, gold_like_reconciler, reviewer_sequence

M1 = parse(read("meeting-01-kickoff.txt"), "m1")
M2 = parse(read("meeting-02-deep-dive.txt"), "m2")
M3 = parse(read("meeting-03-follow-up.txt"), "m3")


def orchestrator(
    settings: Settings,
    fake: FakeLLM,
    brain: FakeBrain | None = None,
    stages: dict[str, BaseModel] | None = None,
) -> Orchestrator:
    brain = brain or FakeBrain()
    session = LLMSession(fake, settings, tools=BrainToolBox(brain), mocked=True)

    async def on_stage(stage: Stage, out: BaseModel) -> None:
        if stages is not None:
            stages[stage] = out

    return Orchestrator(session, brain, on_stage=on_stage)


async def test_initial_pipeline_end_to_end(settings: Settings) -> None:
    fake, brain = FakeLLM(), FakeBrain()
    stages: dict[str, BaseModel] = {}
    snap = await orchestrator(settings, fake, brain, stages).run_initial([M1, M2])
    assert snap.requirements and snap.questions and snap.items
    covered = {rid for i in snap.items for rid in i.requirement_ids}
    assert covered == {r.id for r in snap.requirements}
    assert snap.estimate == summarize(snap.items)
    assert {f.requirement_id for f in snap.feasibility} == {r.id for r in snap.requirements}
    assert len([c for c in brain.calls if c[0] == "feasibility"]) == len(snap.requirements)
    assert set(stages) == {"requirements", "questions", "feasibility", "estimate"}
    agents = [r.agent for r in fake.requests]
    assert agents[:2] == ["extractor", "questioner"] or agents[:2] == ["questioner", "extractor"]
    assert agents[2:] == ["feasibility", "scoper", "reviewer"]
    # every agent saw the same transcript prefix, so the cache can be shared per model
    assert len({r.context for r in fake.requests}) == 1


async def test_review_loop_revises_then_stops(settings: Settings) -> None:
    fake = fake_with(reviewer=reviewer_sequence(["revise", "approve"]))
    snap = await orchestrator(settings, fake).run_initial([M1])
    scoper_calls = [r for r in fake.requests if r.agent == "scoper"]
    assert len(scoper_calls) == 2
    assert "QA effort is missing" in scoper_calls[1].payload
    assert snap.review is not None and snap.review.loops == 2 and snap.review.approved_by_reviewer


async def test_review_loop_is_bounded(settings: Settings) -> None:
    fake = fake_with(reviewer=reviewer_sequence(["revise"]))
    snap = await orchestrator(settings, fake).run_initial([M1])
    assert len([r for r in fake.requests if r.agent == "scoper"]) == settings.max_review_loops
    assert snap.review is not None and not snap.review.approved_by_reviewer


async def test_rerun_from_estimate_reuses_earlier_stages(settings: Settings) -> None:
    stages: dict[str, BaseModel] = {}
    await orchestrator(settings, FakeLLM(), stages=stages).run_initial([M1])
    saved = SavedStages.model_validate({k: v.model_dump() for k, v in stages.items()})
    fake = FakeLLM()
    snap = await orchestrator(settings, fake).run_initial([M1], saved=saved, from_stage="estimate")
    assert {r.agent for r in fake.requests} == {"scoper", "reviewer"}
    assert snap.requirements == saved.requirements.requirements  # type: ignore[union-attr]


async def test_brain_down_pipeline_still_completes_with_unknowns(settings: Settings) -> None:
    snap = await orchestrator(settings, FakeLLM(), FakeBrain(down=True)).run_initial([M1])
    assert snap.feasibility and {f.kind for f in snap.feasibility} == {"unknown"}
    assert all(i.kind == "unknown" for i in snap.items)


async def test_follow_up_updates_instead_of_starting_over(settings: Settings) -> None:
    v1 = await orchestrator(settings, FakeLLM()).run_initial([M1, M2])
    fake = fake_with(reconciler=gold_like_reconciler(M3))
    v2 = await orchestrator(settings, fake).run_follow_up(
        v1, prior_version=1, transcripts=[M1, M2, M3], follow_up_ids=["m3"]
    )
    log = v2.changelog
    assert log is not None and (log.from_version, log.to_version) == (1, 2)
    first, second = v1.requirements[0], v1.requirements[1]
    assert [m.id for m in log.modified] == [first.id]
    assert [r.id for r in log.removed] == [second.id]
    assert len(log.added) == 1 and len(log.out_of_scope) == 1
    assert len(log.unchanged_ids) == len(v1.requirements) - 2
    # untouched items are carried over as is; only touched or new requirements are re-scoped
    v1_items = {i.id: i for i in v1.items}
    untouched = [i for i in v1.items if not {first.id, second.id} & set(i.requirement_ids)]
    assert all(v1_items[i.id] in v2.items for i in untouched)
    scoper = next(r for r in fake.requests if r.agent == "scoper")
    assert scoper.payload.count('"id": "REQ-') == 3
    # out-of-scope request is quoted separately and excluded from the agreed price
    oos = log.out_of_scope[0]
    assert OUT_OF_SCOPE_FLAG in oos.flags
    [cr] = log.change_requests
    assert cr.requirement_ids == [oos.id] and cr.delta_hours > 0 and cr.price_usd > 0
    assert v2.estimate is not None
    assert (
        v2.estimate.expected_hours
        == summarize([i for i in v2.items if oos.id not in i.requirement_ids]).expected_hours
    )
    assert log.delta_hours_by_requirement[second.id] < 0
    # feasibility was only re-checked for changed requirements
    feas_req = next(r for r in fake.requests if r.agent == "feasibility")
    assert feas_req.payload.count('"requirement_id"') == 3


async def test_empty_extraction_yields_empty_scope(settings: Settings) -> None:
    from scope.agents.extractor import ExtractionOutput

    fake = fake_with(extractor=lambda r, s: ExtractionOutput(requirements=[], discarded=[]))
    snap = await orchestrator(settings, fake).run_initial([M1])
    assert snap.requirements == [] and snap.items == []
    assert snap.estimate is not None and snap.estimate.expected_hours == 0
    assert "scoper" not in {r.agent for r in fake.requests}
