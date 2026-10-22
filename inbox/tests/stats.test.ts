import { describe, expect, it } from "vitest";
import { THRESHOLDS } from "../src/config/thresholds";
import { mean, wilsonLowerBound } from "../src/learning/stats";

describe("wilsonLowerBound", () => {
  it("returns 0 without data", () => {
    expect(wilsonLowerBound(0, 0, 1.96)).toBe(0);
  });

  it("matches the closed form for all successes: n / (n + z^2)", () => {
    expect(wilsonLowerBound(20, 20, 1.96)).toBeCloseTo(20 / (20 + 1.96 ** 2), 10);
  });

  it("matches a known value (8/10, z=1.96 -> 0.490)", () => {
    expect(wilsonLowerBound(8, 10, 1.96)).toBeCloseTo(0.4902, 3);
  });

  it("is monotonic in successes", () => {
    const values = Array.from({ length: 31 }, (_, k) => wilsonLowerBound(k, 30, THRESHOLDS.wilsonZ));
    for (let i = 1; i < values.length; i++) expect(values[i]).toBeGreaterThan(values[i - 1] ?? 0);
  });

  it("documents why z=1.645: a full 30-window can reach 0.9, z=1.96 cannot", () => {
    expect(wilsonLowerBound(30, 30, 1.96)).toBeLessThan(THRESHOLDS.autoMinWilsonLowerBound);
    expect(wilsonLowerBound(30, 30, THRESHOLDS.wilsonZ)).toBeGreaterThanOrEqual(THRESHOLDS.autoMinWilsonLowerBound);
    expect(wilsonLowerBound(29, 30, THRESHOLDS.wilsonZ)).toBeLessThan(THRESHOLDS.autoMinWilsonLowerBound);
  });

  it("rejects impossible counts", () => {
    expect(() => wilsonLowerBound(5, 4, 1.96)).toThrow(RangeError);
  });
});

describe("mean", () => {
  it("handles empty input", () => {
    expect(mean([])).toBe(0);
    expect(mean([1, 2, 3])).toBe(2);
  });
});
