import { COMPANY } from "../config/company";
import type {
  Classification,
  CustomerType,
  DraftOutput,
  Intent,
  JudgeOutput,
  ReflectorOutput,
  RiskClass,
  ScreenResult,
} from "../domain/schemas";
import { normalizeForComparison, normalizedEditDistance } from "../learning/compare";
import { readDataBlock } from "../pipeline/prompts";
import type { FakeHandlers } from "./fake-client";

/**
 * Deterministic keyword heuristics that stand in for the model offline. They
 * exist so tests, the demo seed and `npm run eval -- --mock` run without an API
 * key. They are intentionally simple and are never presented as model quality.
 */

interface ParsedEmail {
  fromName: string;
  subject: string;
  body: string;
}

function parseEmailBlock(user: string): ParsedEmail {
  const block = readDataBlock(user, "email_data") ?? "";
  const fromName = /^From: (.*?) </m.exec(block)?.[1] ?? "";
  const subject = /^Subject: (.*)$/m.exec(block)?.[1] ?? "";
  const bodyStart = block.indexOf("\n\n");
  return { fromName, subject, body: bodyStart >= 0 ? block.slice(bodyStart + 2) : block };
}

const INJECTION =
  /ignore (all |any |the )?(previous|prior|above|earlier) (instructions|rules)|disregard (your|all|the) (instructions|rules)|system prompt|you are now|developer mode|reveal (your|the) (prompt|instructions|api key)|<!--|​|as an ai (assistant|model),? you must|new instructions:|assistant:\s|forward (all|every|the) (emails|customer)/i;
const ABUSE = /\b(idiot|idiots|moron|scam artists|f[*u]ck|shit|i will find you|you'll regret)\b/i;
const PII = /\b(?:\d[ -]?){13,19}\b|\b\d{3}-\d{2}-\d{4}\b|\b(password|passcode)\s*[:=]?\s*\S+|\bdate of birth\b|\bdob\b|\biban\b|\bsort code\b/i;

export function heuristicScreen(user: string): ScreenResult {
  const { subject, body } = parseEmailBlock(user);
  const text = `${subject}\n${body}`;
  const reasons: string[] = [];
  const injection_suspected = INJECTION.test(text);
  if (injection_suspected) reasons.push("Contains text that tries to instruct the assistant.");
  const abuse = ABUSE.test(text);
  if (abuse) reasons.push("Contains abusive language.");
  const contains_pii = PII.test(text);
  if (contains_pii) reasons.push("Contains sensitive personal data (card, password or ID).");
  return { injection_suspected, abuse, contains_pii, reasons };
}

const INTENT_RULES: ReadonlyArray<{ intent: Intent; pattern: RegExp }> = [
  { intent: "spam", pattern: /\b(unsubscribe|guaranteed (first page|ranking)|backlinks|crypto|bitcoin|casino|seo services for your website|dear (sir|madam|website owner))\b/i },
  { intent: "refund", pattern: /\b(refund|money back|chargeback|reimburse)\b/i },
  { intent: "cancellation", pattern: /\b(cancel|cancellation|terminate (our|my) (plan|contract|retainer))\b/i },
  { intent: "complaint", pattern: /\b(unacceptable|disappointed|terrible|worst|furious|appalling|unprofessional|fed up)\b/i },
  { intent: "partnership", pattern: /\b(partner(ship)?|white[- ]label|affiliate|collaborat\w*|referral agreement)\b/i },
  { intent: "quote_request", pattern: /\b(quote|estimate|how much (would|will|does) it cost|pricing for|ballpark)\b/i },
  { intent: "new_project", pattern: /\b(new (website|site|directory|project)|build (us|me) a|launch a (new )?directory|starting a directory)\b/i },
  { intent: "billing", pattern: /\b(invoice|charged|billing|receipt|payment failed|card declined|double charged|overcharged)\b/i },
  { intent: "follow_up", pattern: /\b(following up|follow up on|any update|checking in on|still waiting|haven't heard back)\b/i },
  { intent: "bug_report", pattern: /\b(not working|broken|error|bug|doesn't show|isn't showing|not showing|stopped working|missing from|blank page|not public)\b/i },
  { intent: "how_to", pattern: /\b(how (do|can|would) (i|we)|where (do|can) (i|we)|is there a way|what's the best way|can you (show|tell) me how)\b/i },
];

const LEGAL = /\b(lawyer|attorney|legal action|sue\b|lawsuit|solicitor|gdpr|data protection|small claims)\b/i;
const COMMITMENT = /\b(deadline|by (monday|tuesday|wednesday|thursday|friday|next week|end of)|guarantee|can you commit|promise)\b/i;

export function heuristicClassify(user: string): Classification {
  const { subject, body } = parseEmailBlock(user);
  const text = `${subject}\n${body}`;
  const matches = INTENT_RULES.filter((r) => r.pattern.test(text)).map((r) => r.intent);
  const intent: Intent = matches[0] ?? "other";
  const distinct = new Set(matches).size;
  const confidence = intent === "other" ? 0.5 : distinct === 1 ? 0.92 : distinct === 2 ? 0.8 : 0.6;

  const customer_type: CustomerType = /\b(our clients?|for a client|agency|my clients?)\b/i.test(text)
    ? "agency"
    : /\b(considering|thinking about|interested in|looking for an agency|came across|found you|not a client yet)\b/i.test(text)
      ? "new"
      : "existing";

  const risk_class: RiskClass = LEGAL.test(text)
    ? "legal"
    : intent === "refund"
      ? "refund"
      : intent === "complaint"
        ? "complaint"
        : intent === "quote_request" || intent === "new_project" || COMMITMENT.test(text)
          ? "commitment"
          : "none";

  return { intent, confidence, customer_type, risk_class };
}

const OPENINGS: Record<Intent, string> = {
  how_to: "Thanks for asking. Here is how to do that.",
  bug_report: "Sorry this isn't working as expected. Here is what usually fixes it.",
  new_project: "Thanks for thinking of us for this project. A teammate is reviewing the details and will confirm scope and pricing with you.",
  quote_request: "Thanks for the details. A teammate is reviewing them and will confirm scope and pricing with you.",
  refund: "Thanks for letting us know. Refund requests are handled personally by your account manager, who has your message now.",
  cancellation: "Sorry to see you go. A teammate is checking your plan and will confirm the next steps for the cancellation.",
  complaint: "I'm sorry about the experience you've had, and thank you for telling us directly.",
  billing: "Thanks for checking on this.",
  follow_up: "Thanks for following up, and sorry for the wait.",
  partnership: "Thanks for reaching out about working together.",
  spam: "",
  other: "Thanks for getting in touch.",
};

interface ParsedFact {
  id: string;
  text: string;
}

function parseFacts(user: string): ParsedFact[] {
  const block = readDataBlock(user, "facts") ?? "";
  const facts: ParsedFact[] = [];
  for (const entry of block.split(/\n\n(?=\[F\d+\])/)) {
    const m = /^\[(F\d+)\][^\n]*\n([\s\S]*)$/.exec(entry.trim());
    if (m?.[1] && m[2]) facts.push({ id: m[1], text: m[2].trim() });
  }
  return facts;
}

function parseRules(user: string): string[] {
  const block = readDataBlock(user, "playbook") ?? "";
  return block
    .split("\n")
    .map((l) => /^[A-Z]+-\d+: (.*)$/.exec(l.trim())?.[1])
    .filter((t): t is string => typeof t === "string");
}

function sentences(text: string): string[] {
  return text
    .split(/(?<=[.!?])\s+|\n+/)
    .map((s) => s.trim())
    .filter((s) => s.length > 0);
}

const INCLUDE_RULE = /^Include a line like: "(.+)"$/;
const AVOID_RULE = /^Avoid saying: "(.+)"$/;

export function heuristicDraft(user: string): DraftOutput {
  const email = parseEmailBlock(user);
  const intent = (/intent=(\w+)/.exec(readDataBlock(user, "classification") ?? "")?.[1] ?? "other") as Intent;
  const facts = parseFacts(user);
  const rules = parseRules(user);
  const firstName = email.fromName.split(/\s+/)[0] || "there";

  const lines: string[] = [OPENINGS[intent] ?? OPENINGS.other];
  const citations: DraftOutput["fact_citations"] = [];
  const primary = facts[0];
  let unsure = "";
  const openQuestions: string[] = [];
  if (primary) {
    const claim = sentences(primary.text).slice(0, 2).join(" ");
    lines.push(claim);
    citations.push({ fact_id: primary.id, claim });
  } else {
    lines.push("I'm checking this with the team so you get an accurate answer, not a guess.");
    unsure = "No help-center facts covered this question.";
    openQuestions.push("Confirm the correct answer with the team before sending.");
  }
  for (const rule of rules) {
    const include = INCLUDE_RULE.exec(rule)?.[1];
    if (include && !lines.includes(include)) lines.push(include);
  }
  lines.push("Let me know if anything is unclear.");
  const avoid = rules.map((r) => AVOID_RULE.exec(r)?.[1]).filter((t): t is string => typeof t === "string");
  const body = lines.filter((l) => !avoid.some((a) => l.includes(a)));

  return {
    reply_text: `Hi ${firstName},\n\n${body.join("\n\n")}\n\n${COMPANY.signOff}`,
    fact_citations: citations,
    open_questions: openQuestions,
    unsure_about: unsure,
  };
}

export function heuristicJudge(user: string): JudgeOutput {
  const draft = normalizeForComparison(readDataBlock(user, "draft_reply") ?? "");
  const sent = normalizeForComparison(readDataBlock(user, "sent_reply") ?? "");
  const distance = normalizedEditDistance(draft, sent);
  return distance > 0.03
    ? { material_change: true, reason: `Wording changed by ${Math.round(distance * 100)}%.` }
    : { material_change: false, reason: "Only cosmetic differences." };
}

export function heuristicReflect(user: string, maxRules: number): ReflectorOutput {
  const draft = sentences(readDataBlock(user, "draft_reply") ?? "");
  const sent = sentences(readDataBlock(user, "sent_reply") ?? "");
  const draftSet = new Set(draft);
  const sentSet = new Set(sent);
  const wordy = (s: string) => s.split(/\s+/).length >= 5 && !/^Hi\b/.test(s) && !s.includes("Brightpath");
  const added = sent.filter((s) => !draftSet.has(s) && wordy(s)).map((s) => ({ text: `Include a line like: "${s}"`, scope: "intent" as const }));
  const removed = draft.filter((s) => !sentSet.has(s) && wordy(s)).map((s) => ({ text: `Avoid saying: "${s}"`, scope: "intent" as const }));
  return { rules: [...added, ...removed].slice(0, maxRules), rule_feedback: [] };
}

export function heuristicHandlers(maxReflectorRules: number): FakeHandlers {
  return {
    screen: (r) => heuristicScreen(r.user),
    classify: (r) => heuristicClassify(r.user),
    draft: (r) => heuristicDraft(r.user),
    judge: (r) => heuristicJudge(r.user),
    reflect: (r) => heuristicReflect(r.user, maxReflectorRules),
  };
}
