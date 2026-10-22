import { describe, expect, it } from "vitest";
import { costUsd, pricedModel } from "../src/llm/cost";

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
