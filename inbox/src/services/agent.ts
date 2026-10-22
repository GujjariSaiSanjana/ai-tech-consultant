import { THRESHOLDS } from "../config/thresholds";
import type { ActionsRepo } from "../db/actions-repo";
import type { DraftsRepo } from "../db/drafts-repo";
import type { EmailsRepo, NewEmail } from "../db/emails-repo";
import type { EvalRunsRepo, SettingsRepo } from "../db/ops-repo";
import type { PlaybookRepo } from "../db/playbook-repo";
import type { TrustRepo } from "../db/trust-repo";
import type { HumanActionKind, Outcome } from "../domain/schemas";
import type { Embedder } from "../embeddings/embedder";
import { cosineSimilarity, decideOutcome, isMaterial, normalizedEditDistance, normalizeForComparison } from "../learning/compare";
import { judgeMaterialChange } from "../learning/judge";
import { isRegression, scorePlaybook, type RegressionCase } from "../learning/regression";
import { applyTrustEvent, decideSend, effectiveMode, initialTrust, type TrustState } from "../learning/trust";
import type { LlmClient } from "../llm/client";
import { runPipeline, type PipelineDeps } from "../pipeline/run";
import { redactPii } from "../privacy/redact";
import { LearningLoop, NO_PLAYBOOK_CHANGE, playbookContext, type PlaybookChange } from "./learning-loop";

export interface Repos {
  emails: EmailsRepo;
  drafts: DraftsRepo;
  actions: ActionsRepo;
  playbook: PlaybookRepo;
  trust: TrustRepo;
  settings: SettingsRepo;
  evalRuns: EvalRunsRepo;
}

export interface AgentDeps {
  repos: Repos;
  llm: LlmClient;
  pipeline: PipelineDeps;
  embedder: Embedder;
  clock: () => string;
}

export type ReviewAction = Exclude<HumanActionKind, "auto_send">;

export interface ActionResult {
  outcome: Outcome | null;
  editDistance: number | null;
  cosine: number | null;
  judgeMaterialChange: boolean | null;
  counted: boolean;
  trustBefore: TrustState | null;
  trustAfter: TrustState | null;
  playbook: PlaybookChange;
}

/** A caller mistake (unknown id, wrong state). Identified by name so it survives duplicated module instances in bundles. */
export class InputError extends Error {
  override readonly name = "InputError";
}

export function isInputError(error: unknown): error is InputError {
  return error instanceof Error && error.name === "InputError";
}

/** Application service: the one place that wires pipeline, learning loop, trust ladder and storage together. */
export class InboxAgent {
  private readonly loop: LearningLoop;

  constructor(private readonly deps: AgentDeps) {
    deps.repos.playbook.ensureInitialVersion(deps.clock());
    this.loop = new LearningLoop({
      llm: deps.llm,
      playbook: deps.repos.playbook,
      actions: deps.repos.actions,
      pipelineDeps: deps.pipeline,
      regressionCases: () => this.regressionCases(),
      clock: deps.clock,
    });
  }

  ingest(email: NewEmail): void {
    this.deps.repos.emails.insert(email);
  }

  /** Dev-split, labelled, non-simulated emails, in a fixed order. Held-out emails never tune anything. */
  regressionCases(limit: number = THRESHOLDS.regressionCases): RegressionCase[] {
    return this.deps.repos.emails
      .listAll()
      .filter((e) => e.split === "dev" && !e.simulated)
      .sort((a, b) => a.id.localeCompare(b.id))
      .flatMap((e) => (e.gold_json ? [{ email: e, gold: e.gold_json, tags: e.tags_json }] : []))
      .slice(0, limit);
  }

  async processEmail(emailId: string): Promise<{ draftId: number; autoSent: boolean }> {
    const { repos, clock } = this.deps;
    const email = repos.emails.get(emailId);
    if (!email) throw new InputError(`Unknown email ${emailId}`);
    const active = repos.playbook.requireActiveVersion();
    const result = await runPipeline(email, this.deps.pipeline, playbookContext(repos.playbook, active.id));
    const trust = repos.trust.get(result.kindKey);
    const decision = decideSend({
      state: trust,
      killSwitch: repos.settings.killSwitch(),
      draftNeedsReview: result.needsReview,
      draftModel: result.draftModel,
    });
    const now = clock();
    const { draftId } = repos.drafts.save({
      emailId,
      result,
      playbookVersionId: active.id,
      trustMode: effectiveMode(trust, result.draftModel),
      sendDecision: decision.reason,
      autoSend: decision.auto,
      now,
    });
    if (decision.auto) {
      repos.actions.insert({
        draftId,
        emailId,
        action: "auto_send",
        sentText: result.draft.reply_text,
        editDistance: null,
        cosine: null,
        judgeMaterialChange: null,
        outcome: null,
        kindKey: result.kindKey,
        counted: false,
        playbookVersionId: active.id,
        simulated: email.simulated,
        now,
      });
    }
    repos.emails.setStatus(emailId, decision.auto ? "auto_sent" : "drafted");
    return { draftId, autoSent: decision.auto };
  }

  async processAllNew(): Promise<number> {
    const pending = this.deps.repos.emails.listByStatus("new");
    for (const email of pending) await this.processEmail(email.id);
    return pending.length;
  }

  /**
   * Records what the human did with a draft and runs the whole learning loop:
   * compare, store example, reward/reflect/curate, update trust.
   */
  async recordAction(input: { draftId: number; action: ReviewAction; text?: string; simulated?: boolean }): Promise<ActionResult> {
    const { repos, clock } = this.deps;
    const draft = repos.drafts.get(input.draftId);
    if (!draft) throw new InputError(`Unknown draft ${input.draftId}`);
    const expected = input.action === "auto_rewrite" ? "auto_sent" : "pending";
    if (draft.status !== expected) throw new InputError(`Draft ${draft.id} is ${draft.status}, cannot ${input.action}`);
    const email = repos.emails.get(draft.email_id);
    const classification = repos.drafts.classification(draft.classification_id);
    if (!email || !classification) throw new InputError("Draft is missing its email or classification");
    const now = clock();
    const simulated = input.simulated ?? false;

    if (input.action === "reject") {
      repos.actions.insert({
        draftId: draft.id,
        emailId: email.id,
        action: "reject",
        sentText: null,
        editDistance: null,
        cosine: null,
        judgeMaterialChange: null,
        outcome: null,
        kindKey: classification.kind_key,
        counted: false,
        playbookVersionId: draft.playbook_version_id,
        simulated,
        now,
      });
      repos.drafts.markSent(draft.id, "rejected", null, now);
      repos.emails.setStatus(email.id, "rejected");
      return { ...emptyResult(), trustBefore: repos.trust.get(classification.kind_key) };
    }

    const sentText = (input.text ?? draft.reply_text).trim();
    if (sentText.length === 0) throw new InputError("Cannot send an empty reply");
    const normDraft = normalizeForComparison(draft.reply_text);
    const normSent = normalizeForComparison(sentText);
    const editDistance = normalizedEditDistance(normDraft, normSent);
    const [vDraft, vSent] = await this.deps.embedder.embed([normDraft, normSent]);
    const cosine = vDraft && vSent ? cosineSimilarity(vDraft, vSent) : 0;
    const isRewrite = input.action === "rewrite" || input.action === "auto_rewrite";
    const judge = isRewrite ? null : await judgeMaterialChange(this.deps.llm, draft.reply_text, sentText);
    const outcome = decideOutcome(input.action, { editDistance, cosine, judgeMaterialChange: judge });

    const humanActionId = repos.actions.insert({
      draftId: draft.id,
      emailId: email.id,
      action: input.action,
      sentText,
      editDistance,
      cosine,
      judgeMaterialChange: judge,
      outcome,
      kindKey: classification.kind_key,
      counted: classification.counted,
      playbookVersionId: draft.playbook_version_id,
      simulated,
      now,
    });
    if (input.action !== "auto_rewrite") repos.drafts.markSent(draft.id, "sent", sentText, now);
    repos.emails.setStatus(email.id, input.action === "auto_rewrite" ? "auto_sent" : "sent");

    if (email.split !== "heldout") {
      const names = [email.from_name];
      const emailText = redactPii(`${email.subject}\n${email.body}`, names);
      const [embedding] = await this.deps.embedder.embed([emailText]);
      if (embedding) {
        repos.actions.insertExample({
          humanActionId,
          emailId: email.id,
          intent: classification.intent,
          kindKey: classification.kind_key,
          emailText,
          replyText: redactPii(sentText, names),
          embedder: this.deps.embedder.id,
          embedding,
          now,
        });
      }
    }

    this.loop.rewardRules(draft, outcome);
    const playbook = isMaterial(outcome)
      ? await this.loop.learnFromEdit({ email, draft, intent: classification.intent, sentText, humanActionId })
      : NO_PLAYBOOK_CHANGE;

    const trustBefore = repos.trust.get(classification.kind_key);
    let trustAfter = trustBefore;
    if (classification.counted) {
      trustAfter = applyTrustEvent(trustBefore ?? initialTrust(classification.kind_key, classification.risk_class), {
        type: "outcome",
        outcome,
        wasAutoSent: input.action === "auto_rewrite",
      });
      repos.trust.save(trustBefore, trustAfter, clock());
    }

    return { outcome, editDistance, cosine, judgeMaterialChange: judge, counted: classification.counted, trustBefore, trustAfter, playbook };
  }

  rollback(versionId: number): void {
    const version = this.deps.repos.playbook.version(versionId);
    if (!version) throw new InputError(`Unknown playbook version ${versionId}`);
    if (version.status === "rejected") throw new InputError("A version that failed its quality check cannot be activated");
    this.deps.repos.playbook.activate(versionId);
  }

  setKillSwitch(on: boolean): void {
    this.deps.repos.settings.setKillSwitch(on);
  }

  /**
   * Re-scores the active playbook. If it now scores below the score it was
   * promoted with, every kind loses auto-send (eval-regression breaker).
   */
  async qualityCheck(runId: string): Promise<{ score: number; recorded: number | null; regressed: boolean }> {
    const { repos, clock } = this.deps;
    const active = repos.playbook.requireActiveVersion();
    const cases = this.regressionCases();
    const score = await scorePlaybook(cases, this.deps.pipeline, playbookContext(repos.playbook, active.id));
    const regressed = isRegression(score.meanScore, active.eval_score);
    if (active.eval_score === null) repos.playbook.setVersionScore(active.id, score.meanScore);
    repos.evalRuns.insert({
      run_id: runId,
      purpose: "quality_check",
      playbook_version_id: active.id,
      split: "dev",
      cases: score.cases,
      mean_score: score.meanScore,
      pass_rate: score.passRate,
      mocked: this.deps.llm.mocked,
      created_at: clock(),
    });
    if (regressed) {
      for (const state of repos.trust.all()) {
        repos.trust.save(state, applyTrustEvent(state, { type: "eval_regression" }), clock());
      }
    }
    return { score: score.meanScore, recorded: active.eval_score, regressed };
  }
}

function emptyResult(): ActionResult {
  return {
    outcome: null,
    editDistance: null,
    cosine: null,
    judgeMaterialChange: null,
    counted: false,
    trustBefore: null,
    trustAfter: null,
    playbook: NO_PLAYBOOK_CHANGE,
  };
}
