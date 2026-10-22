import { THRESHOLDS } from "../config/thresholds";
import type { DraftRow, ClassificationRow } from "../db/drafts-repo";
import type { EmailRow } from "../db/emails-repo";
import type { VersionRow } from "../db/playbook-repo";
import { kindLabel } from "../domain/kind";
import type { Outcome, TrustMode } from "../domain/schemas";
import type { CuratedRule } from "../learning/curator";
import { autoEligibility, unchangedRateLowerBound } from "../learning/trust";
import type { Services } from "../services/container";

export type InboxFilter = "review" | "auto" | "done" | "all";

export interface InboxItem {
  email: EmailRow;
  draft: DraftRow | null;
  classification: ClassificationRow | null;
  kind: string | null;
}

export function inboxItems(s: Services, filter: InboxFilter): InboxItem[] {
  const items = s.repos.emails.listAll().map((email) => {
    const draft = s.repos.drafts.latestForEmail(email.id);
    const classification = draft ? s.repos.drafts.classification(draft.classification_id) : null;
    return { email, draft, classification, kind: classification ? kindLabel(classification.kind_key) : null };
  });
  const keep: Record<InboxFilter, (i: InboxItem) => boolean> = {
    review: (i) => i.email.status === "new" || i.email.status === "drafted",
    auto: (i) => i.email.status === "auto_sent",
    done: (i) => i.email.status === "sent" || i.email.status === "rejected",
    all: () => true,
  };
  return items.filter(keep[filter]);
}

export interface TrustMeter {
  kindKey: string;
  label: string;
  mode: TrustMode;
  streak: number;
  needed: number;
  countedTotal: number;
  lowerBound: number;
  blockers: string[];
  lastReason: string;
}

export function trustMeters(s: Services): TrustMeter[] {
  return s.repos.trust.all().map((t) => ({
    kindKey: t.kindKey,
    label: kindLabel(t.kindKey),
    mode: t.mode,
    streak: t.streak,
    needed: THRESHOLDS.autoMinStreak,
    countedTotal: t.countedTotal,
    lowerBound: unchangedRateLowerBound(t.outcomes),
    blockers: autoEligibility(t).blockers,
    lastReason: t.lastReason,
  }));
}

export interface LearningPoint {
  index: number;
  at: string;
  editDistance: number;
  unchanged: boolean;
  outcome: Outcome;
  versionId: number;
  rollingUnchanged: number;
  simulated: boolean;
  /** Whether the draft was written with at least one learned playbook rule in force. */
  hadLearnedRules: boolean;
}

const ROLLING = 10;

export function learningSeries(s: Services): LearningPoint[] {
  const rows = s.repos.actions.listOutcomes().filter((r) => r.edit_distance !== null && r.outcome !== null);
  return rows.map((r, index) => {
    const window = rows.slice(Math.max(0, index - ROLLING + 1), index + 1);
    return {
      index,
      at: r.created_at,
      editDistance: Math.min(1, r.edit_distance ?? 0),
      unchanged: r.outcome === "unchanged",
      outcome: r.outcome ?? "rewrite",
      versionId: r.playbook_version_id,
      rollingUnchanged: window.filter((w) => w.outcome === "unchanged").length / window.length,
      simulated: r.simulated,
      hadLearnedRules: (s.repos.drafts.get(r.draft_id)?.applied_rule_ids_json.length ?? 0) > 0,
    };
  });
}

export interface VersionView {
  version: VersionRow;
  rules: CuratedRule[];
  actions: number;
  meanEditDistance: number | null;
  unchangedRate: number | null;
  isActive: boolean;
}

export function versionViews(s: Services, series: readonly LearningPoint[]): VersionView[] {
  const activeId = s.repos.playbook.activeVersionId();
  return s.repos.playbook.versions().map((version) => {
    const points = series.filter((p) => p.versionId === version.id);
    const n = points.length;
    return {
      version,
      rules: s.repos.playbook.rulesFor(version),
      actions: n,
      meanEditDistance: n === 0 ? null : points.reduce((a, p) => a + p.editDistance, 0) / n,
      unchangedRate: n === 0 ? null : points.filter((p) => p.unchanged).length / n,
      isActive: version.id === activeId,
    };
  });
}

export function isSimulatedDemo(s: Services): boolean {
  return s.repos.actions.countSimulated() > 0;
}
