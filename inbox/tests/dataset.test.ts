import { describe, expect, it } from "vitest";
import { INTENTS } from "../src/domain/schemas";
import { loadDataset } from "../src/evals/dataset";

describe("data/emails.jsonl", () => {
  const data = loadDataset();

  it("has at least 60 labelled emails with unique ids", () => {
    expect(data.length).toBeGreaterThanOrEqual(60);
    expect(new Set(data.map((e) => e.id)).size).toBe(data.length);
  });

  it("holds out exactly 15 emails", () => {
    expect(data.filter((e) => e.split === "heldout")).toHaveLength(15);
  });

  it("covers every intent and the adversarial categories", () => {
    const intents = new Set(data.map((e) => e.gold.intent));
    for (const intent of INTENTS) expect(intents.has(intent)).toBe(true);
    const tags = new Set(data.flatMap((e) => e.tags));
    for (const tag of ["injection", "pii", "non_english", "ambiguous", "multi_issue", "angry", "spam"]) expect(tags.has(tag)).toBe(true);
  });
});
