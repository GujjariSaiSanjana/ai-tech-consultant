"""Application workflows: runs, the approval gate, proposal editing and the audit log."""

from __future__ import annotations

import asyncio
import difflib
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from scope.agents import proposal_writer
from scope.config import Settings
from scope.db.repo import DbCallRecorder, Repo
from scope.db.tables import Meeting, ProposalRow, Run, ScopeVersion
from scope.domain.diff import base_items
from scope.domain.models import (
    PROPOSAL_SECTIONS,
    Proposal,
    ProposalSection,
    ProposalSectionName,
    ScopeSnapshot,
)
from scope.domain.render import (
    change_requests_section,
    markdown_to_html,
    price_section,
    proposal_markdown,
)
from scope.domain.states import (
    InvalidTransition,
    ProposalAction,
    ProposalState,
    ScopeVersionStatus,
    approve_scope,
    can_generate_proposal,
    next_proposal_state,
)
from scope.domain.transcript import Transcript, parse, render
from scope.orchestrator import STAGES, Orchestrator, SavedStages, Stage
from scope.wiring import Deps

CODE_SECTIONS: tuple[ProposalSectionName, ...] = ("price", "change_requests")


class GateError(Exception):
    """An action was blocked by the human approval gate or by workflow rules."""


def text_diff(before: str, after: str, label: str) -> str:
    return "".join(
        difflib.unified_diff(
            before.splitlines(keepends=True),
            after.splitlines(keepends=True),
            fromfile=f"{label} (before)",
            tofile=f"{label} (after)",
        )
    )


def snapshot_of(v: ScopeVersion) -> ScopeSnapshot:
    return ScopeSnapshot.model_validate(v.snapshot)


class ScopeService:
    def __init__(self, repo: Repo, settings: Settings, deps: Deps) -> None:
        self.repo = repo
        self.settings = settings
        self.deps = deps

    # meetings and runs -------------------------------------------------------------------
    async def add_meeting(
        self, project_id: int, raw_text: str, title: str | None = None
    ) -> tuple[Meeting, bool]:
        """Add a transcript. Idempotent: the same text twice returns the existing meeting."""
        meetings = await self.repo.list_meetings(project_id)
        for m in meetings:
            if m.raw_text == raw_text:
                return m, False
        key = f"m{len(meetings) + 1}"
        parsed_title = parse(raw_text, key).title if raw_text.strip() else key
        return await self.repo.add_meeting(project_id, title or parsed_title, raw_text), True

    async def check_can_process(self, project_id: int) -> None:
        """The gate for new meetings: a follow-up is reconciled against an approved scope, so the
        latest scope version must be approved first."""
        if any(r.status in ("queued", "running") for r in await self.repo.list_runs(project_id)):
            raise GateError("a run is already in progress; wait for it to finish")
        versions = await self.repo.list_scope_versions(project_id)
        if versions and versions[-1].status != "approved":
            raise GateError(
                f"scope v{versions[-1].version} is still a draft; approve it before "
                "processing a follow-up meeting"
            )

    @staticmethod
    def transcripts(meetings: Sequence[Meeting]) -> list[Transcript]:
        return [parse(m.raw_text, m.meeting_key, m.title) for m in meetings]

    async def start_run(self, project_id: int) -> Run:
        """Decide initial vs follow-up and enforce the gate: a follow-up meeting is reconciled
        against the latest approved scope, so a draft scope must be approved first."""
        await self.check_can_process(project_id)
        meetings = await self.repo.list_meetings(project_id)
        pending = [m for m in meetings if m.run_id is None]
        if not pending:
            raise GateError("no new meeting to process; add a transcript first")
        versions = await self.repo.list_scope_versions(project_id)
        approved = [v for v in versions if v.status == "approved"]
        kind = "follow_up" if approved else "initial"
        run = await self.repo.create_run(
            project_id,
            kind,
            base_version=approved[-1].version if approved else None,
            meeting_keys=[m.meeting_key for m in pending],
            mocked=self.deps.mocked,
        )
        await self.repo.mark_meetings([m.id for m in pending], run.id)
        return run

    async def execute_run(
        self, run_id: int, *, saved: SavedStages | None = None, from_stage: Stage | None = None
    ) -> ScopeVersion | None:
        run = await self.repo.get_run(run_id)
        await self.repo.update_run(run_id, status="running")
        session = self.deps.session(
            self.settings, run_id=str(run_id), recorder=DbCallRecorder(self.repo, run_id)
        )

        async def persist(stage: Stage, out: BaseModel) -> None:
            await self.repo.save_stage(run_id, stage, out.model_dump(mode="json"))

        orch = Orchestrator(session, self.deps.brain, on_stage=persist)
        try:
            # Every meeting processed up to this run: follow-ups see the full history.
            meetings = [
                m
                for m in await self.repo.list_meetings(run.project_id)
                if m.run_id is not None and m.run_id <= run_id
            ]
            transcripts = self.transcripts(meetings)
            if run.kind == "initial":
                snapshot = await orch.run_initial(transcripts, saved=saved, from_stage=from_stage)
            else:
                base = await self._version(run.project_id, run.base_version)
                snapshot = await orch.run_follow_up(
                    snapshot_of(base),
                    prior_version=base.version,
                    transcripts=transcripts,
                    follow_up_ids=run.meeting_keys["keys"],
                    saved=saved,
                    from_stage=from_stage,
                )
        except Exception as exc:
            await self.repo.finish_run(
                run_id,
                status="failed",
                error=f"{type(exc).__name__}: {exc}",
                cost_usd=session.total_cost_usd,
            )
            return None
        version = await self.repo.add_scope_version(
            run.project_id, run_id, snapshot.model_dump(mode="json")
        )
        await self.repo.finish_run(
            run_id, status="done", error=None, cost_usd=session.total_cost_usd
        )
        return version

    async def prepare_rerun(self, run_id: int, stage: Stage) -> tuple[Run, SavedStages]:
        """Create a run that re-runs ``stage`` and later stages of a finished run, reusing the
        earlier stage outputs. Run it with ``execute_run(run.id, saved=..., from_stage=...)``."""
        old = await self.repo.get_run(run_id)
        runs = await self.repo.list_runs(old.project_id)
        if runs[0].id != run_id:
            raise GateError("only the latest run can be re-run")
        if old.status not in ("done", "failed"):
            raise GateError("wait for the run to finish before re-running it")
        run = await self.repo.create_run(
            old.project_id,
            old.kind,
            base_version=old.base_version,
            meeting_keys=old.meeting_keys["keys"],
            mocked=self.deps.mocked,
        )
        stages = await self.repo.load_stages(run_id)
        saved = SavedStages.model_validate({k: v for k, v in stages.items() if k in STAGES})
        for name in STAGES[: STAGES.index(stage)]:
            if name in stages:
                await self.repo.save_stage(run.id, name, stages[name])
        return run, saved

    async def rerun_from(self, run_id: int, stage: Stage) -> Run:
        run, saved = await self.prepare_rerun(run_id, stage)
        await self.execute_run(run.id, saved=saved, from_stage=stage)
        return await self.repo.get_run(run.id)

    async def _version(self, project_id: int, number: int | None) -> ScopeVersion:
        for v in await self.repo.list_scope_versions(project_id):
            if v.version == number:
                return v
        raise GateError(f"scope version {number} not found")

    # scope approval ------------------------------------------------------------------------
    async def approve_scope(self, version_id: int, actor: str) -> None:
        _require_actor(actor)
        v = await self.repo.get_scope_version(version_id)
        try:
            new = approve_scope(_scope_status(v.status))
        except InvalidTransition as exc:
            raise GateError(str(exc)) from exc
        await self.repo.update_scope_version(
            version_id, status=new, approved_by=actor, approved_at=datetime.now(UTC)
        )
        await self.repo.audit(
            project_id=v.project_id,
            entity="scope",
            entity_id=v.id,
            actor=actor,
            action="approve_scope",
            version=v.version,
        )

    # proposal ------------------------------------------------------------------------------
    async def _context(self, project_id: int) -> str:
        return render(self.transcripts(await self.repo.list_meetings(project_id)))

    @staticmethod
    def _code_section(name: ProposalSectionName, snap: ScopeSnapshot) -> str:
        if name == "price":
            assert snap.estimate is not None
            base = {i.id for i in base_items(snap.items, snap.requirements)}
            excluded = {i.id for i in snap.items} - base
            return price_section(snap.items, snap.estimate, excluded_ids=excluded)
        return change_requests_section(snap.changelog)

    async def generate_proposal(self, version_id: int, actor: str) -> ProposalRow:
        _require_actor(actor)
        v = await self.repo.get_scope_version(version_id)
        if not can_generate_proposal(_scope_status(v.status)):
            raise GateError("approve the scope before generating a proposal")
        project = await self.repo.get_project(v.project_id)
        snap = snapshot_of(v)
        session = self.deps.session(self.settings)
        written = await proposal_writer.write(
            session, snap, client=project.client, context=await self._context(v.project_id)
        )
        sections: list[ProposalSection] = []
        for name in PROPOSAL_SECTIONS:
            if name == "change_requests" and snap.changelog is None:
                continue
            if name in CODE_SECTIONS:
                sections.append(
                    ProposalSection(
                        name=name, content=self._code_section(name, snap), generated_by="code"
                    )
                )
            else:
                sections.append(
                    ProposalSection(name=name, content=written[name], generated_by="llm")
                )
        row = await self.repo.create_proposal(
            v.project_id, v.id, Proposal(sections=sections).model_dump(mode="json")
        )
        await self.repo.audit(
            project_id=v.project_id,
            entity="proposal",
            entity_id=row.id,
            actor=actor,
            action="generate",
            version=row.revision,
        )
        return row

    async def _transition(self, row: ProposalRow, action: ProposalAction) -> ProposalState:
        doc = Proposal.model_validate(row.document)
        try:
            return next_proposal_state(
                _state(row.state), action, all_sections_approved=doc.all_approved
            )
        except InvalidTransition as exc:
            raise GateError(str(exc)) from exc

    async def _change_section(
        self,
        proposal_id: int,
        section: ProposalSectionName,
        content: str,
        actor: str,
        action: ProposalAction,
        by: str,
    ) -> ProposalRow:
        _require_actor(actor)
        row = await self.repo.get_proposal(proposal_id)
        state = await self._transition(row, action)
        doc = Proposal.model_validate(row.document)
        sec = doc.section(section)
        diff = text_diff(sec.content, content, section)
        sec.content, sec.approved = content, False
        sec.generated_by = "human" if by == "human" else sec.generated_by
        revision = row.revision + 1
        await self.repo.update_proposal(
            proposal_id, document=doc.model_dump(mode="json"), state=state, revision=revision
        )
        await self.repo.audit(
            project_id=row.project_id,
            entity="proposal",
            entity_id=row.id,
            actor=actor,
            action=action,
            version=revision,
            section=section,
            diff=diff or None,
        )
        return await self.repo.get_proposal(proposal_id)

    async def edit_section(
        self, proposal_id: int, section: ProposalSectionName, content: str, actor: str
    ) -> ProposalRow:
        return await self._change_section(
            proposal_id, section, content, actor, "edit_section", "human"
        )

    async def regenerate_section(
        self,
        proposal_id: int,
        section: ProposalSectionName,
        actor: str,
        instruction: str | None = None,
    ) -> ProposalRow:
        _require_actor(actor)
        row = await self.repo.get_proposal(proposal_id)
        await self._transition(row, "regenerate_section")
        v = await self.repo.get_scope_version(row.scope_version_id)
        snap = snapshot_of(v)
        if section in CODE_SECTIONS:
            content = self._code_section(section, snap)
        else:
            project = await self.repo.get_project(row.project_id)
            current = Proposal.model_validate(row.document).section(section).content
            content = await proposal_writer.rewrite_section(
                self.deps.session(self.settings),
                snap,
                client=project.client,
                context=await self._context(row.project_id),
                section=section,
                current=current,
                instruction=instruction,
            )
        return await self._change_section(
            proposal_id, section, content, actor, "regenerate_section", "llm"
        )

    async def approve_section(
        self, proposal_id: int, section: ProposalSectionName, actor: str
    ) -> ProposalRow:
        _require_actor(actor)
        row = await self.repo.get_proposal(proposal_id)
        await self._transition(row, "approve_section")
        doc = Proposal.model_validate(row.document)
        doc.section(section).approved = True
        await self.repo.update_proposal(proposal_id, document=doc.model_dump(mode="json"))
        await self.repo.audit(
            project_id=row.project_id,
            entity="proposal",
            entity_id=row.id,
            actor=actor,
            action="approve_section",
            version=row.revision,
            section=section,
        )
        return await self.repo.get_proposal(proposal_id)

    async def transition(self, proposal_id: int, action: ProposalAction, actor: str) -> ProposalRow:
        """submit_for_review, approve or send. Sending exports Markdown and HTML (no email)."""
        _require_actor(actor)
        if action not in ("submit_for_review", "approve", "send"):
            raise GateError(f"use the section actions for {action}")
        row = await self.repo.get_proposal(proposal_id)
        state = await self._transition(row, action)
        fields: dict[str, Any] = {"state": state}
        if action == "send":
            fields["export_path"] = str(await self._export(row))
        await self.repo.update_proposal(proposal_id, **fields)
        await self.repo.audit(
            project_id=row.project_id,
            entity="proposal",
            entity_id=row.id,
            actor=actor,
            action=action,
            version=row.revision,
        )
        return await self.repo.get_proposal(proposal_id)

    async def markdown(self, row: ProposalRow) -> str:
        project = await self.repo.get_project(row.project_id)
        v = await self.repo.get_scope_version(row.scope_version_id)
        return proposal_markdown(
            Proposal.model_validate(row.document),
            client=project.client,
            version=v.version,
            revision=row.revision,
        )

    async def _export(self, row: ProposalRow) -> Path:
        md = await self.markdown(row)
        stem = (
            Path(self.settings.export_dir)
            / f"project-{row.project_id}"
            / (f"proposal-{row.id}-rev{row.revision}")
        )
        html_doc = markdown_to_html(md, f"Proposal {row.id}")
        await asyncio.to_thread(_write_export, stem, md, html_doc)
        return stem.with_suffix(".md")


def _require_actor(actor: str) -> None:
    if not actor.strip():
        raise GateError("say who you are: every approval and edit is recorded in the audit log")


def _write_export(stem: Path, md: str, html_doc: str) -> None:
    stem.parent.mkdir(parents=True, exist_ok=True)
    stem.with_suffix(".md").write_text(md, encoding="utf-8")
    stem.with_suffix(".html").write_text(html_doc, encoding="utf-8")


def _state(value: str) -> ProposalState:
    match value:
        case "draft" | "in_review" | "approved" | "sent":
            return value
    raise ValueError(f"unknown proposal state {value!r}")


def _scope_status(value: str) -> ScopeVersionStatus:
    match value:
        case "draft" | "approved":
            return value
    raise ValueError(f"unknown scope status {value!r}")
