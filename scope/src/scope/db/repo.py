"""Data access. Each method is one short transaction; no business rules live here."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from scope.db.tables import (
    AuditEntry,
    LLMCall,
    Meeting,
    Project,
    ProposalRow,
    Run,
    ScopeVersion,
    StageOutput,
)
from scope.llm.types import CallRecord


class NotFoundError(LookupError):
    pass


class Repo:
    def __init__(self, sessions: async_sessionmaker[AsyncSession]) -> None:
        self._s = sessions

    async def _get[M: (Project, Meeting, Run, ScopeVersion, ProposalRow)](
        self, model: type[M], id_: int
    ) -> M:
        async with self._s() as s:
            row = await s.get(model, id_)
            if row is None:
                raise NotFoundError(f"{model.__name__} {id_} not found")
            return row

    async def _add[M: (Project, Meeting, Run, ScopeVersion, ProposalRow, AuditEntry, LLMCall)](
        self, row: M
    ) -> M:
        async with self._s() as s:
            s.add(row)
            await s.commit()
            await s.refresh(row)
            return row

    async def _update(
        self, model: type[Run | ScopeVersion | ProposalRow | Meeting], id_: int, **fields: Any
    ) -> None:
        async with self._s() as s:
            row = await s.get(model, id_)
            if row is None:
                raise NotFoundError(f"{model.__name__} {id_} not found")
            for k, v in fields.items():
                setattr(row, k, v)
            await s.commit()

    # projects and meetings
    async def create_project(self, name: str, client: str) -> Project:
        return await self._add(Project(name=name, client=client))

    async def get_project(self, project_id: int) -> Project:
        return await self._get(Project, project_id)

    async def list_projects(self) -> Sequence[Project]:
        async with self._s() as s:
            return (await s.scalars(select(Project).order_by(Project.id.desc()))).all()

    async def add_meeting(self, project_id: int, title: str, raw_text: str) -> Meeting:
        async with self._s() as s:
            n = await s.scalar(select(func.count()).where(Meeting.project_id == project_id)) or 0
            row = Meeting(
                project_id=project_id, meeting_key=f"m{n + 1}", title=title, raw_text=raw_text
            )
            s.add(row)
            await s.commit()
            await s.refresh(row)
            return row

    async def list_meetings(self, project_id: int) -> Sequence[Meeting]:
        async with self._s() as s:
            q = select(Meeting).where(Meeting.project_id == project_id).order_by(Meeting.id)
            return (await s.scalars(q)).all()

    async def mark_meetings(self, meeting_ids: Sequence[int], run_id: int) -> None:
        for mid in meeting_ids:
            await self._update(Meeting, mid, run_id=run_id)

    # runs and stages
    async def create_run(
        self,
        project_id: int,
        kind: str,
        *,
        base_version: int | None,
        meeting_keys: list[str],
        mocked: bool,
    ) -> Run:
        return await self._add(
            Run(
                project_id=project_id,
                kind=kind,
                base_version=base_version,
                meeting_keys={"keys": meeting_keys},
                mocked=mocked,
            )
        )

    async def get_run(self, run_id: int) -> Run:
        return await self._get(Run, run_id)

    async def update_run(self, run_id: int, **fields: Any) -> None:
        await self._update(Run, run_id, **fields)

    async def finish_run(
        self, run_id: int, *, status: str, error: str | None, cost_usd: float
    ) -> None:
        await self._update(
            Run,
            run_id,
            status=status,
            error=error,
            cost_usd=cost_usd,
            finished_at=datetime.now(UTC),
        )

    async def list_runs(self, project_id: int) -> Sequence[Run]:
        async with self._s() as s:
            q = select(Run).where(Run.project_id == project_id).order_by(Run.id.desc())
            return (await s.scalars(q)).all()

    async def save_stage(self, run_id: int, stage: str, payload: dict[str, Any]) -> None:
        async with self._s() as s:
            q = select(StageOutput).where(StageOutput.run_id == run_id, StageOutput.stage == stage)
            row = await s.scalar(q)
            if row is None:
                s.add(StageOutput(run_id=run_id, stage=stage, payload=payload))
            else:
                row.payload = payload
            await s.commit()

    async def load_stages(self, run_id: int) -> dict[str, dict[str, Any]]:
        async with self._s() as s:
            rows = (await s.scalars(select(StageOutput).where(StageOutput.run_id == run_id))).all()
            return {r.stage: r.payload for r in rows}

    # scope versions
    async def add_scope_version(
        self, project_id: int, run_id: int, snapshot: dict[str, Any]
    ) -> ScopeVersion:
        async with self._s() as s:
            q = select(func.max(ScopeVersion.version)).where(ScopeVersion.project_id == project_id)
            latest = await s.scalar(q) or 0
            row = ScopeVersion(
                project_id=project_id, version=latest + 1, run_id=run_id, snapshot=snapshot
            )
            s.add(row)
            await s.commit()
            await s.refresh(row)
            return row

    async def get_scope_version(self, version_id: int) -> ScopeVersion:
        return await self._get(ScopeVersion, version_id)

    async def list_scope_versions(self, project_id: int) -> Sequence[ScopeVersion]:
        async with self._s() as s:
            q = (
                select(ScopeVersion)
                .where(ScopeVersion.project_id == project_id)
                .order_by(ScopeVersion.version)
            )
            return (await s.scalars(q)).all()

    async def update_scope_version(self, version_id: int, **fields: Any) -> None:
        await self._update(ScopeVersion, version_id, **fields)

    # proposals
    async def create_proposal(
        self, project_id: int, scope_version_id: int, document: dict[str, Any]
    ) -> ProposalRow:
        return await self._add(
            ProposalRow(project_id=project_id, scope_version_id=scope_version_id, document=document)
        )

    async def get_proposal(self, proposal_id: int) -> ProposalRow:
        return await self._get(ProposalRow, proposal_id)

    async def latest_proposal(self, project_id: int) -> ProposalRow | None:
        async with self._s() as s:
            q = (
                select(ProposalRow)
                .where(ProposalRow.project_id == project_id)
                .order_by(ProposalRow.id.desc())
                .limit(1)
            )
            return await s.scalar(q)

    async def update_proposal(self, proposal_id: int, **fields: Any) -> None:
        await self._update(ProposalRow, proposal_id, **fields)

    # audit and llm calls
    async def audit(
        self,
        *,
        project_id: int,
        entity: str,
        entity_id: int,
        actor: str,
        action: str,
        version: int,
        section: str | None = None,
        diff: str | None = None,
    ) -> None:
        await self._add(
            AuditEntry(
                project_id=project_id,
                entity=entity,
                entity_id=entity_id,
                actor=actor,
                action=action,
                version=version,
                section=section,
                diff=diff,
            )
        )

    async def list_audit(self, project_id: int) -> Sequence[AuditEntry]:
        async with self._s() as s:
            q = (
                select(AuditEntry)
                .where(AuditEntry.project_id == project_id)
                .order_by(AuditEntry.id.desc())
            )
            return (await s.scalars(q)).all()

    async def add_llm_call(self, run_id: int, call: CallRecord) -> None:
        await self._add(
            LLMCall(
                run_id=run_id,
                agent=call.agent,
                model=call.model,
                input_tokens=call.usage.input_tokens,
                output_tokens=call.usage.output_tokens,
                cache_read_tokens=call.usage.cache_read_tokens,
                cache_write_tokens=call.usage.cache_write_tokens,
                cost_usd=call.cost_usd,
                latency_ms=call.latency_ms,
                stop_reason=call.stop_reason,
                request_id=call.request_id,
                error=call.error,
                mocked=call.mocked,
            )
        )

    async def list_llm_calls(self, run_id: int) -> Sequence[LLMCall]:
        async with self._s() as s:
            q = select(LLMCall).where(LLMCall.run_id == run_id).order_by(LLMCall.id)
            return (await s.scalars(q)).all()


class DbCallRecorder:
    """Writes every model call (including failed ones) to ``llm_calls``."""

    def __init__(self, repo: Repo, run_id: int) -> None:
        self._repo = repo
        self._run_id = run_id

    async def record(self, call: CallRecord) -> None:
        await self._repo.add_llm_call(self._run_id, call)
