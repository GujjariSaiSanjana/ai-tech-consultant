import { describe, expect, it } from "vitest";
import { costUsd, pricedModel, pricingFor } from "../src/llm/cost";
import { isClaudeModel, providerPricing } from "../src/llm/models";

describe("costUsd", () => {
  it("prices haiku, sonnet and opus per million tokens", () => {
    const usage = { inputTokens: 1_000_000, outputTokens: 1_000_000, cacheReadTokens: 0, cacheWriteTokens: 0 };
    expect(costUsd("claude-haiku-5-5", usage)).toBeCloseTo(0.6);
    expect(costUsd("claude-sonnet-5-5", usage)).toBeCloseTo(12);
    expect(costUsd("claude-opus-5-5", usage)).toBeCloseTo(24);
  });

  it("prices cache reads and writes as multiples of input", () => {
    const usage = { inputTokens: 0, outputTokens: 0, cacheReadTokens: 1_000_000, cacheWriteTokens: 1_000_000 };
    expect(costUsd("claude-sonnet-5-5", usage)).toBeCloseTo(2 * 0.1 + 2 * 1.25);
  });

  it("resolves suffixed model ids and returns 0 for unknown models", () => {
    expect(pricedModel("claude-sonnet-5-5-20261001")).toBe("claude-sonnet-5-5");
    expect(costUsd("other-model", { inputTokens: 10, outputTokens: 10, cacheReadTokens: 0, cacheWriteTokens: 0 })).toBe(0);
  });
});

describe("fallback model pricing", () => {
  const usage = { inputTokens: 1_000_000, outputTokens: 1_000_000, cacheReadTokens: 0, cacheWriteTokens: 0 };

  it("prices gemini-2.5-flash at 0.30/2.50 per MTok by name", () => {
    expect(pricingFor("gemini-2.5-flash")).not.toBeNull();
    expect(costUsd("gemini-2.5-flash", usage)).toBeCloseTo(2.8);
  });

  it("prices NVIDIA models at 0 by default, and a configured price wins", () => {
    const model = "meta/llama-3.3-70b-instruct";
    expect(costUsd(model, usage, providerPricing("nvidia", model))).toBe(0);
    const custom = { inputPerMTok: 1, outputPerMTok: 2, cacheReadMultiplier: 1, cacheWriteMultiplier: 1 };
    expect(costUsd(model, usage, providerPricing("nvidia", model, custom))).toBeCloseTo(3);
    expect(providerPricing("gemini", "gemini-unknown")).toBeNull();
  });

  it("tells Claude models apart from fallback models", () => {
    expect(isClaudeModel("claude-sonnet-5-5")).toBe(true);
    expect(isClaudeModel("claude-sonnet-5-5-20261001")).toBe(true);
    expect(isClaudeModel("gemini-2.5-flash")).toBe(false);
    expect(isClaudeModel("meta/llama-3.3-70b-instruct")).toBe(false);
  });
});
