import { ClassificationSchema, type Classification, type EmailInput } from "../domain/schemas";
import type { LlmClient } from "../llm/client";
import { CLASSIFY_SYSTEM, classifyUser } from "./prompts";

/** Step 2: intent, customer type, risk class. Confidence is clamped; failures fall back to a zero-confidence "other". */
export async function classifyEmail(llm: LlmClient, email: EmailInput): Promise<Classification> {
  try {
    const result = await llm.parse({ purpose: "classify", system: CLASSIFY_SYSTEM, user: classifyUser(email), schema: ClassificationSchema });
    return { ...result.data, confidence: Math.min(1, Math.max(0, result.data.confidence)) };
  } catch {
    return { intent: "other", confidence: 0, customer_type: "existing", risk_class: "none" };
  }
}
