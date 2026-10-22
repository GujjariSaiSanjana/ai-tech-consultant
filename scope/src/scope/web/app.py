"""Routes. Every state-changing action is a plain form POST that redirects (works without JS);
HTMX only polls run progress."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Coroutine
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated, Any, cast
from urllib.parse import quote

from fastapi import FastAPI, Form, Request, UploadFile
from fastapi.responses import HTMLResponse, PlainTextResponse, RedirectResponse, Response
from fastapi.templating import Jinja2Templates

from scope.bootstrap import open_service
from scope.config import Settings, get_settings
from scope.db.repo import NotFoundError
from scope.domain.models import PROPOSAL_SECTIONS, SECTION_TITLES, Proposal
from scope.domain.states import ProposalAction
from scope.orchestrator import STAGES
from scope.service import GateError, ScopeService, snapshot_of
from scope.web.views import KIND_LABELS, diff_rows, feasibility_by_id
from scope.wiring import Deps

TEMPLATES = Jinja2Templates(directory=Path(__file__).parent / "templates")
TABS = ("transcripts", "questions", "scope", "hours", "proposal", "changes")


def create_app(settings: Settings | None = None, deps: Deps | None = None) -> FastAPI:
    settings = settings or get_settings()
    tasks: set[asyncio.Task[Any]] = set()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        async with open_service(settings, deps) as svc:
            app.state.svc = svc
            yield
            for t in tasks:
                t.cancel()

    app = FastAPI(title="Scope of Work", lifespan=lifespan)

    def svc_of(request: Request) -> ScopeService:
        return cast(ScopeService, request.app.state.svc)

    def back(url: str, msg: str | None = None, fragment: str | None = None) -> RedirectResponse:
        if msg:
            url += ("&" if "?" in url else "?") + "msg=" + quote(msg)
        if fragment:
            url += "#" + quote(fragment)
        return RedirectResponse(url, status_code=303)

    def in_background(coro: Coroutine[Any, Any, Any]) -> None:
        task = asyncio.create_task(coro)
        tasks.add(task)
        task.add_done_callback(tasks.discard)

    def with_actor(resp: Response, actor: str) -> Response:
        resp.set_cookie("actor", actor, max_age=60 * 60 * 24 * 30, samesite="lax")
        return resp

    @app.exception_handler(NotFoundError)
    async def not_found(request: Request, exc: NotFoundError) -> Response:
        return PlainTextResponse(str(exc), status_code=404)

    @app.get("/", response_class=HTMLResponse)
    async def index(request: Request) -> Response:
        projects = await svc_of(request).repo.list_projects()
        return TEMPLATES.TemplateResponse(
            request,
            "index.html",
            {
                "projects": projects,
                "mocked": svc_of(request).deps.mocked,
                "msg": request.query_params.get("msg"),
            },
        )

    @app.post("/projects")
    async def create_project(
        request: Request, name: Annotated[str, Form()], client: Annotated[str, Form()]
    ) -> Response:
        p = await svc_of(request).repo.create_project(name.strip(), client.strip())
        return back(f"/projects/{p.id}?tab=transcripts")

    @app.get("/projects/{project_id}", response_class=HTMLResponse)
    async def project(
        request: Request, project_id: int, tab: str = "transcripts", v: int | None = None
    ) -> Response:
        svc = svc_of(request)
        proj = await svc.repo.get_project(project_id)
        versions = list(await svc.repo.list_scope_versions(project_id))
        current = next((x for x in versions if x.version == v), versions[-1] if versions else None)
        snap = snapshot_of(current) if current else None
        prior = None
        if current and snap and snap.changelog:
            prior = next(x for x in versions if x.version == snap.changelog.from_version)
        proposal_row = await svc.repo.latest_proposal(project_id)
        proposal = Proposal.model_validate(proposal_row.document) if proposal_row else None
        runs = list(await svc.repo.list_runs(project_id))
        ctx = {
            "project": proj,
            "tab": tab if tab in TABS else "transcripts",
            "tabs": TABS,
            "meetings": await svc.repo.list_meetings(project_id),
            "runs": runs,
            "versions": versions,
            "current": current,
            "snap": snap,
            "feas": feasibility_by_id(snap) if snap else {},
            "kind_labels": KIND_LABELS,
            "diff": diff_rows(snapshot_of(prior), snap.changelog)
            if prior and snap and snap.changelog
            else None,
            "prior": prior,
            "proposal_row": proposal_row,
            "proposal": proposal,
            "proposal_for_current": bool(
                proposal_row and current and proposal_row.scope_version_id == current.id
            ),
            "section_titles": SECTION_TITLES,
            "audit": await svc.repo.list_audit(project_id),
            "stages": STAGES,
            "running": any(r.status in ("queued", "running") for r in runs),
            "actor": request.cookies.get("actor", ""),
            "msg": request.query_params.get("msg"),
            "mocked": svc.deps.mocked,
        }
        return TEMPLATES.TemplateResponse(request, "project.html", ctx)

    @app.get("/projects/{project_id}/runs/status", response_class=HTMLResponse)
    async def run_status(request: Request, project_id: int) -> Response:
        runs = list(await svc_of(request).repo.list_runs(project_id))
        resp = TEMPLATES.TemplateResponse(
            request,
            "_run_status.html",
            {
                "runs": runs,
                "project_id": project_id,
                "running": any(r.status in ("queued", "running") for r in runs),
            },
        )
        if not any(r.status in ("queued", "running") for r in runs):
            resp.headers["HX-Refresh"] = "true"  # polling only runs while busy: reload results
        return resp

    @app.post("/projects/{project_id}/meetings")
    async def add_meeting(
        request: Request,
        project_id: int,
        text: Annotated[str, Form()] = "",
        file: UploadFile | None = None,
    ) -> Response:
        raw = text
        if file is not None and file.filename:
            raw = (await file.read()).decode("utf-8", errors="replace")
        if not raw.strip():
            return back(f"/projects/{project_id}?tab=transcripts", "The transcript is empty.")
        m, created = await svc_of(request).add_meeting(project_id, raw)
        msg = (
            f"Added meeting {m.meeting_key}."
            if created
            else (f"This transcript was already added as {m.meeting_key}.")
        )
        return back(f"/projects/{project_id}?tab=transcripts", msg)

    @app.post("/projects/{project_id}/runs")
    async def start_run(request: Request, project_id: int) -> Response:
        svc = svc_of(request)
        try:
            run = await svc.start_run(project_id)
        except GateError as exc:
            return back(f"/projects/{project_id}?tab=transcripts", str(exc))
        in_background(svc.execute_run(run.id))
        return back(f"/projects/{project_id}?tab=transcripts", f"Run {run.id} started.")

    @app.post("/runs/{run_id}/rerun")
    async def rerun(request: Request, run_id: int, stage: Annotated[str, Form()]) -> Response:
        svc = svc_of(request)
        run = await svc.repo.get_run(run_id)
        if stage not in STAGES:
            return back(f"/projects/{run.project_id}?tab=transcripts", "Unknown stage.")
        try:
            new, saved = await svc.prepare_rerun(run_id, stage)
        except GateError as exc:
            return back(f"/projects/{run.project_id}?tab=transcripts", str(exc))
        in_background(svc.execute_run(new.id, saved=saved, from_stage=stage))
        return back(
            f"/projects/{run.project_id}?tab=transcripts",
            f"Re-running from {stage} as run {new.id}.",
        )

    @app.post("/scope/{version_id}/approve")
    async def approve(request: Request, version_id: int, actor: Annotated[str, Form()]) -> Response:
        svc = svc_of(request)
        v = await svc.repo.get_scope_version(version_id)
        try:
            await svc.approve_scope(version_id, actor.strip())
            msg = f"Scope v{v.version} approved."
        except GateError as exc:
            msg = str(exc)
        return with_actor(back(f"/projects/{v.project_id}?tab=scope&v={v.version}", msg), actor)

    @app.post("/scope/{version_id}/proposal")
    async def generate(
        request: Request, version_id: int, actor: Annotated[str, Form()]
    ) -> Response:
        svc = svc_of(request)
        v = await svc.repo.get_scope_version(version_id)
        try:
            await svc.generate_proposal(version_id, actor.strip())
            msg = "Proposal drafted. Review each section."
        except GateError as exc:
            msg = str(exc)
        return with_actor(back(f"/projects/{v.project_id}?tab=proposal&v={v.version}", msg), actor)

    @app.post("/proposals/{proposal_id}/sections/{section}/{action}")
    async def section_action(
        request: Request,
        proposal_id: int,
        section: str,
        action: str,
        actor: Annotated[str, Form()],
        content: Annotated[str, Form()] = "",
        instruction: Annotated[str, Form()] = "",
    ) -> Response:
        svc = svc_of(request)
        row = await svc.repo.get_proposal(proposal_id)
        url = f"/projects/{row.project_id}?tab=proposal"
        if section not in PROPOSAL_SECTIONS:
            return back(url, "Unknown section.")
        name = section
        try:
            match action:
                case "approve":
                    await svc.approve_section(proposal_id, name, actor.strip())
                case "edit":
                    await svc.edit_section(proposal_id, name, content, actor.strip())
                case "regenerate":
                    await svc.regenerate_section(
                        proposal_id, name, actor.strip(), instruction or None
                    )
                case _:
                    return back(url, "Unknown action.")
            msg = f"{SECTION_TITLES[name]}: {action} done."
        except GateError as exc:
            msg = str(exc)
        return with_actor(back(url, msg, fragment=f"sec-{section}"), actor)

    @app.post("/proposals/{proposal_id}/transition")
    async def transition(
        request: Request,
        proposal_id: int,
        action: Annotated[str, Form()],
        actor: Annotated[str, Form()],
    ) -> Response:
        svc = svc_of(request)
        row = await svc.repo.get_proposal(proposal_id)
        url = f"/projects/{row.project_id}?tab=proposal"
        if action not in ("submit_for_review", "approve", "send"):
            return back(url, "Unknown action.")
        try:
            row = await svc.transition(proposal_id, cast(ProposalAction, action), actor.strip())
            msg = f"Proposal is now {row.state.replace('_', ' ')}."
            if row.export_path:
                msg += f" Exported to {row.export_path}"
        except GateError as exc:
            msg = str(exc)
        return with_actor(back(url, msg), actor)

    @app.get("/proposals/{proposal_id}/preview.md", response_class=PlainTextResponse)
    async def preview(request: Request, proposal_id: int) -> str:
        svc = svc_of(request)
        return await svc.markdown(await svc.repo.get_proposal(proposal_id))

    return app
