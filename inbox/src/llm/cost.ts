import { MODEL_IDS, PRICING, type ModelId } from "./models";

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

export function costUsd(model: string, usage: TokenUsage): number {
  const id = pricedModel(model);
  if (!id) return 0;
  const p = PRICING[id];
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
