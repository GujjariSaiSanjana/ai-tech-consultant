import type { ActionsRepo } from "../db/actions-repo";
import type { DraftRow } from "../db/drafts-repo";
import type { EmailRow } from "../db/emails-repo";
import type { PlaybookRepo } from "../db/playbook-repo";
import type { Intent, Outcome } from "../domain/schemas";
import { curate, type RuleProposal } from "../learning/curator";
import { reflectOnEdit } from "../learning/reflector";
import { scorePlaybook, shouldPromote, type RegressionCase, type RegressionScore } from "../learning/regression";
import type { LlmClient } from "../llm/client";
import type { PipelineDeps, PlaybookContext } from "../pipeline/run";

export interface PlaybookChange {
  proposed: string[];
  addedRuleIds: string[];
  prunedRuleIds: string[];
  newVersionId: number | null;
  promoted: boolean;
  candidateScore: number | null;
  currentScore: number | null;
}

export const NO_PLAYBOOK_CHANGE: PlaybookChange = {
  proposed: [],
  addedRuleIds: [],
  prunedRuleIds: [],
  newVersionId: null,
  promoted: false,
  candidateScore: null,
  currentScore: null,
};

export interface LearningLoopDeps {
  llm: LlmClient;
  playbook: PlaybookRepo;
  actions: ActionsRepo;
  pipelineDeps: PipelineDeps;
  regressionCases: () => RegressionCase[];
  clock: () => string;
}

export function playbookContext(repo: PlaybookRepo, versionId: number): PlaybookContext {
  const version = repo.version(versionId);
  if (!version) throw new Error(`Unknown playbook version ${versionId}`);
  return { versionId, rules: repo.rulesFor(version).map((r) => ({ id: r.id, text: r.text, scope: r.scope })) };
}

/**
 * Layer B: reflect on a material edit, curate a new immutable playbook version,
 * and promote it only if the regression eval does not get worse.
 */
export class LearningLoop {
  constructor(private readonly deps: LearningLoopDeps) {}

  /** Counter updates that do not create a version: unchanged sends reward the rules that were in force. */
  rewardRules(draft: DraftRow, outcome: Outcome): void {
    if (outcome === "unchanged") this.deps.playbook.adjustCounters(draft.applied_rule_ids_json, "helpful");
  }

  async learnFromEdit(input: { email: EmailRow; draft: DraftRow; intent: Intent; sentText: string; humanActionId: number }): Promise<PlaybookChange> {
    const { playbook } = this.deps;
    const active = playbook.requireActiveVersion();
    const currentRules = playbook.rulesFor(active);
    const applied = currentRules.filter((r) => input.draft.applied_rule_ids_json.includes(r.id));

    const reflection = await reflectOnEdit(this.deps.llm, {
      email: input.email,
      intent: input.intent,
      draft: input.draft.reply_text,
      sent: input.sentText,
      appliedRules: applied.map((r) => ({ id: r.id, text: r.text })),
    });
    for (const f of reflection.feedback) playbook.adjustCounters([f.rule_id], f.verdict);

    const refreshed = playbook.rulesFor(active);
    const proposals: RuleProposal[] = reflection.rules.map((r) => ({ ...r, evidenceIds: [input.humanActionId] }));
    const now = this.deps.clock();
    const curated = curate(refreshed, proposals, playbook.nextRuleId(), now);
    playbook.saveRules(curated.rules);
    const base = { ...NO_PLAYBOOK_CHANGE, proposed: reflection.rules.map((r) => r.text) };
    if (!curated.changed) return base;

    const candidateContext: PlaybookContext = {
      versionId: -1,
      rules: curated.rules.map((r) => ({ id: r.id, text: r.text, scope: r.scope })),
    };
    const current = await this.currentScore(active.id);
    const candidate = await scorePlaybook(this.deps.regressionCases(), this.deps.pipelineDeps, candidateContext);
    const promote = shouldPromote(candidate, current);
    const note = [
      curated.added.length > 0 ? `Added ${curated.added.join(", ")}` : "",
      curated.pruned.length > 0 ? `Removed ${curated.pruned.join(", ")}` : "",
      `from an edit (action #${input.humanActionId}).`,
      promote ? "" : `Rejected: quality check ${candidate.meanScore.toFixed(3)} < ${current.meanScore.toFixed(3)}.`,
    ]
      .filter(Boolean)
      .join(" ");
    const versionId = playbook.createVersion({
      parentId: active.id,
      ruleIds: curated.rules.map((r) => r.id),
      status: promote ? "archived" : "rejected",
      note,
      evalScore: candidate.meanScore,
      baselineScore: current.meanScore,
      now,
    });
    if (promote) playbook.activate(versionId);
    return {
      ...base,
      addedRuleIds: curated.added,
      prunedRuleIds: curated.pruned,
      newVersionId: versionId,
      promoted: promote,
      candidateScore: candidate.meanScore,
      currentScore: current.meanScore,
    };
  }

  /** Score of a version on the regression set, computed once and stored on the version. */
  async currentScore(versionId: number): Promise<RegressionScore> {
    const version = this.deps.playbook.version(versionId);
    if (version?.eval_score !== null && version?.eval_score !== undefined) {
      return { meanScore: version.eval_score, passRate: 0, cases: this.deps.regressionCases().length };
    }
    const score = await scorePlaybook(this.deps.regressionCases(), this.deps.pipelineDeps, playbookContext(this.deps.playbook, versionId));
    this.deps.playbook.setVersionScore(versionId, score.meanScore);
    return score;
  }
}
