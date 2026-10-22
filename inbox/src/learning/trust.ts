import { THRESHOLDS } from "../config/thresholds";
import { riskAllowsAuto } from "../domain/kind";
import type { Outcome, RiskClass, TrustMode } from "../domain/schemas";
import { isMaterial } from "./compare";
import { wilsonLowerBound } from "./stats";

/** Trust ladder state for one kind_key. Pure data, persisted as one row. */
export interface TrustState {
  kindKey: string;
  riskClass: RiskClass;
  mode: TrustMode;
  /** Consecutive "sent unchanged" outcomes. */
  streak: number;
  /** Last `rollingWindow` counted outcomes, oldest first. */
  outcomes: Outcome[];
  countedTotal: number;
  lastReason: string;
}

export type TrustEvent =
  | { type: "outcome"; outcome: Outcome; wasAutoSent: boolean }
  | { type: "eval_regression" };

export function initialTrust(kindKey: string, riskClass: RiskClass): TrustState {
  return {
    kindKey,
    riskClass,
    mode: "shadow",
    streak: 0,
    outcomes: [],
    countedTotal: 0,
    lastReason: "New kind of email: the agent is watching how you reply.",
  };
}

export function unchangedRateLowerBound(outcomes: readonly Outcome[]): number {
  const window = outcomes.slice(-THRESHOLDS.rollingWindow);
  const unchanged = window.filter((o) => o === "unchanged").length;
  return wilsonLowerBound(unchanged, window.length, THRESHOLDS.wilsonZ);
}

export function materialEditsInBreakerWindow(outcomes: readonly Outcome[]): number {
  return outcomes.slice(-THRESHOLDS.breakerWindow).filter(isMaterial).length;
}

export interface Eligibility {
  eligible: boolean;
  /** Plain-language reasons auto is not (yet) allowed. Empty when eligible. */
  blockers: string[];
}

/** Whether a kind has earned auto-send. Does not look at per-draft flags or the kill switch. */
export function autoEligibility(state: TrustState): Eligibility {
  const blockers: string[] = [];
  if (!riskAllowsAuto(state.riskClass)) {
    blockers.push(`${state.riskClass} emails always need a human, by policy.`);
  }
  if (state.mode === "shadow") {
    blockers.push(`Still learning: ${THRESHOLDS.shadowOutcomes - state.countedTotal} more replies before drafts are trusted at all.`);
  }
  if (state.streak < THRESHOLDS.autoMinStreak) {
    blockers.push(`Needs ${THRESHOLDS.autoMinStreak - state.streak} more drafts sent without changes in a row.`);
  }
  const lb = unchangedRateLowerBound(state.outcomes);
  if (lb < THRESHOLDS.autoMinWilsonLowerBound) {
    blockers.push(
      `Unchanged rate (worst case) is ${Math.round(lb * 100)}%, needs ${Math.round(THRESHOLDS.autoMinWilsonLowerBound * 100)}%.`,
    );
  }
  if (materialEditsInBreakerWindow(state.outcomes) >= THRESHOLDS.breakerMaterialEdits) {
    blockers.push(`Too many big edits recently (${THRESHOLDS.breakerMaterialEdits}+ in the last ${THRESHOLDS.breakerWindow}).`);
  }
  return { eligible: blockers.length === 0, blockers };
}

function demote(state: TrustState, reason: string): TrustState {
  return { ...state, mode: state.mode === "shadow" ? "shadow" : "assist", streak: 0, lastReason: reason };
}

/** Pure state transition. Every mode change carries a human-readable reason. */
export function applyTrustEvent(state: TrustState, event: TrustEvent): TrustState {
  if (event.type === "eval_regression") {
    return demote(state, "Quality check regressed: auto-send paused until drafts earn it again.");
  }

  const outcomes = [...state.outcomes, event.outcome].slice(-THRESHOLDS.rollingWindow);
  const next: TrustState = {
    ...state,
    outcomes,
    countedTotal: state.countedTotal + 1,
    streak: event.outcome === "unchanged" ? state.streak + 1 : 0,
  };

  if (event.wasAutoSent && event.outcome === "rewrite") {
    return demote(next, "A person rewrote an auto-sent reply: back to drafts for review.");
  }
  if (materialEditsInBreakerWindow(outcomes) >= THRESHOLDS.breakerMaterialEdits) {
    const wasAuto = state.mode === "auto";
    return demote(
      next,
      wasAuto
        ? `${THRESHOLDS.breakerMaterialEdits} big edits in the last ${THRESHOLDS.breakerWindow}: auto-send switched off.`
        : `${THRESHOLDS.breakerMaterialEdits} big edits in the last ${THRESHOLDS.breakerWindow}: streak reset.`,
    );
  }

  if (next.mode === "shadow") {
    if (next.countedTotal >= THRESHOLDS.shadowOutcomes) {
      return { ...next, mode: "assist", lastReason: `Watched ${next.countedTotal} replies: drafts are now offered for one-click send.` };
    }
    return { ...next, lastReason: `Watching: ${next.countedTotal} of ${THRESHOLDS.shadowOutcomes} replies seen.` };
  }

  if (next.mode === "assist" && autoEligibility(next).eligible) {
    return { ...next, mode: "auto", lastReason: `${next.streak} drafts in a row sent unchanged: auto-send unlocked.` };
  }

  if (next.mode === "auto") return { ...next, lastReason: state.lastReason };
  return {
    ...next,
    lastReason: event.outcome === "unchanged" ? `${next.streak} sent unchanged in a row.` : "Draft was edited: streak reset.",
  };
}

export interface SendDecisionInput {
  state: TrustState | null;
  killSwitch: boolean;
  /** Draft-level safety: screen flags, guardrail flags, missing facts, low confidence. */
  draftNeedsReview: boolean;
}

/** Final per-draft decision. Auto only when the kind earned it AND this draft is clean AND the kill switch is off. */
export function decideSend(input: SendDecisionInput): { auto: boolean; reason: string } {
  if (!input.state) return { auto: false, reason: "New kind of email." };
  if (input.state.mode !== "auto") return { auto: false, reason: `Kind is in ${input.state.mode} mode.` };
  if (input.killSwitch) return { auto: false, reason: "Auto-send is switched off for everyone (kill switch)." };
  if (!riskAllowsAuto(input.state.riskClass)) return { auto: false, reason: "Risk class never auto-sends." };
  if (input.draftNeedsReview) return { auto: false, reason: "This draft has flags that need a person." };
  return { auto: true, reason: "Kind earned auto-send and this draft has no flags." };
}
