import { z } from "zod";
import type { BrainClient } from "../brain/schemas";
import type { DatasetEmail } from "../domain/schemas";
import { addUsage, ZERO_USAGE, type TokenUsage } from "../llm/cost";
import type { LlmCallRecord, LlmClient } from "../llm/client";
import { wilsonLowerBound } from "../learning/stats";
import { runPipeline, type ExampleMemory, type PlaybookContext } from "../pipeline/run";
import { scoreCase, type CaseChecks } from "./scoring";

/** One row of docs/contracts/eval-results.md. */
export const EvalRowSchema = z.object({
  task: z.literal("inbox"),
  run_id: z.string(),
  case_id: z.string(),
  split: z.enum(["dev", "heldout"]),
  tags: z.array(z.string()),
  model: z.string(),
  rep: z.number().int(),
  passed: z.boolean(),
  score: z.number(),
  cost_usd: z.number(),
  latency_ms: z.number().int(),
  input_tokens: z.number().int(),
  output_tokens: z.number().int(),
  error: z.string().nullable(),
  mocked: z.boolean(),
});
export type EvalRow = z.infer<typeof EvalRowSchema>;

/** Collects the calls made while one case runs, so cost and tokens are per case. */
export class CaseUsageSink {
  private usage: TokenUsage = ZERO_USAGE;
  private cost = 0;

  record(call: LlmCallRecord): void {
    this.usage = addUsage(this.usage, call.usage);
    this.cost += call.costUsd;
  }

  take(): { usage: TokenUsage; cost: number } {
    const out = { usage: this.usage, cost: this.cost };
    this.usage = ZERO_USAGE;
    this.cost = 0;
    return out;
  }
}

export interface EvalRunInput {
  runId: string;
  cases: readonly DatasetEmail[];
  reps: number;
  llm: LlmClient;
  sink: CaseUsageSink;
  routeModel: string;
  brain: BrainClient;
  memory: ExampleMemory;
  playbook: PlaybookContext;
}

export interface EvalCaseDetail {
  row: EvalRow;
  checks: CaseChecks | null;
}

export async function runEval(input: EvalRunInput): Promise<EvalCaseDetail[]> {
  const details: EvalCaseDetail[] = [];
  for (let rep = 0; rep < input.reps; rep++) {
    for (const c of input.cases) {
      input.sink.take();
      const started = Date.now();
      let row: EvalRow;
      let checks: CaseChecks | null = null;
      try {
        const result = await runPipeline(c, { llm: input.llm, brain: input.brain, memory: input.memory }, input.playbook);
        const scored = scoreCase(c.gold, c.tags, result);
        checks = scored.checks;
        const { usage, cost } = input.sink.take();
        row = {
          task: "inbox",
          run_id: input.runId,
          case_id: c.id,
          split: c.split,
          tags: c.tags,
          model: result.draftModel ?? input.routeModel,
          rep,
          passed: scored.passed,
          score: scored.score,
          cost_usd: cost,
          latency_ms: Date.now() - started,
          input_tokens: usage.inputTokens + usage.cacheReadTokens + usage.cacheWriteTokens,
          output_tokens: usage.outputTokens,
          error: result.error,
          mocked: input.llm.mocked,
        };
      } catch (error) {
        const { usage, cost } = input.sink.take();
        row = {
          task: "inbox",
          run_id: input.runId,
          case_id: c.id,
          split: c.split,
          tags: c.tags,
          model: input.routeModel,
          rep,
          passed: false,
          score: 0,
          cost_usd: cost,
          latency_ms: Date.now() - started,
          input_tokens: usage.inputTokens,
          output_tokens: usage.outputTokens,
          error: error instanceof Error ? error.message : String(error),
          mocked: input.llm.mocked,
        };
      }
      details.push({ row: EvalRowSchema.parse(row), checks });
    }
  }
  return details;
}

function percentile(sorted: readonly number[], p: number): number {
  if (sorted.length === 0) return 0;
  const index = Math.min(sorted.length - 1, Math.max(0, Math.ceil(p * sorted.length) - 1));
  return sorted[index] ?? 0;
}

export interface EvalSummary {
  model: string;
  cases: number;
  errors: number;
  passRate: number;
  passRateLow: number;
  passRateHigh: number;
  meanScore: number;
  intentAccuracy: number;
  riskAccuracy: number;
  safetyRate: number;
  costUsd: number;
  p50LatencyMs: number;
  p95LatencyMs: number;
  mocked: boolean;
}

/** Quality metrics exclude infra-error rows, as the contract requires. */
export function summarize(details: readonly EvalCaseDetail[]): EvalSummary[] {
  const byModel = new Map<string, EvalCaseDetail[]>();
  for (const d of details) byModel.set(d.row.model, [...(byModel.get(d.row.model) ?? []), d]);
  return [...byModel.entries()].map(([model, rows]) => {
    const ok = rows.filter((r) => r.row.error === null && r.checks !== null);
    const n = ok.length;
    const passed = ok.filter((r) => r.row.passed).length;
    const frac = (f: (c: CaseChecks) => boolean) => (n === 0 ? 0 : ok.filter((r) => r.checks && f(r.checks)).length / n);
    const latencies = rows.map((r) => r.row.latency_ms).sort((a, b) => a - b);
    const flipped = n - passed;
    return {
      model,
      cases: rows.length,
      errors: rows.length - n,
      passRate: n === 0 ? 0 : passed / n,
      passRateLow: wilsonLowerBound(passed, n, 1.96),
      passRateHigh: 1 - wilsonLowerBound(flipped, n, 1.96),
      meanScore: n === 0 ? 0 : ok.reduce((s, r) => s + r.row.score, 0) / n,
      intentAccuracy: frac((c) => c.intentCorrect),
      riskAccuracy: frac((c) => c.riskCorrect),
      safetyRate: frac((c) => c.safetyOk),
      costUsd: rows.reduce((s, r) => s + r.row.cost_usd, 0),
      p50LatencyMs: percentile(latencies, 0.5),
      p95LatencyMs: percentile(latencies, 0.95),
      mocked: rows.some((r) => r.row.mocked),
    };
  });
}
