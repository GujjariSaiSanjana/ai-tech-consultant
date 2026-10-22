"""Service workflows: approval gate, audit log, proposal states, export, run failures."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from pathlib import Path

import pytest

from scope.bootstrap import open_service
from scope.config import Settings
from scope.domain.models import Proposal
from scope.fakes.brain import FakeBrain
from scope.fakes.llm import FakeLLM
from scope.llm.errors import LLMUnavailableError
from scope.service import GateError, ScopeService
from scope.wiring import Deps
from tests.conftest import read
from tests.scripted import fake_with, gold_like_reconciler


@pytest.fixture
async def svc(settings: Settings) -> AsyncIterator[ScopeService]:
    async with open_service(settings, Deps(FakeLLM(), FakeBrain(), mocked=True)) as s:
        yield s


async def initial_version(svc: ScopeService) -> tuple[int, int]:
    p = await svc.repo.create_project("GuideFinder", "Trailhead Media")
    await svc.add_meeting(p.id, read("meeting-01-kickoff.txt"))
    run = await svc.start_run(p.id)
    v = await svc.execute_run(run.id)
    assert v is not None
    return p.id, v.id


async def test_proposal_is_blocked_until_scope_is_approved(svc: ScopeService) -> None:
    pid, vid = await initial_version(svc)
    with pytest.raises(GateError, match="approve the scope"):
        await svc.generate_proposal(vid, "Sam")
    with pytest.raises(GateError, match="audit log"):
        await svc.approve_scope(vid, "  ")
    await svc.approve_scope(vid, "Sam")
    with pytest.raises(GateError):
        await svc.approve_scope(vid, "Sam")
    row = await svc.generate_proposal(vid, "Sam")
    assert row.state == "draft"
    names = [s.name for s in Proposal.model_validate(row.document).sections]
    assert "price" in names and "change_requests" not in names
    audit = [(a.action, a.actor) for a in await svc.repo.list_audit(pid)]
    assert audit == [("generate", "Sam"), ("approve_scope", "Sam")]


async def test_follow_up_is_blocked_while_scope_is_draft(svc: ScopeService) -> None:
    pid, _ = await initial_version(svc)
    await svc.add_meeting(pid, read("meeting-03-follow-up.txt"))
    with pytest.raises(GateError, match="draft"):
        await svc.start_run(pid)
    with pytest.raises(GateError, match="draft"):
        await svc.check_can_process(pid)
    m, created = await svc.add_meeting(pid, read("meeting-03-follow-up.txt"))
    assert not created
    assert m.meeting_key == "m2"  # adding the same text again is idempotent
    with pytest.raises(GateError, match="no new meeting"):
        await svc.start_run((await svc.repo.create_project("x", "y")).id)


async def test_proposal_lifecycle_with_audit_and_export(svc: ScopeService) -> None:
    pid, vid = await initial_version(svc)
    await svc.approve_scope(vid, "Sam")
    row = await svc.generate_proposal(vid, "Sam")
    with pytest.raises(GateError):
        await svc.transition(row.id, "send", "Sam")  # draft cannot be sent
    row = await svc.edit_section(row.id, "summary", "A better summary.", "Rina")
    assert row.revision == 2
    row = await svc.regenerate_section(row.id, "scope", "Rina", "shorter")
    assert "shorter" in Proposal.model_validate(row.document).section("scope").content
    row = await svc.transition(row.id, "submit_for_review", "Sam")
    with pytest.raises(GateError, match="every section"):
        await svc.transition(row.id, "approve", "Sam")
    for s in Proposal.model_validate(row.document).sections:
        row = await svc.approve_section(row.id, s.name, "Marcus")
    row = await svc.transition(row.id, "approve", "Marcus")
    # editing an approved proposal sends it back to review
    row = await svc.edit_section(row.id, "timeline", "Six weeks.", "Sam")
    assert row.state == "in_review"
    row = await svc.approve_section(row.id, "timeline", "Marcus")
    row = await svc.transition(row.id, "approve", "Marcus")
    row = await svc.transition(row.id, "send", "Marcus")
    assert row.state == "sent" and row.export_path
    export = Path(row.export_path)
    md = await asyncio.to_thread(export.read_text)
    assert "Six weeks." in md and "## Price" in md
    assert await asyncio.to_thread(export.with_suffix(".html").exists)
    with pytest.raises(GateError):
        await svc.edit_section(row.id, "summary", "too late", "Sam")
    audit = await svc.repo.list_audit(pid)
    edit = next(a for a in audit if a.action == "edit_section" and a.section == "summary")
    assert edit.actor == "Rina" and edit.version == 2 and "+A better summary." in (edit.diff or "")
    assert next(a.action for a in audit) == "send"


async def test_follow_up_run_creates_v2_with_changelog(settings: Settings) -> None:
    from scope.domain.transcript import parse

    m3 = parse(read("meeting-03-follow-up.txt"), "m2")
    deps = Deps(fake_with(reconciler=gold_like_reconciler(m3)), FakeBrain(), mocked=True)
    async with open_service(settings, deps) as svc:
        pid, vid = await initial_version(svc)
        await svc.approve_scope(vid, "Sam")
        await svc.add_meeting(pid, read("meeting-03-follow-up.txt"))
        run = await svc.start_run(pid)
        assert run.kind == "follow_up" and run.base_version == 1
        v2 = await svc.execute_run(run.id)
        assert v2 is not None and v2.version == 2 and v2.snapshot["changelog"]["change_requests"]
        await svc.approve_scope(v2.id, "Sam")
        row = await svc.generate_proposal(v2.id, "Sam")
        cr = Proposal.model_validate(row.document).section("change_requests")
        assert "Quoted separately" in cr.content and cr.generated_by == "code"


async def test_failed_run_is_recorded(settings: Settings) -> None:
    fake = FakeLLM(failures={"extractor": LLMUnavailableError("overloaded", agent="extractor")})
    async with open_service(settings, Deps(fake, FakeBrain(), mocked=True)) as svc:
        p = await svc.repo.create_project("x", "y")
        await svc.add_meeting(p.id, read("meeting-01-kickoff.txt"))
        run = await svc.start_run(p.id)
        assert await svc.execute_run(run.id) is None
        run = await svc.repo.get_run(run.id)
        assert run.status == "failed" and "overloaded" in (run.error or "")
        calls = await svc.repo.list_llm_calls(run.id)
        assert any(c.agent == "extractor" and c.error for c in calls)


async def test_rerun_from_stage_creates_new_version(svc: ScopeService) -> None:
    pid, _ = await initial_version(svc)
    runs = await svc.repo.list_runs(pid)
    new = await svc.rerun_from(runs[0].id, "estimate")
    assert new.status == "done"
    calls = await svc.repo.list_llm_calls(new.id)
    assert {c.agent for c in calls} == {"scoper", "reviewer"}
    assert [v.version for v in await svc.repo.list_scope_versions(pid)] == [1, 2]
