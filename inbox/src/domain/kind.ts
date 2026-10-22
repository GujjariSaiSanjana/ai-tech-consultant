import { THRESHOLDS } from "../config/thresholds";
import type { Classification, CustomerType, Intent, RiskClass, ScreenResult } from "./schemas";

/** "Same kind of email" = intent + customer type + risk class. */
export function kindKey(c: Pick<Classification, "intent" | "customer_type" | "risk_class">): string {
  return `${c.intent}:${c.customer_type}:${c.risk_class}`;
}

export function parseKindKey(key: string): { intent: Intent; customer_type: CustomerType; risk_class: RiskClass } | null {
  const [intent, customer_type, risk_class] = key.split(":");
  if (!intent || !customer_type || !risk_class) return null;
  return {
    intent: intent as Intent,
    customer_type: customer_type as CustomerType,
    risk_class: risk_class as RiskClass,
  };
}

export function screenFlagged(s: ScreenResult): boolean {
  return s.injection_suspected || s.abuse || s.contains_pii;
}

/** An outcome only moves the trust ladder when classification is confident and the screen is clean. */
export function countsTowardTrust(c: Classification, s: ScreenResult): boolean {
  return c.confidence >= THRESHOLDS.countedMinConfidence && !screenFlagged(s);
}

/** Refund, commitment and legal emails always need a human. */
export const NEVER_AUTO_RISKS: ReadonlySet<RiskClass> = new Set<RiskClass>(["refund", "commitment", "legal"]);

export function riskAllowsAuto(risk: RiskClass): boolean {
  return !NEVER_AUTO_RISKS.has(risk);
}

const INTENT_LABELS: Record<Intent, string> = {
  how_to: "How-to",
  bug_report: "Bug report",
  new_project: "New project",
  quote_request: "Quote request",
  refund: "Refund",
  cancellation: "Cancellation",
  complaint: "Complaint",
  billing: "Billing",
  follow_up: "Follow-up",
  partnership: "Partnership",
  spam: "Spam",
  other: "Other",
};

const CUSTOMER_LABELS: Record<CustomerType, string> = {
  new: "new customer",
  existing: "existing customer",
  agency: "agency",
};

export function intentLabel(intent: Intent): string {
  return INTENT_LABELS[intent];
}

/** Human readable kind, e.g. "How-to, existing customer". Risk is added when not "none". */
export function kindLabel(key: string): string {
  const parsed = parseKindKey(key);
  if (!parsed) return key;
  const base = `${INTENT_LABELS[parsed.intent] ?? parsed.intent}, ${CUSTOMER_LABELS[parsed.customer_type] ?? parsed.customer_type}`;
  return parsed.risk_class === "none" ? base : `${base} (${parsed.risk_class} risk)`;
}
