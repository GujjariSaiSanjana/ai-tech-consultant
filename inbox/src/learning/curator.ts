import { THRESHOLDS } from "../config/thresholds";

export interface CuratedRule {
  id: string;
  text: string;
  /** "global" or an intent. */
  scope: string;
  helpful: number;
  harmful: number;
  evidenceIds: number[];
  createdAt: string;
}

export interface RuleProposal {
  text: string;
  scope: string;
  evidenceIds: number[];
}

export interface CurationResult {
  rules: CuratedRule[];
  added: string[];
  merged: string[];
  pruned: string[];
  /** True when rule membership changed, i.e. a new playbook version is warranted. */
  changed: boolean;
}

const MAX_RULE_CHARS = 240;

/** Lowercased words with a naive plural strip, so "customers ask" matches "customer asks". */
function wordSet(text: string): Set<string> {
  const words = text.toLowerCase().match(/[a-z0-9]+/g) ?? [];
  return new Set(words.map((w) => (w.length > 3 && w.endsWith("s") && !w.endsWith("ss") ? w.slice(0, -1) : w)));
}

export function jaccard(a: string, b: string): number {
  const wa = wordSet(a);
  const wb = wordSet(b);
  if (wa.size === 0 && wb.size === 0) return 1;
  let shared = 0;
  for (const w of wa) if (wb.has(w)) shared++;
  return shared / (wa.size + wb.size - shared);
}

function cleanRuleText(text: string): string {
  return text.replace(/\s+/g, " ").trim().slice(0, MAX_RULE_CHARS);
}

function netScore(r: CuratedRule): number {
  return r.helpful - r.harmful;
}

/**
 * Deterministic curator (no model involved): merges near-duplicate proposals
 * into existing rules, prunes rules that have proven harmful, and caps rules
 * per scope. Pure: inputs are not mutated, ids come from `newId`.
 */
export function curate(
  current: readonly CuratedRule[],
  proposals: readonly RuleProposal[],
  newId: () => string,
  now: string,
): CurationResult {
  const rules = current.map((r) => ({ ...r, evidenceIds: [...r.evidenceIds] }));
  const added: string[] = [];
  const merged: string[] = [];

  for (const proposal of proposals) {
    const text = cleanRuleText(proposal.text);
    if (text.length === 0) continue;
    const duplicate = rules.find(
      (r) => r.scope === proposal.scope && jaccard(r.text, text) >= THRESHOLDS.curatorDuplicateJaccard,
    );
    if (duplicate) {
      duplicate.helpful += 1;
      duplicate.evidenceIds = [...new Set([...duplicate.evidenceIds, ...proposal.evidenceIds])];
      if (!merged.includes(duplicate.id)) merged.push(duplicate.id);
      continue;
    }
    const rule: CuratedRule = {
      id: newId(),
      text,
      scope: proposal.scope,
      helpful: 0,
      harmful: 0,
      evidenceIds: [...proposal.evidenceIds],
      createdAt: now,
    };
    rules.push(rule);
    added.push(rule.id);
  }

  const pruned = rules.filter((r) => r.harmful - r.helpful >= THRESHOLDS.curatorPruneMargin).map((r) => r.id);
  let kept = rules.filter((r) => !pruned.includes(r.id));

  const byScope = new Map<string, CuratedRule[]>();
  for (const r of kept) byScope.set(r.scope, [...(byScope.get(r.scope) ?? []), r]);
  for (const group of byScope.values()) {
    if (group.length <= THRESHOLDS.curatorMaxRulesPerScope) continue;
    const ranked = [...group].sort((a, b) => netScore(b) - netScore(a) || b.createdAt.localeCompare(a.createdAt));
    for (const r of ranked.slice(THRESHOLDS.curatorMaxRulesPerScope)) pruned.push(r.id);
  }
  kept = kept.filter((r) => !pruned.includes(r.id));

  const addedKept = added.filter((id) => !pruned.includes(id));
  const prunedExisting = pruned.filter((id) => current.some((r) => r.id === id));
  return {
    rules: kept,
    added: addedKept,
    merged,
    pruned: prunedExisting,
    changed: addedKept.length > 0 || prunedExisting.length > 0,
  };
}
