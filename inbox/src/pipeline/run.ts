import { THRESHOLDS } from "../config/thresholds";
import type { BrainClient } from "../brain/schemas";
import { countsTowardTrust, kindKey, riskAllowsAuto, screenFlagged } from "../domain/kind";
import type { Classification, DraftOutput, EmailInput, GuardrailFlag, Intent, Retrieval, ScreenResult } from "../domain/schemas";
import type { LlmClient } from "../llm/client";
import { classifyEmail } from "./classify";
import { draftReply } from "./draft";
import { checkGuardrails, invalidCitations } from "./guardrail";
import type { ExampleView } from "./prompts";
import { retrieveFacts } from "./retrieve";
import { screenEmail } from "./screen";

export interface PlaybookRule {
  id: string;
  text: string;
  /** "global" or an intent. */
  scope: string;
}

export interface PlaybookContext {
  versionId: number;
  rules: readonly PlaybookRule[];
}

export interface RetrievedExample extends ExampleView {
  id: number;
}

/** Layer A episodic memory: nearest past sent replies of the same intent. */
export interface ExampleMemory {
  nearest(intent: Intent, emailText: string, k: number): Promise<RetrievedExample[]>;
}

export interface PipelineDeps {
  llm: LlmClient;
  brain: BrainClient;
  memory: ExampleMemory;
}

export interface PipelineResult {
  screen: ScreenResult;
  classification: Classification;
  kindKey: string;
  counted: boolean;
  retrieval: Retrieval;
  draft: DraftOutput;
  draftModel: string | null;
  guardrailFlags: GuardrailFlag[];
  needsReview: boolean;
  reviewReasons: string[];
  appliedRuleIds: string[];
  exampleIds: number[];
  error: string | null;
}

export function rulesInScope(rules: readonly PlaybookRule[], intent: Intent): PlaybookRule[] {
  return rules.filter((r) => r.scope === "global" || r.scope === intent);
}

const EMPTY_DRAFT: DraftOutput = { reply_text: "", fact_citations: [], open_questions: [], unsure_about: "" };

/** Runs screen, classify, retrieve, draft and guardrail for one email. Persists nothing. */
export async function runPipeline(email: EmailInput, deps: PipelineDeps, playbook: PlaybookContext): Promise<PipelineResult> {
  const [screen, classification] = await Promise.all([screenEmail(deps.llm, email), classifyEmail(deps.llm, email)]);
  const key = kindKey(classification);
  const counted = countsTowardTrust(classification, screen);
  const reviewReasons: string[] = [];
  if (screenFlagged(screen)) reviewReasons.push(...screen.reasons);
  if (!riskAllowsAuto(classification.risk_class)) {
    reviewReasons.push(`${classification.risk_class} emails always need a person to check the reply.`);
  }
  if (classification.confidence < THRESHOLDS.countedMinConfidence) {
    reviewReasons.push(`Not sure what this email is (confidence ${Math.round(classification.confidence * 100)}%).`);
  }

  if (classification.intent === "spam") {
    const retrieval: Retrieval = { status: "ok", facts: [], setting_path: null, warnings: [], gap_ticket_id: null, error: null };
    return {
      screen,
      classification,
      kindKey: key,
      counted,
      retrieval,
      draft: { ...EMPTY_DRAFT, unsure_about: "Looks like spam. Suggest Reject." },
      draftModel: null,
      guardrailFlags: [],
      needsReview: true,
      reviewReasons: [...reviewReasons, "Looks like spam: no reply drafted."],
      appliedRuleIds: [],
      exampleIds: [],
      error: null,
    };
  }

  const retrieval = await retrieveFacts(deps.brain, email, classification);
  if (retrieval.status === "needs_facts") {
    reviewReasons.push(retrieval.error ? "Knowledge Brain unavailable: needs facts." : "No help-center facts found: needs facts.");
  }

  const rules = rulesInScope(playbook.rules, classification.intent);
  const examples = await deps.memory.nearest(classification.intent, `${email.subject}\n${email.body}`, THRESHOLDS.fewShotK);

  let draft = EMPTY_DRAFT;
  let draftModel: string | null = null;
  let error: string | null = null;
  try {
    const result = await draftReply(deps.llm, {
      email,
      classification,
      facts: retrieval.facts,
      factsNote:
        retrieval.status === "needs_facts"
          ? "No verified facts are available. Do not state how the product works; say the team is checking."
          : null,
      rules: rules.map((r) => ({ id: r.id, text: r.text })),
      examples,
    });
    draft = result.output;
    draftModel = result.model;
  } catch (e) {
    error = e instanceof Error ? e.message : String(e);
    reviewReasons.push(`Draft could not be written: ${error}`);
  }

  const guardrailFlags = checkGuardrails(draft, retrieval.facts);
  if (guardrailFlags.length > 0) reviewReasons.push(`${guardrailFlags.length} unbacked claim(s) flagged.`);
  const badCitations = invalidCitations(draft, retrieval.facts);
  if (badCitations.length > 0) reviewReasons.push(`Cites facts that were not retrieved: ${badCitations.join(", ")}.`);
  if (draft.open_questions.length > 0) reviewReasons.push("Draft has open questions for the team.");

  return {
    screen,
    classification,
    kindKey: key,
    counted,
    retrieval,
    draft,
    draftModel,
    guardrailFlags,
    needsReview: reviewReasons.length > 0,
    reviewReasons,
    appliedRuleIds: rules.map((r) => r.id),
    exampleIds: examples.map((e) => e.id),
    error,
  };
}
