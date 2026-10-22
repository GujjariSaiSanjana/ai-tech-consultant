import { z } from "zod";
import type { LlmCallRecord, LlmCallSink } from "../llm/client";
import { bool, type Db } from "./database";
import { boolColumn } from "./json-columns";

const SettingRowSchema = z.object({ value: z.string() });

export class SettingsRepo {
  constructor(private readonly db: Db) {}

  get(key: string): string | null {
    return this.db.get(SettingRowSchema, "SELECT value FROM settings WHERE key = :key", { key })?.value ?? null;
  }

  set(key: string, value: string): void {
    this.db.run(
      "INSERT INTO settings (key, value) VALUES (:key, :value) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
      { key, value },
    );
  }

  killSwitch(): boolean {
    return this.get("kill_switch") === "on";
  }

  setKillSwitch(on: boolean): void {
    this.set("kill_switch", on ? "on" : "off");
  }
}

/** Persists every model call; doubles as the LLM client's sink. */
export class LlmCallsRepo implements LlmCallSink {
  constructor(
    private readonly db: Db,
    private readonly clock: () => string,
  ) {}

  record(call: LlmCallRecord): void {
    this.db.run(
      `INSERT INTO llm_calls (purpose, model, input_tokens, output_tokens, cache_read_tokens, cache_write_tokens, cost_usd,
         latency_ms, stop_reason, error, mocked, created_at)
       VALUES (:purpose, :model, :inp, :out, :cr, :cw, :cost, :lat, :stop, :error, :mocked, :now)`,
      {
        purpose: call.purpose,
        model: call.model,
        inp: call.usage.inputTokens,
        out: call.usage.outputTokens,
        cr: call.usage.cacheReadTokens,
        cw: call.usage.cacheWriteTokens,
        cost: call.costUsd,
        lat: call.latencyMs,
        stop: call.stopReason,
        error: call.error,
        mocked: bool(call.mocked),
        now: this.clock(),
      },
    );
  }

  summary(): Array<{ purpose: string; model: string; calls: number; cost_usd: number; errors: number; mocked: boolean }> {
    return this.db.all(
      z.object({ purpose: z.string(), model: z.string(), calls: z.number(), cost_usd: z.number(), errors: z.number(), mocked: boolColumn }),
      `SELECT purpose, model, COUNT(*) AS calls, COALESCE(SUM(cost_usd), 0) AS cost_usd,
         SUM(CASE WHEN error IS NULL THEN 0 ELSE 1 END) AS errors, MAX(mocked) AS mocked
       FROM llm_calls GROUP BY purpose, model ORDER BY purpose`,
    );
  }
}

const EvalRunRowSchema = z.object({
  id: z.number(),
  run_id: z.string(),
  purpose: z.string(),
  playbook_version_id: z.number().nullable(),
  split: z.string(),
  cases: z.number(),
  mean_score: z.number(),
  pass_rate: z.number(),
  mocked: boolColumn,
  created_at: z.string(),
});
export type EvalRunRow = z.infer<typeof EvalRunRowSchema>;

export class EvalRunsRepo {
  constructor(private readonly db: Db) {}

  insert(r: Omit<EvalRunRow, "id">): void {
    this.db.run(
      `INSERT INTO eval_runs (run_id, purpose, playbook_version_id, split, cases, mean_score, pass_rate, mocked, created_at)
       VALUES (:run_id, :purpose, :pv, :split, :cases, :mean, :pass, :mocked, :now)`,
      {
        run_id: r.run_id,
        purpose: r.purpose,
        pv: r.playbook_version_id,
        split: r.split,
        cases: r.cases,
        mean: r.mean_score,
        pass: r.pass_rate,
        mocked: bool(r.mocked),
        now: r.created_at,
      },
    );
  }

  recent(limit: number): EvalRunRow[] {
    return this.db.all(EvalRunRowSchema, "SELECT * FROM eval_runs ORDER BY id DESC LIMIT :limit", { limit });
  }
}
