"""`python -m brain.evals.run [--models a,b] [--split dev|heldout] [--mock] [--reps N]`

Runs every case through the same BrainService the API uses and writes one row per case per
model per repetition to evals/results/brain/<run_id>.jsonl (docs/contracts/eval-results.md).
Without ANTHROPIC_API_KEY (or with --mock) answers come from the offline extractive answerer
and every row says `mocked: true`; such numbers measure retrieval and abstention, not Claude.

Cases tagged `removed` first mark their article removed in the target database, exactly as a
sitemap removal would; the next sync restores it.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import secrets
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from brain.config import REPO_DIR, Settings
from brain.container import build_container
from brain.evals.cases import CASES_PATH, EvalCase, load_cases
from brain.evals.scoring import Verdict, judge, judge_feasibility
from brain.migrate import migrate
from brain.service import BrainService

RESULTS_DIR = REPO_DIR / "evals" / "results" / "brain"
log = logging.getLogger(__name__)


def new_run_id() -> str:
    return f"brain-{datetime.now(UTC):%Y%m%dT%H%M%SZ}-{secrets.token_hex(3)}"


async def run_cases(
    settings: Settings, cases: list[EvalCase], *, run_id: str, reps: int, force_mock: bool
) -> list[dict[str, Any]]:
    offline = {"anthropic_api_key": None, "gemini_api_key": None, "nvidia_api_key": None}
    effective = settings.model_copy(update=offline) if force_mock else settings
    rows: list[dict[str, Any]] = []
    async with build_container(effective) as c:
        mocked = not c.llm_live
        for case in cases:
            for article_id in case.setup.remove_article_ids:
                await c.repo.mark_removed(article_id)
        for rep in range(reps):
            for case in cases:
                rows.append(await _one(c.service, case, run_id=run_id, rep=rep, mocked=mocked))
    return rows


async def _one(service: BrainService, case: EvalCase, *, run_id: str, rep: int, mocked: bool) -> dict[str, Any]:
    base = {
        "task": "brain",
        "run_id": run_id,
        "case_id": case.id,
        "split": case.split,
        "tags": case.tags,
        "rep": rep,
        "mocked": mocked,
    }
    started = time.perf_counter()
    try:
        if case.endpoint == "feasibility":
            feasibility, used = await service.feasibility_with_usage(case.question)
            verdict: Verdict = judge_feasibility(case, feasibility)
            model = service.answer_model
        else:
            response = await service.ask(case.question)
            verdict, used, model = judge(case, response), response.usage, response.model
    except Exception as exc:  # infra errors are rows too, excluded from quality metrics by the reporter
        return base | {
            "model": service.answer_model,
            "passed": False,
            "score": 0.0,
            "cost_usd": 0.0,
            "latency_ms": int((time.perf_counter() - started) * 1000),
            "input_tokens": 0,
            "output_tokens": 0,
            "error": f"{type(exc).__name__}: {exc}"[:500],
        }
    return base | {
        "model": model,
        "passed": verdict.passed,
        "score": verdict.score,
        "cost_usd": used.cost_usd,
        "latency_ms": used.latency_ms,
        "input_tokens": used.input_tokens,
        "output_tokens": used.output_tokens,
        "error": None,
    }


def write_rows(rows: list[dict[str, Any]], run_id: str, results_dir: Path = RESULTS_DIR) -> Path:
    results_dir.mkdir(parents=True, exist_ok=True)
    path = results_dir / f"{run_id}.jsonl"
    path.write_text("".join(json.dumps(row, sort_keys=True) + "\n" for row in rows))
    return path


def summarize(rows: list[dict[str, Any]]) -> str:
    lines = []
    keys = sorted({(r["model"], r["split"]) for r in rows})
    for model, split in keys:
        group = [r for r in rows if r["model"] == model and r["split"] == split and r["error"] is None]
        errors = sum(1 for r in rows if r["model"] == model and r["split"] == split and r["error"] is not None)
        passed = sum(r["passed"] for r in group)
        mean = sum(r["score"] for r in group) / len(group) if group else 0.0
        lines.append(f"{model} [{split}] pass {passed}/{len(group)}  mean score {mean:.3f}  errors {errors}")
    return "\n".join(lines)


async def _main(args: argparse.Namespace) -> None:
    base = Settings()
    await migrate(base.database_url)
    cases = [c for c in load_cases(args.cases) if args.split is None or c.split == args.split]
    models = [m.strip() for m in args.models.split(",")] if args.models else [base.answer_model]
    if (args.mock or not base.anthropic_api_key) and len(models) > 1:
        log.warning("mock mode answers offline; --models is ignored")
        models = models[:1]
    run_id = new_run_id()
    rows: list[dict[str, Any]] = []
    for model in models:
        settings = base.model_copy(update={"answer_model": model})
        rows += await run_cases(settings, cases, run_id=run_id, reps=args.reps, force_mock=args.mock)
    path = write_rows(rows, run_id)
    print(summarize(rows))
    print(f"wrote {len(rows)} rows to {path}")


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="python -m brain.evals.run", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--models", default=None, help="comma-separated answer models, e.g. claude-sonnet-5-5")
    parser.add_argument("--split", choices=["dev", "heldout"], default=None)
    parser.add_argument("--mock", action="store_true", help="never call Claude, even with a key")
    parser.add_argument("--reps", type=int, default=1)
    parser.add_argument("--cases", type=Path, default=CASES_PATH)
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s: %(message)s")
    asyncio.run(_main(parser.parse_args()))


if __name__ == "__main__":
    main()
