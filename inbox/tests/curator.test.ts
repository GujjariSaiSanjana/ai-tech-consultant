import { describe, expect, it } from "vitest";
import { THRESHOLDS } from "../src/config/thresholds";
import { curate, jaccard, type CuratedRule } from "../src/learning/curator";

function ids(): () => string {
  let n = 100;
  return () => `R-${++n}`;
}

const rule = (over: Partial<CuratedRule>): CuratedRule => ({
  id: "R-1",
  text: "Link the invoice page when a customer asks about billing.",
  scope: "billing",
  helpful: 0,
  harmful: 0,
  evidenceIds: [1],
  createdAt: "2026-01-01T00:00:00Z",
  ...over,
});

describe("curate", () => {
  it("adds a new rule with a fresh id and marks the set changed", () => {
    const r = curate([], [{ text: "  Greet the customer   by first name. ", scope: "global", evidenceIds: [7] }], ids(), "now");
    expect(r.changed).toBe(true);
    expect(r.added).toEqual(["R-101"]);
    expect(r.rules[0]).toMatchObject({ id: "R-101", text: "Greet the customer by first name.", scope: "global", evidenceIds: [7] });
  });

  it("merges a near-duplicate in the same scope instead of adding", () => {
    const existing = [rule({})];
    const r = curate(existing, [{ text: "Link the invoice page when customers ask about billing.", scope: "billing", evidenceIds: [2] }], ids(), "now");
    expect(r.changed).toBe(false);
    expect(r.merged).toEqual(["R-1"]);
    expect(r.rules).toHaveLength(1);
    expect(r.rules[0]?.helpful).toBe(1);
    expect(r.rules[0]?.evidenceIds).toEqual([1, 2]);
    expect(existing[0]?.helpful).toBe(0);
  });

  it("does not merge across scopes", () => {
    const r = curate([rule({})], [{ text: rule({}).text, scope: "how_to", evidenceIds: [2] }], ids(), "now");
    expect(r.added).toHaveLength(1);
    expect(r.rules).toHaveLength(2);
  });

  it("prunes rules whose harmful count exceeds helpful by the margin", () => {
    const bad = rule({ id: "R-9", harmful: THRESHOLDS.curatorPruneMargin, helpful: 0 });
    const r = curate([bad, rule({ id: "R-2", text: "Something else entirely here." })], [], ids(), "now");
    expect(r.pruned).toEqual(["R-9"]);
    expect(r.changed).toBe(true);
    expect(r.rules.map((x) => x.id)).toEqual(["R-2"]);
  });

  it("caps rules per scope, dropping the least helpful", () => {
    const many = Array.from({ length: THRESHOLDS.curatorMaxRulesPerScope }, (_, i) =>
      rule({ id: `R-${i}`, text: `distinct rule number ${i} alpha${i} beta${i}`, helpful: i + 1 }),
    );
    const r = curate(many, [{ text: "a brand new unrelated instruction", scope: "billing", evidenceIds: [] }], ids(), "now");
    expect(r.rules.filter((x) => x.scope === "billing")).toHaveLength(THRESHOLDS.curatorMaxRulesPerScope);
    expect(r.added).toEqual([]);
    expect(r.changed).toBe(false);
  });

  it("ignores empty proposals", () => {
    expect(curate([], [{ text: "   ", scope: "global", evidenceIds: [] }], ids(), "now").changed).toBe(false);
  });
});

describe("jaccard", () => {
  it("is 1 for identical word sets and 0 for disjoint ones", () => {
    expect(jaccard("a b c", "c b a")).toBe(1);
    expect(jaccard("a b", "c d")).toBe(0);
  });
});
