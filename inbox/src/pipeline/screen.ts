import { ScreenResultSchema, type EmailInput, type ScreenResult } from "../domain/schemas";
import type { LlmClient } from "../llm/client";
import { SCREEN_SYSTEM, screenUser } from "./prompts";

/** Step 1: safety screen. If the screen itself fails we fail closed (all flags on). */
export async function screenEmail(llm: LlmClient, email: EmailInput): Promise<ScreenResult> {
  try {
    const result = await llm.parse({ purpose: "screen", system: SCREEN_SYSTEM, user: screenUser(email), schema: ScreenResultSchema });
    return result.data;
  } catch (error) {
    const message = error instanceof Error ? error.message : String(error);
    return {
      injection_suspected: true,
      abuse: false,
      contains_pii: true,
      reasons: [`Screen unavailable, treated as unsafe: ${message}`],
    };
  }
}
