import type { EmailInput, GoldLabels } from "../domain/schemas";
import { scoreCase } from "../evals/scoring";
import { runPipeline, type PipelineDeps, type PlaybookContext } from "../pipeline/run";
import { mean } from "./stats";

export interface RegressionCase {
  email: EmailInput;
  gold: GoldLabels;
  tags: string[];
}

export interface RegressionScore {
  meanScore: number;
  passRate: number;
  cases: number;
}

/** Scores one playbook version on the fixed regression set (dev split only). */
export async function scorePlaybook(
  cases: readonly RegressionCase[],
  deps: PipelineDeps,
  playbook: PlaybookContext,
): Promise<RegressionScore> {
  const scores = [];
  for (const c of cases) {
    const result = await runPipeline(c.email, deps, playbook);
    scores.push(scoreCase(c.gold, c.tags, result));
  }
  return {
    meanScore: mean(scores.map((s) => s.score)),
    passRate: mean(scores.map((s) => (s.passed ? 1 : 0))),
    cases: scores.length,
  };
}

/** Promotion gate: a candidate is promoted only if it scores at least as well as the active version. */
export function shouldPromote(candidate: RegressionScore, current: RegressionScore): boolean {
  return candidate.meanScore >= current.meanScore;
}

/** A re-check of the active version that scores below its own promotion score is a regression. */
export function isRegression(recheck: number, recorded: number | null, tolerance = 1e-9): boolean {
  return recorded !== null && recheck < recorded - tolerance;
}
