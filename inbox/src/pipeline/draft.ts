import { DraftOutputSchema, type DraftOutput } from "../domain/schemas";
import type { LlmClient } from "../llm/client";
import { DRAFT_SYSTEM, draftUser, type DraftPromptInput } from "./prompts";

export interface DraftResult {
  output: DraftOutput;
  model: string;
}

/** Step 4: the reply itself, in the company voice, with fact citations. */
export async function draftReply(llm: LlmClient, input: DraftPromptInput): Promise<DraftResult> {
  const result = await llm.parse({ purpose: "draft", system: DRAFT_SYSTEM, user: draftUser(input), schema: DraftOutputSchema });
  return { output: result.data, model: result.model };
}
