export const MODEL_IDS = ["claude-haiku-5-5", "claude-sonnet-5-5", "claude-opus-5-5"] as const;
export type ModelId = (typeof MODEL_IDS)[number];

export function isModelId(value: string): value is ModelId {
  return (MODEL_IDS as readonly string[]).includes(value);
}

/** USD per million tokens. Cache reads/writes are priced as multiples of the input rate. */
export interface ModelPricing {
  inputPerMTok: number;
  outputPerMTok: number;
  cacheReadMultiplier: number;
  cacheWriteMultiplier: number;
}

export const PRICING: Record<ModelId, ModelPricing> = {
  "claude-haiku-5-5": { inputPerMTok: 0.1, outputPerMTok: 0.5, cacheReadMultiplier: 0.1, cacheWriteMultiplier: 1.25 },
  "claude-sonnet-5-5": { inputPerMTok: 2, outputPerMTok: 10, cacheReadMultiplier: 0.1, cacheWriteMultiplier: 1.25 },
  "claude-opus-5-5": { inputPerMTok: 4, outputPerMTok: 20, cacheReadMultiplier: 0.05, cacheWriteMultiplier: 1.25 },
};

export const LLM_PURPOSES = ["screen", "classify", "draft", "judge", "reflect"] as const;
export type LlmPurpose = (typeof LLM_PURPOSES)[number];

export type Effort = "low" | "medium" | "high";

export interface Route {
  model: ModelId;
  maxTokens: number;
  effort: Effort;
}

export type RoutingTable = Record<LlmPurpose, Route>;

/**
 * Cheap, fast Haiku for the yes/no and labelling steps; Sonnet where writing
 * quality and reasoning about edits matter. No sampling parameters are sent:
 * they are rejected on these models.
 */
export const DEFAULT_ROUTES: RoutingTable = {
  screen: { model: "claude-haiku-5-5", maxTokens: 2000, effort: "low" },
  classify: { model: "claude-haiku-5-5", maxTokens: 2000, effort: "low" },
  judge: { model: "claude-haiku-5-5", maxTokens: 2000, effort: "low" },
  draft: { model: "claude-sonnet-5-5", maxTokens: 8000, effort: "medium" },
  reflect: { model: "claude-sonnet-5-5", maxTokens: 6000, effort: "medium" },
};

export function withDraftModel(routes: RoutingTable, model: ModelId): RoutingTable {
  return { ...routes, draft: { ...routes.draft, model } };
}
