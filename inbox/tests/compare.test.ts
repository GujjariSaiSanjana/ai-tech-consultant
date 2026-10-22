import { describe, expect, it } from "vitest";
import { THRESHOLDS } from "../src/config/thresholds";
import { HashingEmbedder } from "../src/embeddings/embedder";
import {
  cosineSimilarity,
  decideOutcome,
  isMaterial,
  levenshtein,
  normalizedEditDistance,
  normalizeForComparison,
  stripSignature,
} from "../src/learning/compare";

describe("levenshtein", () => {
  it.each([
    ["", "", 0],
    ["abc", "", 3],
    ["", "abc", 3],
    ["kitten", "sitting", 3],
    ["flaw", "lawn", 2],
    ["same", "same", 0],
  ])("%s -> %s = %i", (a, b, d) => {
    expect(levenshtein(a, b)).toBe(d);
  });

  it("normalizes by the longer string", () => {
    expect(normalizedEditDistance("kitten", "sitting")).toBeCloseTo(3 / 7);
    expect(normalizedEditDistance("", "")).toBe(0);
    expect(normalizedEditDistance("abc", "xyz")).toBe(1);
  });
});

describe("normalization", () => {
  it("strips a trailing signature and collapses whitespace", () => {
    const a = "Hi Ana,\n\nDo   this.\n\nBest,\nThe Brightpath team";
    const b = "Hi Ana,\nDo this.\n\nThanks,\nJo";
    expect(normalizeForComparison(a)).toBe("Hi Ana, Do this.");
    expect(normalizeForComparison(b)).toBe("Hi Ana, Do this.");
  });

  it("keeps text without a signature", () => {
    expect(stripSignature("Line one\nLine two")).toBe("Line one\nLine two");
  });
});

describe("cosine", () => {
  it("is 1 for identical vectors and 0 for orthogonal ones", () => {
    expect(cosineSimilarity([1, 2, 3], [1, 2, 3])).toBeCloseTo(1);
    expect(cosineSimilarity([1, 0], [0, 1])).toBe(0);
    expect(cosineSimilarity([0, 0], [1, 1])).toBe(0);
  });

  it("rejects mismatched dimensions", () => {
    expect(() => cosineSimilarity([1], [1, 2])).toThrow(RangeError);
  });

  it("hashing embedder is deterministic and separates different replies", async () => {
    const e = new HashingEmbedder(256);
    const [a, b, c] = await e.embed(["Go to Settings and save.", "Go to Settings and save.", "We cannot help with refunds."]);
    expect(a).toEqual(b);
    expect(cosineSimilarity(a ?? [], b ?? [])).toBeCloseTo(1);
    expect(cosineSimilarity(a ?? [], c ?? [])).toBeLessThan(0.5);
  });
});

describe("decideOutcome", () => {
  const clean = { editDistance: 0, cosine: 1, judgeMaterialChange: false };

  it("is unchanged only when all three signals agree", () => {
    expect(decideOutcome("send", clean)).toBe("unchanged");
    expect(decideOutcome("send", { ...clean, editDistance: THRESHOLDS.unchangedMaxEditDistance })).toBe("minor_edit");
    expect(decideOutcome("send", { ...clean, cosine: THRESHOLDS.unchangedMinCosine })).toBe("minor_edit");
    expect(decideOutcome("send", { ...clean, judgeMaterialChange: true })).toBe("material_edit");
    expect(decideOutcome("send", { ...clean, judgeMaterialChange: null })).toBe("minor_edit");
  });

  it("grades by edit size", () => {
    expect(decideOutcome("edit_send", { editDistance: 0.05, cosine: 0.95, judgeMaterialChange: false })).toBe("minor_edit");
    expect(decideOutcome("edit_send", { editDistance: THRESHOLDS.materialMinEditDistance, cosine: 0.9, judgeMaterialChange: false })).toBe(
      "material_edit",
    );
    expect(decideOutcome("edit_send", { editDistance: THRESHOLDS.rewriteMinEditDistance, cosine: 0.2, judgeMaterialChange: true })).toBe("rewrite");
  });

  it("treats explicit rewrites as rewrites regardless of the text", () => {
    expect(decideOutcome("rewrite", clean)).toBe("rewrite");
    expect(decideOutcome("auto_rewrite", clean)).toBe("rewrite");
  });

  it("isMaterial covers material edits and rewrites only", () => {
    expect(isMaterial("unchanged")).toBe(false);
    expect(isMaterial("minor_edit")).toBe(false);
    expect(isMaterial("material_edit")).toBe(true);
    expect(isMaterial("rewrite")).toBe(true);
  });
});
