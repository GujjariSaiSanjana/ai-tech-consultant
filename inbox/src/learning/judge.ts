import { JudgeOutputSchema } from "../domain/schemas";
import type { LlmClient } from "../llm/client";
import { JUDGE_SYSTEM, judgeUser } from "../pipeline/prompts";
import { normalizeForComparison } from "./compare";

/**
 * Third "sent unchanged" signal. Identical normalized text is trivially not a
 * material change, so no model call is spent on it. Returns null when the judge
 * could not run, which never counts as unchanged.
 */
export async function judgeMaterialChange(llm: LlmClient, draft: string, sent: string): Promise<boolean | null> {
  if (normalizeForComparison(draft) === normalizeForComparison(sent)) return false;
  try {
    const result = await llm.parse({ purpose: "judge", system: JUDGE_SYSTEM, user: judgeUser(draft, sent), schema: JudgeOutputSchema });
    return result.data.material_change;
  } catch {
    return null;
  }
}
