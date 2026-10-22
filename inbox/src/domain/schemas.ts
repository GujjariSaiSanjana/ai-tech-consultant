import { z } from "zod";

export const INTENTS = [
  "how_to",
  "bug_report",
  "new_project",
  "quote_request",
  "refund",
  "cancellation",
  "complaint",
  "billing",
  "follow_up",
  "partnership",
  "spam",
  "other",
] as const;
export const IntentSchema = z.enum(INTENTS);
export type Intent = z.infer<typeof IntentSchema>;

export const CUSTOMER_TYPES = ["new", "existing", "agency"] as const;
export const CustomerTypeSchema = z.enum(CUSTOMER_TYPES);
export type CustomerType = z.infer<typeof CustomerTypeSchema>;

export const RISK_CLASSES = ["none", "refund", "commitment", "complaint", "legal"] as const;
export const RiskClassSchema = z.enum(RISK_CLASSES);
export type RiskClass = z.infer<typeof RiskClassSchema>;

export const SplitSchema = z.enum(["dev", "heldout"]);
export type Split = z.infer<typeof SplitSchema>;

/** An inbound customer email. The body is untrusted data, never instructions. */
export const EmailInputSchema = z.object({
  id: z.string().min(1),
  from_name: z.string(),
  from_email: z.string(),
  subject: z.string(),
  body: z.string(),
  received_at: z.string(),
});
export type EmailInput = z.infer<typeof EmailInputSchema>;

export const GoldLabelsSchema = z.object({
  intent: IntentSchema,
  risk_class: RiskClassSchema,
  must_mention: z.array(z.string()),
  must_not_say: z.array(z.string()),
});
export type GoldLabels = z.infer<typeof GoldLabelsSchema>;

export const DatasetEmailSchema = EmailInputSchema.extend({
  split: SplitSchema,
  tags: z.array(z.string()),
  gold: GoldLabelsSchema,
});
export type DatasetEmail = z.infer<typeof DatasetEmailSchema>;

/** Screen step output (LLM structured output). */
export const ScreenResultSchema = z.object({
  injection_suspected: z.boolean(),
  abuse: z.boolean(),
  contains_pii: z.boolean(),
  reasons: z.array(z.string()),
});
export type ScreenResult = z.infer<typeof ScreenResultSchema>;

/** Classify step output (LLM structured output). Confidence is clamped to 0..1 after parsing. */
export const ClassificationSchema = z.object({
  intent: IntentSchema,
  confidence: z.number(),
  customer_type: CustomerTypeSchema,
  risk_class: RiskClassSchema,
});
export type Classification = z.infer<typeof ClassificationSchema>;

/** A fact retrieved from the Knowledge Brain, numbered so the drafter can cite it. */
export const FactSchema = z.object({
  id: z.string(),
  source: z.enum(["ask", "search"]),
  title: z.string(),
  url: z.string(),
  text: z.string(),
  image_url: z.string().nullable(),
});
export type Fact = z.infer<typeof FactSchema>;

export const RetrievalSchema = z.object({
  status: z.enum(["ok", "needs_facts"]),
  facts: z.array(FactSchema),
  setting_path: z.string().nullable(),
  warnings: z.array(z.string()),
  gap_ticket_id: z.string().nullable(),
  error: z.string().nullable(),
});
export type Retrieval = z.infer<typeof RetrievalSchema>;

/** Draft step output (LLM structured output). */
export const DraftOutputSchema = z.object({
  reply_text: z.string(),
  fact_citations: z.array(z.object({ fact_id: z.string(), claim: z.string() })),
  open_questions: z.array(z.string()),
  unsure_about: z.string(),
});
export type DraftOutput = z.infer<typeof DraftOutputSchema>;

export const GUARDRAIL_KINDS = ["price", "refund", "discount", "date", "commitment"] as const;
export const GuardrailFlagSchema = z.object({
  kind: z.enum(GUARDRAIL_KINDS),
  span: z.string(),
  start: z.number().int(),
  end: z.number().int(),
  reason: z.string(),
});
export type GuardrailFlag = z.infer<typeof GuardrailFlagSchema>;

export const TrustModeSchema = z.enum(["shadow", "assist", "auto"]);
export type TrustMode = z.infer<typeof TrustModeSchema>;

export const OutcomeSchema = z.enum(["unchanged", "minor_edit", "material_edit", "rewrite"]);
export type Outcome = z.infer<typeof OutcomeSchema>;

export const HumanActionKindSchema = z.enum([
  "send",
  "edit_send",
  "rewrite",
  "reject",
  "auto_send",
  "auto_rewrite",
]);
export type HumanActionKind = z.infer<typeof HumanActionKindSchema>;

/** Judge output for the third "sent unchanged" signal. */
export const JudgeOutputSchema = z.object({
  material_change: z.boolean(),
  reason: z.string(),
});
export type JudgeOutput = z.infer<typeof JudgeOutputSchema>;

/** Reflector output: 0-2 proposed rules plus feedback on rules that were in force. */
export const ReflectorOutputSchema = z.object({
  rules: z.array(
    z.object({
      text: z.string(),
      scope: z.enum(["intent", "global"]),
    }),
  ),
  rule_feedback: z.array(
    z.object({
      rule_id: z.string(),
      verdict: z.enum(["helpful", "harmful"]),
    }),
  ),
});
export type ReflectorOutput = z.infer<typeof ReflectorOutputSchema>;
