/**
 * Every tunable number the learning loop and trust ladder depend on lives here,
 * so a reviewer can audit the whole safety envelope in one file.
 */
export const THRESHOLDS = {
  /** "Sent unchanged" check 1: normalized Levenshtein distance must be below this. */
  unchangedMaxEditDistance: 0.02,
  /** "Sent unchanged" check 2: embedding cosine similarity must be above this. */
  unchangedMinCosine: 0.97,
  /** Edits at or above this distance are material even if the judge says otherwise. */
  materialMinEditDistance: 0.15,
  /** Edits at or above this distance are treated as a rewrite. */
  rewriteMinEditDistance: 0.6,

  /** A draft only counts toward its kind's trust when the classifier is this sure. */
  countedMinConfidence: 0.85,

  /** First N counted outcomes of a kind are shadow mode. */
  shadowOutcomes: 5,
  /** Consecutive unchanged sends needed before auto is considered. */
  autoMinStreak: 20,
  /** Rolling window of counted outcomes used for the unchanged rate. */
  rollingWindow: 30,
  /** Wilson lower bound of the rolling unchanged rate must reach this. */
  autoMinWilsonLowerBound: 0.9,
  /**
   * z for the Wilson bound. 1.645 is a one-sided 95% bound. With z = 1.96 a
   * 30-outcome window can never exceed 30 / (30 + 3.84) = 0.886, so a 0.9 gate
   * would be unreachable.
   */
  wilsonZ: 1.645,
  /** Circuit breaker: this many material edits inside the last N outcomes trips it. */
  breakerMaterialEdits: 2,
  breakerWindow: 10,

  /** Episodic memory: few-shot examples retrieved per draft. */
  fewShotK: 3,
  /** Reflector may propose at most this many rules per edit. */
  reflectorMaxRules: 2,
  /** Curator: rules with token Jaccard similarity at or above this are duplicates. */
  curatorDuplicateJaccard: 0.6,
  /** Curator: a rule is dropped once harmful exceeds helpful by this margin. */
  curatorPruneMargin: 2,
  /** Curator: maximum rules kept per scope (oldest least helpful dropped first). */
  curatorMaxRulesPerScope: 8,

  /** Promotion gate: number of dev cases in the regression eval. */
  regressionCases: 8,

  /** Brain retrieval sizes. */
  brainAskTopK: 6,
  brainSearchTopK: 4,
  brainTimeoutMs: 8000,
} as const;

export type Thresholds = typeof THRESHOLDS;
