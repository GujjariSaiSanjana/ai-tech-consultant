"""The pipeline, as plain deterministic Python (no LLM router).

Initial run:   [Extractor || Questioner] -> Feasibility -> (Scoper -> Reviewer) x <= N -> snapshot
Follow-up run: [Reconciler || Questioner] -> Feasibility (changed only) -> re-scope touched items
               -> Reviewer loop -> change requests -> snapshot

Each stage's output is handed to ``on_stage`` (persisted as JSON by the service) and can be passed
back in ``saved`` to re-run from any stage without repeating the earlier ones.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Sequence
from typing import Literal

from pydantic import BaseModel, Field

from scope.agents import extractor, feasibility, questioner, reconciler, reviewer, scoper
from scope.brain.client import BrainClient
from scope.domain.checks import change_request_grouping_notes, coverage_notes, estimate_rule_notes
from scope.domain.diff import apply_findings, base_items, finalize_changes, split_items
from scope.domain.models import (
    Assumption,
    ChangeLog,
    DiscardedMention,
    Feasibility,
    OpenQuestion,
    ReconcileFinding,
    Requirement,
    Review,
    ReviewNote,
    ScopeItem,
    ScopeSnapshot,
)
from scope.domain.pert import summarize
from scope.domain.transcript import Transcript, estimate_tokens, render
from scope.llm.session import LLMSession

Stage = Literal["requirements", "questions", "feasibility", "estimate"]
STAGES: tuple[Stage, ...] = ("requirements", "questions", "feasibility", "estimate")


class RequirementsStage(BaseModel):
    requirements: list[Requirement]
    discarded: list[DiscardedMention]
    findings: list[ReconcileFinding] = Field(default_factory=list)
    changelog: ChangeLog | None = None


class QuestionsStage(BaseModel):
    questions: list[OpenQuestion]
    assumptions: list[Assumption]


class FeasibilityStage(BaseModel):
    results: list[Feasibility]


class EstimateStage(BaseModel):
    items: list[ScopeItem]
    exclusions: list[str]
    assumptions: list[Assumption]
    review: Review
    changelog: ChangeLog | None = None


class SavedStages(BaseModel):
    requirements: RequirementsStage | None = None
    questions: QuestionsStage | None = None
    feasibility: FeasibilityStage | None = None
    estimate: EstimateStage | None = None


StageCallback = Callable[[Stage, BaseModel], Awaitable[None]]


def evidence_digest(requirements: Sequence[Requirement]) -> str:
    """Context for downstream agents when the transcripts are too long to resend whole."""
    lines = [
        "(Transcripts exceed the single-request limit; these are the verbatim quotes that "
        "support each requirement.)"
    ]
    for r in requirements:
        lines += [f"[{e.meeting_id} {e.timestamp}] {e.speaker}: {e.quote}" for e in r.evidence]
    return "\n".join(lines)


def _dedupe_assumptions(*groups: Sequence[Assumption]) -> list[Assumption]:
    seen: dict[str, Assumption] = {}
    for g in groups:
        for a in g:
            seen.setdefault(a.id, a)
    return list(seen.values())


class Orchestrator:
    def __init__(
        self, session: LLMSession, brain: BrainClient, on_stage: StageCallback | None = None
    ) -> None:
        self.session = session
        self.brain = brain
        self.on_stage = on_stage

    async def _emit(self, stage: Stage, out: BaseModel) -> None:
        if self.on_stage is not None:
            await self.on_stage(stage, out)

    def _context(self, transcripts: Sequence[Transcript], reqs: Sequence[Requirement]) -> str:
        text = render(transcripts)
        if estimate_tokens(text) > self.session.settings.long_transcript_tokens:
            return evidence_digest(reqs)
        return text

    @staticmethod
    def _keep(saved: SavedStages | None, stage: Stage, from_stage: Stage | None) -> bool:
        if saved is None or getattr(saved, stage) is None:
            return False
        return from_stage is not None and STAGES.index(stage) < STAGES.index(from_stage)

    async def run_initial(
        self,
        transcripts: Sequence[Transcript],
        *,
        saved: SavedStages | None = None,
        from_stage: Stage | None = None,
    ) -> ScopeSnapshot:
        keep_req = self._keep(saved, "requirements", from_stage)
        keep_q = self._keep(saved, "questions", from_stage)

        async def requirements() -> RequirementsStage:
            if keep_req and saved and saved.requirements:
                return saved.requirements
            reqs, discarded = await extractor.extract(self.session, transcripts)
            out = RequirementsStage(requirements=reqs, discarded=discarded)
            await self._emit("requirements", out)
            return out

        async def questions() -> QuestionsStage:
            if keep_q and saved and saved.questions:
                return saved.questions
            qs, assumptions = await questioner.ask(self.session, transcripts)
            out = QuestionsStage(questions=qs, assumptions=assumptions)
            await self._emit("questions", out)
            return out

        req_stage, q_stage = await asyncio.gather(requirements(), questions())
        reqs = req_stage.requirements
        context = self._context(transcripts, reqs)
        feas = await self._feasibility(saved, from_stage, reqs, context)
        est = await self._estimate(
            saved,
            from_stage,
            context=context,
            all_reqs=reqs,
            to_scope=reqs,
            feas=feas,
            q_stage=q_stage,
            kept_items=[],
        )
        return self._snapshot(req_stage, q_stage, feas, est, changelog=None)

    async def run_follow_up(
        self,
        prior: ScopeSnapshot,
        *,
        prior_version: int,
        transcripts: Sequence[Transcript],
        follow_up_ids: Sequence[str],
        saved: SavedStages | None = None,
        from_stage: Stage | None = None,
    ) -> ScopeSnapshot:
        keep_req = self._keep(saved, "requirements", from_stage)
        keep_q = self._keep(saved, "questions", from_stage)

        async def requirements() -> RequirementsStage:
            if keep_req and saved and saved.requirements:
                return saved.requirements
            findings, discarded = await reconciler.reconcile(
                self.session, prior.requirements, transcripts, follow_up_ids
            )
            reqs, log = apply_findings(
                prior.requirements,
                findings,
                from_version=prior_version,
                to_version=prior_version + 1,
            )
            out = RequirementsStage(
                requirements=reqs, discarded=discarded, findings=findings, changelog=log
            )
            await self._emit("requirements", out)
            return out

        async def questions() -> QuestionsStage:
            if keep_q and saved and saved.questions:
                return saved.questions
            qs, assumptions = await questioner.ask(self.session, transcripts)
            out = QuestionsStage(
                questions=qs, assumptions=_dedupe_assumptions(prior.assumptions, assumptions)
            )
            await self._emit("questions", out)
            return out

        req_stage, q_stage = await asyncio.gather(requirements(), questions())
        assert req_stage.changelog is not None
        log = req_stage.changelog
        reqs = req_stage.requirements
        kept_items, rescope_ids = split_items(prior.items, log)
        context = self._context(transcripts, reqs)

        prior_feas = {f.requirement_id: f for f in prior.feasibility}
        modified = {m.id for m in log.modified}
        to_check = [
            r for r in reqs if r.id in modified or (r.id in rescope_ids and r.id not in prior_feas)
        ]
        new_feas = {
            f.requirement_id: f
            for f in await self._feasibility(saved, from_stage, to_check, context)
        }
        feas = [
            new_feas.get(r.id) or prior_feas[r.id]
            for r in reqs
            if r.id in new_feas or r.id in prior_feas
        ]

        est = await self._estimate(
            saved,
            from_stage,
            context=context,
            all_reqs=reqs,
            to_scope=[r for r in reqs if r.id in rescope_ids],
            feas=feas,
            q_stage=q_stage,
            kept_items=kept_items,
        )
        log = finalize_changes(log, prior.items, est.items)
        snapshot = self._snapshot(req_stage, q_stage, feas, est, changelog=log)
        snapshot.exclusions = list(dict.fromkeys([*prior.exclusions, *snapshot.exclusions]))
        return snapshot

    async def _feasibility(
        self,
        saved: SavedStages | None,
        from_stage: Stage | None,
        reqs: Sequence[Requirement],
        context: str,
    ) -> list[Feasibility]:
        if self._keep(saved, "feasibility", from_stage) and saved and saved.feasibility:
            return saved.feasibility.results
        results = await feasibility.check(self.session, self.brain, reqs, context)
        await self._emit("feasibility", FeasibilityStage(results=results))
        return results

    async def _estimate(
        self,
        saved: SavedStages | None,
        from_stage: Stage | None,
        *,
        context: str,
        all_reqs: Sequence[Requirement],
        to_scope: Sequence[Requirement],
        feas: Sequence[Feasibility],
        q_stage: QuestionsStage,
        kept_items: Sequence[ScopeItem],
    ) -> EstimateStage:
        """Evaluator-optimizer loop: scope, check in code, review, revise (bounded)."""
        if self._keep(saved, "estimate", from_stage) and saved and saved.estimate:
            return saved.estimate
        settings = self.session.settings
        by_req = {f.requirement_id: f for f in feas}
        notes: list[ReviewNote] = []
        items: list[ScopeItem] = []
        exclusions: list[str] = []
        extra: list[Assumption] = []
        loops = 0
        approved = True
        if to_scope:
            for attempt in range(1, settings.max_review_loops + 1):
                loops = attempt
                new_items, exclusions, extra = await scoper.estimate(
                    self.session,
                    context=context,
                    requirements=to_scope,
                    feasibility=[by_req[r.id] for r in to_scope if r.id in by_req],
                    questions=q_stage.questions,
                    assumptions=q_stage.assumptions,
                    review_notes=notes,
                    kept_items=kept_items,
                )
                items = [*kept_items, *new_items]
                code_notes = (
                    coverage_notes(all_reqs, items)
                    + estimate_rule_notes(
                        items, by_req, setting_max_hours=settings.setting_max_hours
                    )
                    + change_request_grouping_notes(all_reqs, items)
                )
                approved, llm_notes = await reviewer.review(
                    self.session,
                    context=context,
                    requirements=all_reqs,
                    feasibility=feas,
                    items=items,
                    assumptions=[*q_stage.assumptions, *extra],
                    code_notes=code_notes,
                )
                notes = [*code_notes, *llm_notes]
                if not any(n.severity == "must_fix" for n in notes):
                    break
        else:
            items = list(kept_items)
        review = Review(
            loops=loops,
            notes=notes,
            approved_by_reviewer=approved and not any(n.severity == "must_fix" for n in notes),
        )
        out = EstimateStage(items=items, exclusions=exclusions, assumptions=extra, review=review)
        await self._emit("estimate", out)
        return out

    @staticmethod
    def _snapshot(
        req_stage: RequirementsStage,
        q_stage: QuestionsStage,
        feas: Sequence[Feasibility],
        est: EstimateStage,
        changelog: ChangeLog | None,
    ) -> ScopeSnapshot:
        reqs = req_stage.requirements
        return ScopeSnapshot(
            requirements=reqs,
            discarded=req_stage.discarded,
            questions=q_stage.questions,
            assumptions=_dedupe_assumptions(q_stage.assumptions, est.assumptions),
            feasibility=list(feas),
            items=est.items,
            exclusions=est.exclusions,
            estimate=summarize(base_items(est.items, reqs)),
            review=est.review,
            changelog=changelog,
        )
