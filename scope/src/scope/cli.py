"""Command line: ``python -m scope.cli run --transcript PATH [--project ID]`` and ``approve``."""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

from scope.bootstrap import open_service
from scope.config import Settings
from scope.domain.transcript import EmptyTranscriptError, parse
from scope.service import GateError, ScopeService, snapshot_of
from scope.summary import scope_summary


async def _run(
    svc: ScopeService, paths: list[Path], project_id: int | None, client: str | None
) -> int:
    if project_id is None:
        first = paths[0].read_text(encoding="utf-8")
        try:
            title = parse(first, "m1").title
        except EmptyTranscriptError:
            title = paths[0].stem
        project = await svc.repo.create_project(name=title, client=client or "Client")
        project_id = project.id
        print(f"Created project {project_id}: {title}")
    try:
        await svc.check_can_process(project_id)
        for p in paths:
            m, created = await svc.add_meeting(project_id, p.read_text(encoding="utf-8"))
            print(f"{'Added' if created else 'Already added:'} meeting {m.meeting_key}: {m.title}")
        run = await svc.start_run(project_id)
    except GateError as exc:
        print(f"Blocked: {exc}", file=sys.stderr)
        return 2
    mode = "offline fake LLM" if svc.deps.mocked else "Claude"
    print(f"Run {run.id} ({run.kind}) using {mode} ...")
    version = await svc.execute_run(run.id)
    run = await svc.repo.get_run(run.id)
    if version is None:
        print(f"Run failed: {run.error}", file=sys.stderr)
        return 1
    print()
    print(scope_summary(snapshot_of(version), version=version.version, status=version.status))
    print(f"\nLLM cost: ${run.cost_usd:.4f}{' (mocked)' if run.mocked else ''}")
    print(f"Approve with: python -m scope.cli approve --project {project_id} --actor 'Your Name'")
    return 0


async def _approve(svc: ScopeService, project_id: int, actor: str) -> int:
    versions = await svc.repo.list_scope_versions(project_id)
    if not versions:
        print("No scope version to approve.", file=sys.stderr)
        return 2
    try:
        await svc.approve_scope(versions[-1].id, actor)
    except GateError as exc:
        print(f"Blocked: {exc}", file=sys.stderr)
        return 2
    print(f"Scope v{versions[-1].version} of project {project_id} approved by {actor}.")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m scope.cli")
    sub = parser.add_subparsers(dest="cmd", required=True)
    run = sub.add_parser("run", help="process transcript(s) into a scope and estimate")
    run.add_argument(
        "--transcript",
        action="append",
        required=True,
        type=Path,
        help="transcript file; repeat for several meetings",
    )
    run.add_argument("--project", type=int, help="existing project id (follow-up meeting)")
    run.add_argument("--client", help="client name for a new project")
    appr = sub.add_parser("approve", help="approve the latest scope version")
    appr.add_argument("--project", type=int, required=True)
    appr.add_argument("--actor", required=True)
    args = parser.parse_args(argv)

    async def go() -> int:
        async with open_service(Settings()) as svc:
            if args.cmd == "run":
                return await _run(svc, args.transcript, args.project, args.client)
            return await _approve(svc, args.project, args.actor)

    return asyncio.run(go())


if __name__ == "__main__":
    raise SystemExit(main())
