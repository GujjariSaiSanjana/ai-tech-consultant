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

/** True for any Claude model id the API may report, including dated suffixes. */
export function isClaudeModel(reported: string): boolean {
  return MODEL_IDS.some((id) => reported === id || reported.startsWith(`${id}-`));
}

/**
 * Fallback providers reached over their OpenAI-compatible Chat Completions API.
 * Their model names are free-form strings (whatever the provider serves), so
 * they are kept apart from the closed Claude `ModelId` union.
 */
export const FALLBACK_PROVIDERS = ["gemini", "nvidia"] as const;
export type FallbackProvider = (typeof FALLBACK_PROVIDERS)[number];
export type ProviderModel = string;

export function isFallbackProvider(value: string): value is FallbackProvider {
  return (FALLBACK_PROVIDERS as readonly string[]).includes(value);
}

/** These endpoints report no cached tokens, so the cache multipliers are neutral. */
export const FREE_PRICING: ModelPricing = { inputPerMTok: 0, outputPerMTok: 0, cacheReadMultiplier: 1, cacheWriteMultiplier: 1 };

/** Known fallback model prices (USD per MTok). Models not listed are priced by their provider's default. */
export const PROVIDER_MODEL_PRICING: Readonly<Record<ProviderModel, ModelPricing>> = {
  "gemini-2.5-flash": { inputPerMTok: 0.3, outputPerMTok: 2.5, cacheReadMultiplier: 1, cacheWriteMultiplier: 1 },
};

/** build.nvidia.com is used on its free tier, so NVIDIA models cost 0 unless a price is configured. */
export const PROVIDER_DEFAULT_PRICING: Readonly<Record<FallbackProvider, ModelPricing | null>> = {
  gemini: null,
  nvidia: FREE_PRICING,
};

/** Price for a fallback model: an explicit override wins, then the known-model table, then the provider default (null = unpriced). */
export function providerPricing(provider: FallbackProvider, model: ProviderModel, override?: ModelPricing): ModelPricing | null {
  return override ?? PROVIDER_MODEL_PRICING[model] ?? PROVIDER_DEFAULT_PRICING[provider];
}

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
