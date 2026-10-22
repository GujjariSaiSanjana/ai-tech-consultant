import { z } from "zod";
import { HumanActionKindSchema, OutcomeSchema, type HumanActionKind, type Intent, type Outcome } from "../domain/schemas";
import { bool, json, type Db } from "./database";
import { boolColumn, jsonColumn, nullableBoolColumn } from "./json-columns";

const HumanActionRowSchema = z.object({
  id: z.number(),
  draft_id: z.number(),
  email_id: z.string(),
  action: HumanActionKindSchema,
  sent_text: z.string().nullable(),
  edit_distance: z.number().nullable(),
  cosine: z.number().nullable(),
  judge_material_change: nullableBoolColumn,
  outcome: OutcomeSchema.nullable(),
  kind_key: z.string(),
  counted: boolColumn,
  playbook_version_id: z.number(),
  simulated: boolColumn,
  created_at: z.string(),
});
export type HumanActionRow = z.infer<typeof HumanActionRowSchema>;

export interface NewHumanAction {
  draftId: number;
  emailId: string;
  action: HumanActionKind;
  sentText: string | null;
  editDistance: number | null;
  cosine: number | null;
  judgeMaterialChange: boolean | null;
  outcome: Outcome | null;
  kindKey: string;
  counted: boolean;
  playbookVersionId: number;
  simulated: boolean;
  now: string;
}

const ExampleRowSchema = z.object({
  id: z.number(),
  intent: z.string(),
  email_text: z.string(),
  reply_text: z.string(),
  embedding_json: jsonColumn(z.array(z.number())),
});
export type ExampleRow = z.infer<typeof ExampleRowSchema>;

export interface NewExample {
  humanActionId: number;
  emailId: string;
  intent: Intent;
  kindKey: string;
  emailText: string;
  replyText: string;
  embedder: string;
  embedding: number[];
  now: string;
}

export class ActionsRepo {
  constructor(private readonly db: Db) {}

  insert(a: NewHumanAction): number {
    return this.db.run(
      `INSERT INTO human_actions (draft_id, email_id, action, sent_text, edit_distance, cosine, judge_material_change, outcome,
         kind_key, counted, playbook_version_id, simulated, created_at)
       VALUES (:draft, :email, :action, :text, :dist, :cos, :judge, :outcome, :kind, :counted, :pv, :sim, :now)`,
      {
        draft: a.draftId,
        email: a.emailId,
        action: a.action,
        text: a.sentText,
        dist: a.editDistance,
        cos: a.cosine,
        judge: a.judgeMaterialChange === null ? null : bool(a.judgeMaterialChange),
        outcome: a.outcome,
        kind: a.kindKey,
        counted: bool(a.counted),
        pv: a.playbookVersionId,
        sim: bool(a.simulated),
        now: a.now,
      },
    ).lastInsertRowid;
  }

  /** Every action with an outcome, oldest first: the raw series behind the learning chart. */
  listOutcomes(): HumanActionRow[] {
    return this.db.all(HumanActionRowSchema, "SELECT * FROM human_actions WHERE outcome IS NOT NULL ORDER BY created_at, id");
  }

  countSimulated(): number {
    return this.db.get(z.object({ n: z.number() }), "SELECT COUNT(*) AS n FROM human_actions WHERE simulated = 1")?.n ?? 0;
  }

  insertExample(e: NewExample): number {
    return this.db.run(
      `INSERT INTO examples (human_action_id, email_id, intent, kind_key, email_text, reply_text, embedder, embedding_json, created_at)
       VALUES (:ha, :email, :intent, :kind, :etext, :rtext, :embedder, :embedding, :now)`,
      {
        ha: e.humanActionId,
        email: e.emailId,
        intent: e.intent,
        kind: e.kindKey,
        etext: e.emailText,
        rtext: e.replyText,
        embedder: e.embedder,
        embedding: json(e.embedding),
        now: e.now,
      },
    ).lastInsertRowid;
  }

  examplesForIntent(intent: Intent, embedder: string): ExampleRow[] {
    return this.db.all(
      ExampleRowSchema,
      "SELECT id, intent, email_text, reply_text, embedding_json FROM examples WHERE intent = :intent AND embedder = :embedder",
      { intent, embedder },
    );
  }

  countExamples(): number {
    return this.db.get(z.object({ n: z.number() }), "SELECT COUNT(*) AS n FROM examples")?.n ?? 0;
  }
}
