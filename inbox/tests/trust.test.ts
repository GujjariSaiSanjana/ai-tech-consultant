import { describe, expect, it } from "vitest";
import { THRESHOLDS } from "../src/config/thresholds";
import type { Outcome, RiskClass } from "../src/domain/schemas";
import {
  applyTrustEvent,
  autoEligibility,
  decideSend,
  initialTrust,
  materialEditsInBreakerWindow,
  unchangedRateLowerBound,
  type TrustState,
} from "../src/learning/trust";

function feed(state: TrustState, outcomes: readonly Outcome[], wasAutoSent = false): TrustState {
  return outcomes.reduce((s, outcome) => applyTrustEvent(s, { type: "outcome", outcome, wasAutoSent }), state);
}

const unchanged = (n: number): Outcome[] => Array.from({ length: n }, () => "unchanged" as const);

function autoState(risk: RiskClass = "none"): TrustState {
  return feed(initialTrust(`how_to:existing:${risk}`, risk), unchanged(THRESHOLDS.rollingWindow));
}

describe("shadow -> assist", () => {
  it("starts in shadow", () => {
    expect(initialTrust("k", "none").mode).toBe("shadow");
  });

  it("moves to assist after the shadow count, whatever the outcomes", () => {
    const outcomes: Outcome[] = ["material_edit", "unchanged", "minor_edit", "unchanged", "unchanged"];
    const before = feed(initialTrust("k", "none"), outcomes.slice(0, THRESHOLDS.shadowOutcomes - 1));
    expect(before.mode).toBe("shadow");
    const after = feed(before, outcomes.slice(THRESHOLDS.shadowOutcomes - 1));
    expect(after.mode).toBe("assist");
  });
});

describe("assist -> auto", () => {
  it("needs both the streak and the Wilson bound", () => {
    let s = feed(initialTrust("k", "none"), unchanged(THRESHOLDS.autoMinStreak));
    expect(s.streak).toBe(THRESHOLDS.autoMinStreak);
    // Streak met, but 20/20 gives a Wilson bound of ~0.88 < 0.9.
    expect(unchangedRateLowerBound(s.outcomes)).toBeLessThan(THRESHOLDS.autoMinWilsonLowerBound);
    expect(s.mode).toBe("assist");
    s = feed(s, unchanged(THRESHOLDS.rollingWindow - THRESHOLDS.autoMinStreak));
    expect(s.mode).toBe("auto");
  });

  it("finds the exact unlock point from cold", () => {
    let s = initialTrust("k", "none");
    let unlockedAt = -1;
    for (let i = 1; i <= 40 && unlockedAt < 0; i++) {
      s = feed(s, ["unchanged"]);
      if (s.mode === "auto") unlockedAt = i;
    }
    // n/(n+z^2) >= 0.9 first holds at n = 25 for z = 1.645 (and the streak of 20 is already met).
    expect(unlockedAt).toBe(25);
  });

  it("an old edit must leave the 30-window before auto unlocks", () => {
    let s = feed(initialTrust("k", "none"), ["minor_edit", ...unchanged(29)]);
    expect(s.streak).toBe(29);
    expect(s.mode).toBe("assist");
    s = feed(s, ["unchanged"]);
    expect(s.mode).toBe("auto");
  });

  it.each(["refund", "commitment", "legal"] as const)("%s is never auto-eligible", (risk) => {
    const s = autoState(risk);
    expect(s.mode).toBe("assist");
    expect(autoEligibility(s).eligible).toBe(false);
    expect(autoEligibility(s).blockers.join(" ")).toContain("always need a human");
  });

  it.each(["none", "complaint"] as const)("%s can become auto", (risk) => {
    expect(autoState(risk).mode).toBe("auto");
  });
});

describe("circuit breaker", () => {
  it("a human rewrite of an auto-sent reply drops to assist with streak 0", () => {
    const s = applyTrustEvent(autoState(), { type: "outcome", outcome: "rewrite", wasAutoSent: true });
    expect(s.mode).toBe("assist");
    expect(s.streak).toBe(0);
    expect(s.lastReason).toContain("rewrote an auto-sent reply");
  });

  it("two material edits within the last 10 trip it", () => {
    let s = feed(autoState(), ["material_edit"]);
    expect(s.mode).toBe("auto");
    s = feed(s, unchanged(THRESHOLDS.breakerWindow - 2));
    expect(materialEditsInBreakerWindow(s.outcomes)).toBe(1);
    s = feed(s, ["material_edit"]);
    expect(s.mode).toBe("assist");
    expect(s.streak).toBe(0);
  });

  it("two material edits further apart than the window do not trip it", () => {
    let s = feed(autoState(), ["material_edit", ...unchanged(THRESHOLDS.breakerWindow)]);
    s = feed(s, ["material_edit"]);
    expect(s.mode).toBe("auto");
  });

  it("a minor edit in auto resets the streak but keeps auto", () => {
    const s = feed(autoState(), ["minor_edit"]);
    expect(s.mode).toBe("auto");
    expect(s.streak).toBe(0);
  });

  it("an eval regression drops auto to assist and resets the streak", () => {
    const s = applyTrustEvent(autoState(), { type: "eval_regression" });
    expect(s.mode).toBe("assist");
    expect(s.streak).toBe(0);
  });

  it("an eval regression leaves shadow kinds in shadow", () => {
    expect(applyTrustEvent(initialTrust("k", "none"), { type: "eval_regression" }).mode).toBe("shadow");
  });

  it("after a breaker trip, auto needs a full clean window again", () => {
    let s = applyTrustEvent(autoState(), { type: "outcome", outcome: "rewrite", wasAutoSent: true });
    s = feed(s, unchanged(THRESHOLDS.rollingWindow - 1));
    expect(s.mode).toBe("assist");
    s = feed(s, ["unchanged"]);
    expect(s.mode).toBe("auto");
  });

  it("keeps only the rolling window", () => {
    const s = feed(initialTrust("k", "none"), unchanged(THRESHOLDS.rollingWindow + 7));
    expect(s.outcomes).toHaveLength(THRESHOLDS.rollingWindow);
    expect(s.countedTotal).toBe(THRESHOLDS.rollingWindow + 7);
  });
});

describe("decideSend", () => {
  it("auto only when kind is auto, kill switch off, risk allowed and draft clean", () => {
    const state = autoState();
    expect(decideSend({ state, killSwitch: false, draftNeedsReview: false }).auto).toBe(true);
    expect(decideSend({ state, killSwitch: true, draftNeedsReview: false }).auto).toBe(false);
    expect(decideSend({ state, killSwitch: false, draftNeedsReview: true }).auto).toBe(false);
    expect(decideSend({ state: null, killSwitch: false, draftNeedsReview: false }).auto).toBe(false);
    expect(decideSend({ state: { ...state, mode: "assist" }, killSwitch: false, draftNeedsReview: false }).auto).toBe(false);
    expect(decideSend({ state: { ...state, riskClass: "refund" }, killSwitch: false, draftNeedsReview: false }).auto).toBe(false);
  });
});
