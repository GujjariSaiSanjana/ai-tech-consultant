import { describe, expect, it } from "vitest";
import type { DraftOutput, Fact } from "../src/domain/schemas";
import { checkGuardrails, invalidCitations } from "../src/pipeline/guardrail";

const fact = (id: string, text: string): Fact => ({ id, source: "ask", title: "t", url: "https://x.example", text, image_url: null });
const draft = (reply: string, citations: DraftOutput["fact_citations"] = []): DraftOutput => ({
  reply_text: reply,
  fact_citations: citations,
  open_questions: [],
  unsure_about: "",
});

describe("checkGuardrails", () => {
  it("passes a reply with no risky claims", () => {
    expect(checkGuardrails(draft("Go to Settings > SEO Settings and save."), [])).toEqual([]);
  });

  it("flags an unbacked price", () => {
    const flags = checkGuardrails(draft("The upgrade is $49 per month."), []);
    expect(flags).toHaveLength(1);
    expect(flags[0]).toMatchObject({ kind: "price", span: "$49" });
  });

  it("accepts a price stated by a cited fact", () => {
    const facts = [fact("F1", "The Basic plan costs $49 per month.")];
    const reply = "The Basic plan costs $49 per month.";
    expect(checkGuardrails(draft(reply, [{ fact_id: "F1", claim: reply }]), facts)).toEqual([]);
  });

  it("flags a price when the cited fact states a different number", () => {
    const facts = [fact("F1", "The Basic plan costs $29 per month.")];
    const reply = "The Basic plan costs $49 per month.";
    const flags = checkGuardrails(draft(reply, [{ fact_id: "F1", claim: reply }]), facts);
    expect(flags[0]?.reason).toContain("does not state it");
  });

  it("flags commitments, refunds, discounts and dates", () => {
    const reply = "We will refund you by Friday and add a 20% off coupon. We'll fix it tomorrow.";
    const kinds = checkGuardrails(draft(reply), []).map((f) => f.kind);
    expect(kinds).toEqual(expect.arrayContaining(["commitment", "refund", "date", "discount"]));
    expect(kinds.filter((k) => k === "commitment")).toHaveLength(2);
  });

  it("accepts a duration backed by a cited fact", () => {
    const facts = [fact("F1", "SSL is issued automatically once DNS has propagated, which can take up to 48 hours.")];
    const reply = "SSL is issued once DNS has propagated, which can take up to 48 hours.";
    expect(checkGuardrails(draft(reply, [{ fact_id: "F1", claim: reply }]), facts)).toEqual([]);
  });

  it("does not trust citations to facts that were never retrieved", () => {
    const reply = "The plan costs $49.";
    const d = draft(reply, [{ fact_id: "F9", claim: reply }]);
    expect(checkGuardrails(d, [])).toHaveLength(1);
    expect(invalidCitations(d, [])).toEqual(["F9"]);
  });

  it("returns spans with correct offsets", () => {
    const reply = "Hi. It costs $10.";
    const [flag] = checkGuardrails(draft(reply), []);
    expect(reply.slice(flag?.start, flag?.end)).toBe("$10");
  });
});
