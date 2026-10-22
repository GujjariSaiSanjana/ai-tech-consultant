import { z } from "zod";
import { OutcomeSchema, RiskClassSchema, TrustModeSchema, type TrustMode } from "../domain/schemas";
import type { TrustState } from "../learning/trust";
import { json, type Db } from "./database";
import { jsonColumn } from "./json-columns";

const TrustRowSchema = z.object({
  kind_key: z.string(),
  risk_class: RiskClassSchema,
  mode: TrustModeSchema,
  streak: z.number(),
  outcomes_json: jsonColumn(z.array(OutcomeSchema)),
  counted_total: z.number(),
  last_reason: z.string(),
  updated_at: z.string(),
});

function toState(row: z.infer<typeof TrustRowSchema>): TrustState {
  return {
    kindKey: row.kind_key,
    riskClass: row.risk_class,
    mode: row.mode,
    streak: row.streak,
    outcomes: row.outcomes_json,
    countedTotal: row.counted_total,
    lastReason: row.last_reason,
  };
}

const TrustEventRowSchema = z.object({
  id: z.number(),
  kind_key: z.string(),
  from_mode: TrustModeSchema,
  to_mode: TrustModeSchema,
  reason: z.string(),
  created_at: z.string(),
});
export type TrustEventRow = z.infer<typeof TrustEventRowSchema>;

export class TrustRepo {
  constructor(private readonly db: Db) {}

  get(kindKey: string): TrustState | null {
    const row = this.db.get(TrustRowSchema, "SELECT * FROM trust_state WHERE kind_key = :k", { k: kindKey });
    return row ? toState(row) : null;
  }

  all(): Array<TrustState & { updatedAt: string }> {
    return this.db
      .all(TrustRowSchema, "SELECT * FROM trust_state ORDER BY counted_total DESC, kind_key")
      .map((r) => ({ ...toState(r), updatedAt: r.updated_at }));
  }

  /** Saves the new state and, when the mode changed, an audit event. */
  save(previous: TrustState | null, next: TrustState, now: string): void {
    this.db.run(
      `INSERT INTO trust_state (kind_key, risk_class, mode, streak, outcomes_json, counted_total, last_reason, updated_at)
       VALUES (:k, :risk, :mode, :streak, :outcomes, :total, :reason, :now)
       ON CONFLICT(kind_key) DO UPDATE SET mode = excluded.mode, streak = excluded.streak, outcomes_json = excluded.outcomes_json,
         counted_total = excluded.counted_total, last_reason = excluded.last_reason, updated_at = excluded.updated_at`,
      {
        k: next.kindKey,
        risk: next.riskClass,
        mode: next.mode,
        streak: next.streak,
        outcomes: json(next.outcomes),
        total: next.countedTotal,
        reason: next.lastReason,
        now,
      },
    );
    const from: TrustMode = previous?.mode ?? "shadow";
    if (from !== next.mode) {
      this.db.run(
        "INSERT INTO trust_events (kind_key, from_mode, to_mode, reason, created_at) VALUES (:k, :from, :to, :reason, :now)",
        { k: next.kindKey, from, to: next.mode, reason: next.lastReason, now },
      );
    }
  }

  events(limit: number): TrustEventRow[] {
    return this.db.all(TrustEventRowSchema, "SELECT * FROM trust_events ORDER BY created_at DESC, id DESC LIMIT :limit", { limit });
  }
}
