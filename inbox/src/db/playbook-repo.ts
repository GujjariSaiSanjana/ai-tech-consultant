import { z } from "zod";
import type { CuratedRule } from "../learning/curator";
import { json, type Db } from "./database";
import { jsonColumn } from "./json-columns";

export const VERSION_STATUSES = ["active", "archived", "rejected"] as const;
export type VersionStatus = (typeof VERSION_STATUSES)[number];

const RuleRowSchema = z.object({
  id: z.string(),
  text: z.string(),
  scope: z.string(),
  helpful: z.number(),
  harmful: z.number(),
  evidence_ids_json: jsonColumn(z.array(z.number())),
  created_at: z.string(),
});

const VersionRowSchema = z.object({
  id: z.number(),
  parent_id: z.number().nullable(),
  rule_ids_json: jsonColumn(z.array(z.string())),
  status: z.enum(VERSION_STATUSES),
  note: z.string(),
  eval_score: z.number().nullable(),
  baseline_score: z.number().nullable(),
  created_at: z.string(),
});
export type VersionRow = z.infer<typeof VersionRowSchema>;

function toRule(row: z.infer<typeof RuleRowSchema>): CuratedRule {
  return {
    id: row.id,
    text: row.text,
    scope: row.scope,
    helpful: row.helpful,
    harmful: row.harmful,
    evidenceIds: row.evidence_ids_json,
    createdAt: row.created_at,
  };
}

const ACTIVE_KEY = "active_playbook_version";

/**
 * Playbook storage. Versions are immutable lists of rule ids; the active
 * version is a single settings pointer, so rollback is one write.
 */
export class PlaybookRepo {
  constructor(private readonly db: Db) {}

  ensureInitialVersion(now: string): void {
    if (this.activeVersionId() !== null) return;
    const id = this.db.run(
      `INSERT INTO playbook_versions (parent_id, rule_ids_json, status, note, eval_score, baseline_score, created_at)
       VALUES (NULL, '[]', 'active', 'Starting playbook: voice guide only, no learned rules.', NULL, NULL, :now)`,
      { now },
    ).lastInsertRowid;
    this.setActive(id);
  }

  activeVersionId(): number | null {
    const row = this.db.get(z.object({ value: z.string() }), "SELECT value FROM settings WHERE key = :key", { key: ACTIVE_KEY });
    return row ? Number(row.value) : null;
  }

  requireActiveVersion(): VersionRow {
    const id = this.activeVersionId();
    const version = id === null ? null : this.version(id);
    if (!version) throw new Error("No active playbook version");
    return version;
  }

  private setActive(id: number): void {
    this.db.run(
      "INSERT INTO settings (key, value) VALUES (:key, :value) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
      { key: ACTIVE_KEY, value: String(id) },
    );
  }

  version(id: number): VersionRow | null {
    return this.db.get(VersionRowSchema, "SELECT * FROM playbook_versions WHERE id = :id", { id });
  }

  versions(): VersionRow[] {
    return this.db.all(VersionRowSchema, "SELECT * FROM playbook_versions ORDER BY id DESC");
  }

  rulesFor(version: VersionRow): CuratedRule[] {
    if (version.rule_ids_json.length === 0) return [];
    const all = this.db.all(RuleRowSchema, "SELECT * FROM playbook_rules").map(toRule);
    const byId = new Map(all.map((r) => [r.id, r]));
    return version.rule_ids_json.map((id) => byId.get(id)).filter((r): r is CuratedRule => r !== undefined);
  }

  nextRuleId(): () => string {
    const row = this.db.get(z.object({ n: z.number() }), "SELECT COUNT(*) AS n FROM playbook_rules");
    let n = row?.n ?? 0;
    return () => {
      n += 1;
      return `R-${String(n).padStart(4, "0")}`;
    };
  }

  /** Upserts rule rows (new rules and updated counters). */
  saveRules(rules: readonly CuratedRule[]): void {
    for (const r of rules) {
      this.db.run(
        `INSERT INTO playbook_rules (id, text, scope, helpful, harmful, evidence_ids_json, created_at)
         VALUES (:id, :text, :scope, :helpful, :harmful, :evidence, :created)
         ON CONFLICT(id) DO UPDATE SET helpful = excluded.helpful, harmful = excluded.harmful, evidence_ids_json = excluded.evidence_ids_json`,
        { id: r.id, text: r.text, scope: r.scope, helpful: r.helpful, harmful: r.harmful, evidence: json(r.evidenceIds), created: r.createdAt },
      );
    }
  }

  adjustCounters(ruleIds: readonly string[], field: "helpful" | "harmful"): void {
    for (const id of ruleIds) {
      this.db.run(`UPDATE playbook_rules SET ${field} = ${field} + 1 WHERE id = :id`, { id });
    }
  }

  createVersion(input: {
    parentId: number;
    ruleIds: readonly string[];
    status: VersionStatus;
    note: string;
    evalScore: number | null;
    baselineScore: number | null;
    now: string;
  }): number {
    return this.db.run(
      `INSERT INTO playbook_versions (parent_id, rule_ids_json, status, note, eval_score, baseline_score, created_at)
       VALUES (:parent, :rules, :status, :note, :score, :baseline, :now)`,
      {
        parent: input.parentId,
        rules: json(input.ruleIds),
        status: input.status,
        note: input.note,
        score: input.evalScore,
        baseline: input.baselineScore,
        now: input.now,
      },
    ).lastInsertRowid;
  }

  setVersionScore(id: number, score: number): void {
    this.db.run("UPDATE playbook_versions SET eval_score = :score WHERE id = :id", { id, score });
  }

  /** Makes `id` the active version; the previous active one is archived. Used for promotion and rollback. */
  activate(id: number): void {
    const target = this.version(id);
    if (!target) throw new Error(`Unknown playbook version ${id}`);
    this.db.transaction(() => {
      this.db.run("UPDATE playbook_versions SET status = 'archived' WHERE status = 'active'");
      this.db.run("UPDATE playbook_versions SET status = 'active' WHERE id = :id", { id });
      this.setActive(id);
    });
  }
}
