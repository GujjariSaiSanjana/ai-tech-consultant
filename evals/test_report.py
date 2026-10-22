"""Tests for evals/report.py. Run: python3 evals/test_report.py (or pytest evals)."""

from __future__ import annotations

import contextlib
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))

import report


def row(**overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "task": "brain",
        "run_id": "r1",
        "case_id": "c1",
        "split": "dev",
        "tags": [],
        "model": "claude-sonnet-5-5",
        "rep": 0,
        "passed": True,
        "score": 1.0,
        "cost_usd": 0.01,
        "latency_ms": 100,
        "input_tokens": 10,
        "output_tokens": 5,
        "error": None,
        "mocked": False,
    }
    base.update(overrides)
    return base


class WilsonTest(unittest.TestCase):
    def test_27_of_30(self) -> None:
        lo, hi = report.wilson_interval(27, 30)
        self.assertAlmostEqual(lo, 0.744, places=3)
        self.assertAlmostEqual(hi, 0.965, places=3)

    def test_all_pass_upper_bound_is_one(self) -> None:
        lo, hi = report.wilson_interval(20, 20)
        self.assertEqual(hi, 1.0)
        self.assertAlmostEqual(lo, 0.839, places=3)

    def test_zero_pass(self) -> None:
        lo, hi = report.wilson_interval(0, 10)
        self.assertEqual(lo, 0.0)
        self.assertAlmostEqual(hi, 0.278, places=3)

    def test_invalid_inputs(self) -> None:
        with self.assertRaises(ValueError):
            report.wilson_interval(0, 0)
        with self.assertRaises(ValueError):
            report.wilson_interval(5, 4)


class PercentileTest(unittest.TestCase):
    def test_median_odd_and_even(self) -> None:
        self.assertEqual(report.percentile([3, 1, 2], 50), 2.0)
        self.assertEqual(report.percentile([1, 2, 3, 4], 50), 2.5)

    def test_p95_interpolates(self) -> None:
        values = [float(v) for v in range(1, 101)]  # 1..100
        self.assertAlmostEqual(report.percentile(values, 95), 95.05)

    def test_single_value_and_bounds(self) -> None:
        self.assertEqual(report.percentile([7], 95), 7.0)
        self.assertEqual(report.percentile([1, 9], 0), 1.0)
        self.assertEqual(report.percentile([1, 9], 100), 9.0)
        with self.assertRaises(ValueError):
            report.percentile([], 50)


class SummarizeTest(unittest.TestCase):
    def test_error_rows_excluded_from_quality(self) -> None:
        rows = [
            row(case_id="c1", passed=True, score=1.0, latency_ms=100),
            row(case_id="c2", passed=False, score=0.0, latency_ms=300),
            row(
                case_id="c3",
                passed=False,
                score=0.0,
                latency_ms=99999,
                error="529 overloaded",
            ),
        ]
        (s,) = report.summarize(rows)
        self.assertEqual(s.errors, 1)
        self.assertEqual(s.scored, 2)
        self.assertEqual(s.pass_rate, 0.5)
        self.assertEqual(s.mean_score, 0.5)
        self.assertEqual(s.p95_ms, 290.0)  # the 99999 ms error row is ignored
        self.assertEqual(s.cases, 3)
        self.assertAlmostEqual(
            s.cost_per_case_usd, 0.01
        )  # cost still counts error rows

    def test_groups_by_task_and_model_and_counts_reps(self) -> None:
        rows = [row(rep=r, case_id=c) for r in range(3) for c in ("a", "b")]
        rows.append(row(model="claude-haiku-5-5"))
        rows.append(row(task="scope"))
        out = report.summarize(rows)
        keys = [(s.task, s.model) for s in out]
        self.assertEqual(
            keys,
            [
                ("brain", "claude-haiku-5-5"),
                ("brain", "claude-sonnet-5-5"),
                ("scope", "claude-sonnet-5-5"),
            ],
        )
        sonnet = out[1]
        self.assertEqual((sonnet.cases, sonnet.reps, sonnet.runs), (2, 3, 6))

    def test_all_errors_gives_na(self) -> None:
        (s,) = report.summarize([row(error="timeout")])
        self.assertIsNone(s.pass_rate)
        text = report.render([s], 94.59, None)
        self.assertIn("n/a", text)


class RenderTest(unittest.TestCase):
    def test_mock_banner_shown_only_for_mock_rows(self) -> None:
        real = report.render(report.summarize([row()]), 94.59, None)
        self.assertNotIn("MOCK RUN", real)
        mock = report.render(report.summarize([row(mocked=True)]), 94.59, None)
        self.assertIn("MOCK RUN — not real model numbers", mock)
        self.assertIn("(mock)", mock)

    def test_inr_conversion_and_ci_text(self) -> None:
        rows = [row(case_id=f"c{i}", passed=i < 27, cost_usd=0.02) for i in range(30)]
        text = report.render(report.summarize(rows), 100.0, None)
        self.assertIn("90.0% [0.74, 0.97]", text)
        self.assertIn("$0.0200", text)
        self.assertIn("₹2.00", text)

    def test_empty(self) -> None:
        self.assertIn("No results yet", report.render([], 94.59, None))


class LoadAndMainTest(unittest.TestCase):
    def write(
        self, root: Path, task: str, rows: list[dict[str, Any]], extra: str = ""
    ) -> None:
        folder = root / task
        folder.mkdir(parents=True, exist_ok=True)
        body = "\n".join(json.dumps(r) for r in rows) + "\n" + extra
        (folder / "run1.jsonl").write_text(body, encoding="utf-8")

    def test_split_filter_bad_lines_and_raw_ignored(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.write(
                root,
                "brain",
                [row(split="dev"), row(split="heldout")],
                extra="{not json\n",
            )
            self.write(root, "raw", [row()])
            rows, bad = report.load_rows(root)
            self.assertEqual((len(rows), bad), (2, 1))
            held, _ = report.load_rows(root, "heldout")
            self.assertEqual([r["split"] for r in held], ["heldout"])

    def test_main_writes_summary(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.write(root, "inbox", [row(task="inbox", mocked=True)])
            with (
                contextlib.redirect_stdout(io.StringIO()),
                contextlib.redirect_stderr(io.StringIO()),
            ):
                report.main(["--results-dir", str(root)])
            text = (root / "summary.md").read_text(encoding="utf-8")
            self.assertIn("## inbox", text)
            self.assertIn("MOCK RUN", text)

    def test_usd_to_inr_env(self) -> None:
        import os
        from unittest import mock

        with mock.patch.dict(os.environ, {"USD_TO_INR": "80"}):
            self.assertEqual(report.usd_to_inr_from_env(), 80.0)
        with mock.patch.dict(os.environ, {"USD_TO_INR": ""}):
            self.assertEqual(report.usd_to_inr_from_env(), 94.59)


if __name__ == "__main__":
    unittest.main()
