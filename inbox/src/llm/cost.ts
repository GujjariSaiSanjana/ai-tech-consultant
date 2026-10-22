import { MODEL_IDS, PRICING, PROVIDER_MODEL_PRICING, type ModelId, type ModelPricing } from "./models";

export interface TokenUsage {
  inputTokens: number;
  outputTokens: number;
  cacheReadTokens: number;
  cacheWriteTokens: number;
}

export const ZERO_USAGE: TokenUsage = { inputTokens: 0, outputTokens: 0, cacheReadTokens: 0, cacheWriteTokens: 0 };

/** Resolves a model id reported by the API (which may carry a suffix) to a priced model. */
export function pricedModel(reported: string): ModelId | null {
  return MODEL_IDS.find((id) => reported === id || reported.startsWith(`${id}-`)) ?? null;
}

/** Pricing for a reported model name: Claude ids (with suffixes) first, then known fallback-provider models. */
export function pricingFor(model: string): ModelPricing | null {
  const id = pricedModel(model);
  if (id) return PRICING[id];
  return PROVIDER_MODEL_PRICING[model] ?? null;
}

/** Cost of one call. Pass `pricing` to price a model outside the built-in tables; unpriced models cost 0. */
export function costUsd(model: string, usage: TokenUsage, pricing?: ModelPricing | null): number {
  const p = pricing ?? pricingFor(model);
  if (!p) return 0;
  const perToken = p.inputPerMTok / 1_000_000;
  return (
    usage.inputTokens * perToken +
    usage.cacheReadTokens * perToken * p.cacheReadMultiplier +
    usage.cacheWriteTokens * perToken * p.cacheWriteMultiplier +
    (usage.outputTokens * p.outputPerMTok) / 1_000_000
  );
}

export function addUsage(a: TokenUsage, b: TokenUsage): TokenUsage {
  return {
    inputTokens: a.inputTokens + b.inputTokens,
    outputTokens: a.outputTokens + b.outputTokens,
    cacheReadTokens: a.cacheReadTokens + b.cacheReadTokens,
    cacheWriteTokens: a.cacheWriteTokens + b.cacheWriteTokens,
  };
}
