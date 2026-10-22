import type { DraftOutput, Fact, GuardrailFlag } from "../domain/schemas";

type Kind = GuardrailFlag["kind"];

const MONTHS = "jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|june?|july?|aug(?:ust)?|sep(?:t(?:ember)?)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?";
const WEEKDAYS = "monday|tuesday|wednesday|thursday|friday|saturday|sunday";

/** Risky claim detectors. Numeric kinds must have their number present in a cited fact. */
const DETECTORS: ReadonlyArray<{ kind: Kind; pattern: RegExp; numeric: boolean; stem: RegExp | null }> = [
  {
    kind: "price",
    pattern: /[$€£]\s?\d[\d,]*(?:\.\d+)?|\b\d[\d,]*(?:\.\d+)?\s?(?:usd|eur|gbp|dollars|euros|pounds)\b/gi,
    numeric: true,
    stem: null,
  },
  { kind: "discount", pattern: /\b\d{1,3}\s?% off\b|\b(?:discount(?:ed)?|coupon|promo code|free month)\b/gi, numeric: false, stem: /discount|coupon|promo|free month|% off/i },
  { kind: "refund", pattern: /\brefund(?:s|ed|ing)?\b|\bmoney back\b|\bcredit (?:your|the) account\b/gi, numeric: false, stem: /refund|money back|credit/i },
  {
    kind: "date",
    pattern: new RegExp(
      `\\b(?:(?:${MONTHS})\\.? \\d{1,2}(?:st|nd|rd|th)?|\\d{1,2}(?:st|nd|rd|th)? (?:of )?(?:${MONTHS})|\\d{1,2}/\\d{1,2}(?:/\\d{2,4})?|\\d{4}-\\d{2}-\\d{2}|(?:by |on |this |next )(?:${WEEKDAYS})|tomorrow|tonight|next week|end of (?:the )?(?:day|week|month)|within \\d+ (?:hours?|days?|weeks?|business days?)|\\d+ (?:hours?|business days?))\\b`,
      "gi",
    ),
    numeric: true,
    stem: null,
  },
  { kind: "commitment", pattern: /\b(?:we will|we'll|i will|i'll|we promise|i promise|guarantee[ds]?|we can commit)\b/gi, numeric: false, stem: /will|promise|guarantee|commit/i },
];

function words(text: string): Set<string> {
  return new Set(text.toLowerCase().match(/[a-z0-9]+/g) ?? []);
}

function overlap(a: string, b: string): number {
  const wa = words(a);
  const wb = words(b);
  if (wa.size === 0 || wb.size === 0) return 0;
  let shared = 0;
  for (const w of wa) if (wb.has(w)) shared++;
  return shared / Math.min(wa.size, wb.size);
}

/** Finds the sentence around a character offset. */
function sentenceAt(text: string, index: number): string {
  const before = text.slice(0, index);
  const start = Math.max(before.lastIndexOf(". "), before.lastIndexOf("\n"), before.lastIndexOf("? "), before.lastIndexOf("! ")) + 1;
  const rest = text.slice(index);
  const endRel = rest.search(/[.!?](?:\s|$)|\n/);
  const end = endRel === -1 ? text.length : index + endRel + 1;
  return text.slice(start, end).trim();
}

const CLAIM_MATCH_MIN = 0.6;

function numbersIn(text: string): string[] {
  return (text.match(/\d[\d,.]*/g) ?? []).map((n) => n.replace(/[,.]+$/, "").replace(/,/g, ""));
}

/**
 * Deterministic guardrail: every price, discount, refund, date or commitment in
 * the reply must sit in a sentence the drafter cited to a real retrieved fact,
 * and that fact must actually contain the number (or the concept). Anything
 * else is flagged and forces human review.
 */
export function checkGuardrails(draft: DraftOutput, facts: readonly Fact[]): GuardrailFlag[] {
  const factsById = new Map(facts.map((f) => [f.id, f]));
  const validCitations = draft.fact_citations
    .map((c) => ({ claim: c.claim, fact: factsById.get(c.fact_id) }))
    .filter((c): c is { claim: string; fact: Fact } => c.fact !== undefined);
  const text = draft.reply_text;
  const flags: GuardrailFlag[] = [];

  for (const detector of DETECTORS) {
    for (const match of text.matchAll(detector.pattern)) {
      const start = match.index;
      const span = match[0];
      const sentence = sentenceAt(text, start);
      const supporting = validCitations.filter((c) => overlap(c.claim, sentence) >= CLAIM_MATCH_MIN);
      const backed = supporting.some(({ fact }) => {
        if (detector.numeric) {
          const factNumbers = new Set(numbersIn(fact.text));
          const spanNumbers = numbersIn(span);
          if (spanNumbers.length > 0) return spanNumbers.every((n) => factNumbers.has(n));
          return fact.text.toLowerCase().includes(span.toLowerCase());
        }
        return detector.stem ? detector.stem.test(fact.text) : false;
      });
      if (!backed) {
        flags.push({
          kind: detector.kind,
          span,
          start,
          end: start + span.length,
          reason:
            supporting.length === 0
              ? `"${span}" is not backed by any cited fact.`
              : `"${span}" is cited, but the cited fact does not state it.`,
        });
      }
    }
  }
  return flags.sort((a, b) => a.start - b.start);
}

/** Citations that point at fact ids the retriever never returned. */
export function invalidCitations(draft: DraftOutput, facts: readonly Fact[]): string[] {
  const ids = new Set(facts.map((f) => f.id));
  return draft.fact_citations.map((c) => c.fact_id).filter((id) => !ids.has(id));
}
