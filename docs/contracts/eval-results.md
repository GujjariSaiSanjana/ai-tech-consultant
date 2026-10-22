# Eval results contract

Every task's eval runner writes one JSON object per line to
`evals/results/<task>/<run_id>.jsonl` (task = brain | inbox | scope):

```
{
  "task": "brain" | "inbox" | "scope",
  "run_id": str,
  "case_id": str,
  "split": "dev" | "heldout",
  "tags": [str],                 # e.g. ["unanswerable"], ["injection"], ["image_only"]
  "model": str,                  # model id reported by the API response
  "rep": int,                    # repetition index, 0-based
  "passed": bool,
  "score": float,                # 0..1
  "cost_usd": float,
  "latency_ms": int,
  "input_tokens": int,
  "output_tokens": int,
  "error": str | null,           # infra error; such rows are excluded from quality metrics
  "mocked": bool                 # true when produced without a real API key
}
```

`python evals/report.py` reads every file and prints Markdown tables (pass rate with Wilson 95% CI,
mean score, cost per case in USD and INR, p50/p95 latency, error count) per task and model.
