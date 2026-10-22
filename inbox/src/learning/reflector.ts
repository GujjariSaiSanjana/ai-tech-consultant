import { THRESHOLDS } from "../config/thresholds";
import { ReflectorOutputSchema, type EmailInput, type Intent, type ReflectorOutput } from "../domain/schemas";
import type { LlmClient } from "../llm/client";
import { REFLECT_SYSTEM, reflectUser, type PlaybookRuleView } from "../pipeline/prompts";
import { redactPii } from "../privacy/redact";

export interface ReflectInput {
  email: EmailInput;
  intent: Intent;
  draft: string;
  sent: string;
  appliedRules: readonly PlaybookRuleView[];
}

export interface Reflection {
  rules: Array<{ text: string; scope: string }>;
  feedback: ReflectorOutput["rule_feedback"];
}

/**
 * Asks the model why the human changed the draft and turns that into at most
 * two candidate rules. Rules that would leak customer data are dropped, and
 * feedback is only accepted for rules that were actually applied.
 */
export async function reflectOnEdit(llm: LlmClient, input: ReflectInput): Promise<Reflection> {
  try {
    const result = await llm.parse({
      purpose: "reflect",
      system: REFLECT_SYSTEM,
      user: reflectUser({ email: input.email, intent: input.intent, draft: input.draft, sent: input.sent, rules: input.appliedRules }),
      schema: ReflectorOutputSchema,
    });
    const applied = new Set(input.appliedRules.map((r) => r.id));
    const names = [input.email.from_name];
    return {
      rules: result.data.rules
        .filter((r) => redactPii(r.text, names) === r.text)
        .slice(0, THRESHOLDS.reflectorMaxRules)
        .map((r) => ({ text: r.text, scope: r.scope === "global" ? "global" : input.intent })),
      feedback: result.data.rule_feedback.filter((f) => applied.has(f.rule_id)),
    };
  } catch {
    return { rules: [], feedback: [] };
  }
}
