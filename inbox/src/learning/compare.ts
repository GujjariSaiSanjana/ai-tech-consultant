import { THRESHOLDS } from "../config/thresholds";
import type { HumanActionKind, Outcome } from "../domain/schemas";

const SIGN_OFF = /^(--\s*|best( regards)?,?|kind regards,?|warm regards,?|regards,?|thanks( again)?,?|thank you,?|cheers,?|many thanks,?|sincerely,?)$/i;
const SIGNATURE_SEARCH_LINES = 6;

/** Removes a trailing signature block (sign-off line and everything after it). */
export function stripSignature(text: string): string {
  const lines = text.replace(/\r\n/g, "\n").trimEnd().split("\n");
  const firstCandidate = Math.max(0, lines.length - SIGNATURE_SEARCH_LINES);
  for (let i = lines.length - 1; i >= firstCandidate; i--) {
    const line = lines[i];
    if (line !== undefined && SIGN_OFF.test(line.trim())) return lines.slice(0, i).join("\n");
  }
  return lines.join("\n");
}

/** Whitespace and signature normalization applied before any comparison. */
export function normalizeForComparison(text: string): string {
  return stripSignature(text).replace(/\s+/g, " ").trim();
}

/** Classic Levenshtein distance, two-row dynamic programming. */
export function levenshtein(a: string, b: string): number {
  if (a === b) return 0;
  if (a.length === 0) return b.length;
  if (b.length === 0) return a.length;
  let prev = new Array<number>(b.length + 1);
  let curr = new Array<number>(b.length + 1);
  for (let j = 0; j <= b.length; j++) prev[j] = j;
  for (let i = 1; i <= a.length; i++) {
    curr[0] = i;
    for (let j = 1; j <= b.length; j++) {
      const cost = a.charCodeAt(i - 1) === b.charCodeAt(j - 1) ? 0 : 1;
      curr[j] = Math.min((prev[j] ?? 0) + 1, (curr[j - 1] ?? 0) + 1, (prev[j - 1] ?? 0) + cost);
    }
    [prev, curr] = [curr, prev];
  }
  return prev[b.length] ?? 0;
}

/** Levenshtein divided by the longer length: 0 = identical, 1 = nothing in common. */
export function normalizedEditDistance(a: string, b: string): number {
  const longest = Math.max(a.length, b.length);
  if (longest === 0) return 0;
  return levenshtein(a, b) / longest;
}

export function cosineSimilarity(a: readonly number[], b: readonly number[]): number {
  if (a.length !== b.length) throw new RangeError("vectors must have the same dimension");
  let dot = 0;
  let na = 0;
  let nb = 0;
  for (let i = 0; i < a.length; i++) {
    const x = a[i] ?? 0;
    const y = b[i] ?? 0;
    dot += x * y;
    na += x * x;
    nb += y * y;
  }
  if (na === 0 || nb === 0) return 0;
  return dot / Math.sqrt(na * nb);
}

export interface ComparisonSignals {
  editDistance: number;
  cosine: number;
  /** null when the judge could not run (counts as "not confirmed unchanged"). */
  judgeMaterialChange: boolean | null;
}

/**
 * "Sent unchanged" only when all three signals agree. Anything else is graded
 * by how much changed so the breaker and reflector can react proportionally.
 */
export function decideOutcome(action: HumanActionKind, s: ComparisonSignals): Outcome {
  if (action === "rewrite" || action === "auto_rewrite") return "rewrite";
  const unchanged =
    s.editDistance < THRESHOLDS.unchangedMaxEditDistance &&
    s.cosine > THRESHOLDS.unchangedMinCosine &&
    s.judgeMaterialChange === false;
  if (unchanged) return "unchanged";
  if (s.editDistance >= THRESHOLDS.rewriteMinEditDistance) return "rewrite";
  if (s.judgeMaterialChange === true || s.editDistance >= THRESHOLDS.materialMinEditDistance) return "material_edit";
  return "minor_edit";
}

export function isMaterial(outcome: Outcome): boolean {
  return outcome === "material_edit" || outcome === "rewrite";
}
