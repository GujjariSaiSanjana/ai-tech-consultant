import { z } from "zod";
import {
  CustomerTypeSchema,
  DraftOutputSchema,
  FactSchema,
  GuardrailFlagSchema,
  IntentSchema,
  RiskClassSchema,
  TrustModeSchema,
  type TrustMode,
} from "../domain/schemas";
import type { PipelineResult } from "../pipeline/run";
import { bool, json, type Db } from "./database";
import { boolColumn, jsonColumn } from "./json-columns";

const ClassificationRowSchema = z.object({
  id: z.number(),
  email_id: z.string(),
  intent: IntentSchema,
  confidence: z.number(),
  customer_type: CustomerTypeSchema,
  risk_class: RiskClassSchema,
  injection_suspected: boolColumn,
  abuse: boolColumn,
  contains_pii: boolColumn,
  screen_reasons_json: jsonColumn(z.array(z.string())),
  kind_key: z.string(),
  counted: boolColumn,
  created_at: z.string(),
});
export type ClassificationRow = z.infer<typeof ClassificationRowSchema>;

export const DRAFT_STATUSES = ["pending", "sent", "auto_sent", "rejected"] as const;
export type DraftStatus = (typeof DRAFT_STATUSES)[number];

const DraftRowSchema = z.object({
  id: z.number(),
  email_id: z.string(),
  classification_id: z.number(),
  playbook_version_id: z.number(),
  reply_text: z.string(),
  fact_citations_json: jsonColumn(DraftOutputSchema.shape.fact_citations),
  open_questions_json: jsonColumn(z.array(z.string())),
  unsure_about: z.string(),
  facts_json: jsonColumn(z.array(FactSchema)),
  retrieval_status: z.enum(["ok", "needs_facts"]),
  retrieval_error: z.string().nullable(),
  gap_ticket_id: z.string().nullable(),
  guardrail_flags_json: jsonColumn(z.array(GuardrailFlagSchema)),
  review_reasons_json: jsonColumn(z.array(z.string())),
  needs_review: boolColumn,
  applied_rule_ids_json: jsonColumn(z.array(z.string())),
  example_ids_json: jsonColumn(z.array(z.number())),
  trust_mode: TrustModeSchema,
  send_decision: z.string(),
  status: z.enum(DRAFT_STATUSES),
  sent_text: z.string().nullable(),
  model: z.string().nullable(),
  error: z.string().nullable(),
  created_at: z.string(),
  sent_at: z.string().nullable(),
});
export type DraftRow = z.infer<typeof DraftRowSchema>;

export interface SaveDraftInput {
  emailId: string;
  result: PipelineResult;
  playbookVersionId: number;
  trustMode: TrustMode;
  sendDecision: string;
  autoSend: boolean;
  now: string;
}

export class DraftsRepo {
  constructor(private readonly db: Db) {}

  /** Stores classification + draft in one transaction. Auto-sent drafts are stored already sent. */
  save(input: SaveDraftInput): { classificationId: number; draftId: number } {
    const r = input.result;
    return this.db.transaction(() => {
      const c = this.db.run(
        `INSERT INTO classifications (email_id, intent, confidence, customer_type, risk_class, injection_suspected, abuse,
           contains_pii, screen_reasons_json, kind_key, counted, created_at)
         VALUES (:email_id, :intent, :confidence, :customer_type, :risk_class, :inj, :abuse, :pii, :reasons, :kind, :counted, :now)`,
        {
          email_id: input.emailId,
          intent: r.classification.intent,
          confidence: r.classification.confidence,
          customer_type: r.classification.customer_type,
          risk_class: r.classification.risk_class,
          inj: bool(r.screen.injection_suspected),
          abuse: bool(r.screen.abuse),
          pii: bool(r.screen.contains_pii),
          reasons: json(r.screen.reasons),
          kind: r.kindKey,
          counted: bool(r.counted),
          now: input.now,
        },
      );
      const d = this.db.run(
        `INSERT INTO drafts (email_id, classification_id, playbook_version_id, reply_text, fact_citations_json, open_questions_json,
           unsure_about, facts_json, retrieval_status, retrieval_error, gap_ticket_id, guardrail_flags_json, review_reasons_json,
           needs_review, applied_rule_ids_json, example_ids_json, trust_mode, send_decision, status, sent_text, model, error,
           created_at, sent_at)
         VALUES (:email_id, :cid, :pv, :reply, :cites, :oq, :unsure, :facts, :rstatus, :rerror, :gap, :flags, :reasons,
           :needs_review, :rules, :examples, :mode, :decision, :status, :sent_text, :model, :error, :now, :sent_at)`,
        {
          email_id: input.emailId,
          cid: c.lastInsertRowid,
          pv: input.playbookVersionId,
          reply: r.draft.reply_text,
          cites: json(r.draft.fact_citations),
          oq: json(r.draft.open_questions),
          unsure: r.draft.unsure_about,
          facts: json(r.retrieval.facts),
          rstatus: r.retrieval.status,
          rerror: r.retrieval.error,
          gap: r.retrieval.gap_ticket_id,
          flags: json(r.guardrailFlags),
          reasons: json(r.reviewReasons),
          needs_review: bool(r.needsReview),
          rules: json(r.appliedRuleIds),
          examples: json(r.exampleIds),
          mode: input.trustMode,
          decision: input.sendDecision,
          status: input.autoSend ? "auto_sent" : "pending",
          sent_text: input.autoSend ? r.draft.reply_text : null,
          model: r.draftModel,
          error: r.error,
          now: input.now,
          sent_at: input.autoSend ? input.now : null,
        },
      );
      return { classificationId: c.lastInsertRowid, draftId: d.lastInsertRowid };
    });
  }

  get(id: number): DraftRow | null {
    return this.db.get(DraftRowSchema, "SELECT * FROM drafts WHERE id = :id", { id });
  }

  latestForEmail(emailId: string): DraftRow | null {
    return this.db.get(DraftRowSchema, "SELECT * FROM drafts WHERE email_id = :id ORDER BY id DESC LIMIT 1", { id: emailId });
  }

  classification(id: number): ClassificationRow | null {
    return this.db.get(ClassificationRowSchema, "SELECT * FROM classifications WHERE id = :id", { id });
  }

  markSent(id: number, status: Exclude<DraftStatus, "pending">, sentText: string | null, now: string): void {
    this.db.run("UPDATE drafts SET status = :status, sent_text = :text, sent_at = :now WHERE id = :id", {
      id,
      status,
      text: sentText,
      now,
    });
  }

  listAutoSent(limit: number): DraftRow[] {
    return this.db.all(DraftRowSchema, "SELECT * FROM drafts WHERE status = 'auto_sent' ORDER BY sent_at DESC LIMIT :limit", { limit });
  }
}
