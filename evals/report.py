"""Eval reporter: turns evals/results/<task>/*.jsonl into Markdown tables.

Contract: docs/contracts/eval-results.md. Standard library only.

Usage:
    python evals/report.py                 # all splits
    python evals/report.py --split heldout # held-out cases only

Rules (kept simple on purpose):
- Rows with a non-null "error" are infra failures. They are counted in the
  "errors" column and in cost, but excluded from pass rate, score and latency.
- Pass rate is over (case x rep) runs, with a Wilson 95% interval.
- Cost per case = mean cost_usd per run, including error runs (they still cost money).
- If any row has "mocked": true, a loud banner says the numbers are not real.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

RESULTS_DIR = Path(__file__).resolve().parent / "results"
DEFAULT_USD_TO_INR = 94.59
MOCK_BANNER = "> **MOCK RUN — not real model numbers.** Some rows were produced without an API key."
Row = dict[str, Any]


def wilson_interval(passed: int, total: int, z: float = 1.96) -> tuple[float, float]:
    """Wilson score interval for a binomial proportion (default 95%)."""
    if total <= 0:
        raise ValueError("total must be positive")
    if not 0 <= passed <= total:
        raise ValueError("passed must be between 0 and total")
    p = passed / total
    z2 = z * z
    denom = 1 + z2 / total
    center = (p + z2 / (2 * total)) / denom
    margin = z * math.sqrt(p * (1 - p) / total + z2 / (4 * total * total)) / denom
    return max(0.0, center - margin), min(1.0, center + margin)


def percentile(values: list[float], q: float) -> float:
    """Percentile with linear interpolation between closest ranks (q in 0..100)."""
    if not values:
        raise ValueError("values must not be empty")
    if not 0 <= q <= 100:
        raise ValueError("q must be between 0 and 100")
    ordered = sorted(values)
    pos = (len(ordered) - 1) * q / 100
    lower = math.floor(pos)
    upper = math.ceil(pos)
    if lower == upper:
        return float(ordered[lower])
    frac = pos - lower
    return float(ordered[lower] + (ordered[upper] - ordered[lower]) * frac)


def load_rows(results_dir: Path, split: str | None = None) -> tuple[list[Row], int]:
    """Read every results/<task>/*.jsonl file. Returns (rows, bad_line_count)."""
    rows: list[Row] = []
    bad = 0
    for path in sorted(results_dir.glob("*/*.jsonl")):
        if path.parent.name == "raw":
            continue
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                bad += 1
                continue
            if not isinstance(row, dict) or "task" not in row:
                bad += 1
                continue
            if split is not None and row.get("split") != split:
                continue
            rows.append(row)
    return rows, bad


@dataclass
class Summary:
    task: str
    model: str
    cases: int
    reps: int
    runs: int
    scored: int
    passed: int
    mean_score: float | None
    cost_per_case_usd: float
    p50_ms: float | None
    p95_ms: float | None
    errors: int
    mocked: bool

    @property
    def pass_rate(self) -> float | None:
        return self.passed / self.scored if self.scored else None

    @property
    def ci(self) -> tuple[float, float] | None:
        return wilson_interval(self.passed, self.scored) if self.scored else None


def summarize(rows: list[Row]) -> list[Summary]:
    """Group rows by (task, model) and compute the report numbers."""
    groups: dict[tuple[str, str], list[Row]] = {}
    for row in rows:
        key = (str(row["task"]), str(row.get("model") or "unknown"))
        groups.setdefault(key, []).append(row)

    out: list[Summary] = []
    for (task, model), group in sorted(groups.items()):
        ok = [r for r in group if not r.get("error")]
        latencies = [float(r.get("latency_ms", 0)) for r in ok]
        out.append(
            Summary(
                task=task,
                model=model,
                cases=len({r.get("case_id") for r in group}),
                reps=len({r.get("rep") for r in group}),
                runs=len(group),
                scored=len(ok),
                passed=sum(1 for r in ok if r.get("passed") is True),
                mean_score=(sum(float(r.get("score", 0)) for r in ok) / len(ok))
                if ok
                else None,
                cost_per_case_usd=sum(float(r.get("cost_usd", 0)) for r in group)
                / len(group),
                p50_ms=percentile(latencies, 50) if latencies else None,
                p95_ms=percentile(latencies, 95) if latencies else None,
                errors=len(group) - len(ok),
                mocked=any(r.get("mocked") is True for r in group),
            )
        )
    return out


def _fmt(value: float | None, spec: str) -> str:
    return "n/a" if value is None else format(value, spec)


def render(
    summaries: list[Summary], usd_to_inr: float, split: str | None, bad_lines: int = 0
) -> str:
    """Render the Markdown report."""
    lines = ["# Eval summary", ""]
    lines.append(f"Split: {split or 'all'}. USD to INR rate: {usd_to_inr}.")
    lines.append("")
    if any(s.mocked for s in summaries):
        lines += [MOCK_BANNER, ""]
    if bad_lines:
        lines += [f"Warning: skipped {bad_lines} malformed line(s).", ""]
    if not summaries:
        lines.append("No results yet. Run `make eval`.")
        return "\n".join(lines) + "\n"

    header = (
        "| model | cases × reps | pass rate (95% CI) | mean score | cost/case USD | cost/case INR "
        "| p50 ms | p95 ms | errors |"
    )
    sep = "|---|---|---|---|---|---|---|---|---|"
    for task in sorted({s.task for s in summaries}):
        lines += [f"## {task}", ""]
        task_rows = [s for s in summaries if s.task == task]
        if any(s.mocked for s in task_rows):
            lines += [MOCK_BANNER, ""]
        lines += [header, sep]
        for s in task_rows:
            ci = s.ci
            rate = (
                "n/a"
                if s.pass_rate is None or ci is None
                else f"{s.pass_rate:.1%} [{ci[0]:.2f}, {ci[1]:.2f}]"
            )
            lines.append(
                f"| {s.model}{' (mock)' if s.mocked else ''} | {s.cases} × {s.reps} | {rate} "
                f"| {_fmt(s.mean_score, '.3f')} | ${s.cost_per_case_usd:.4f} "
                f"| ₹{s.cost_per_case_usd * usd_to_inr:.2f} | {_fmt(s.p50_ms, '.0f')} "
                f"| {_fmt(s.p95_ms, '.0f')} | {s.errors} |"
            )
        lines.append("")
    return "\n".join(lines)


def usd_to_inr_from_env() -> float:
    raw = os.environ.get("USD_TO_INR", "").strip()
    if not raw:
        return DEFAULT_USD_TO_INR
    try:
        return float(raw)
    except ValueError:
        print(
            f"USD_TO_INR={raw!r} is not a number; using {DEFAULT_USD_TO_INR}",
            file=sys.stderr,
        )
        return DEFAULT_USD_TO_INR


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Summarize eval results as Markdown.")
    parser.add_argument("--split", choices=["dev", "heldout"], default=None)
    parser.add_argument("--results-dir", type=Path, default=RESULTS_DIR)
    parser.add_argument(
        "--out", type=Path, default=None, help="default: <results-dir>/summary.md"
    )
    args = parser.parse_args(argv)

    rows, bad = load_rows(args.results_dir, args.split)
    text = render(summarize(rows), usd_to_inr_from_env(), args.split, bad)
    out = args.out or args.results_dir / "summary.md"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(text, encoding="utf-8")
    print(text)
    print(f"Wrote {out}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
