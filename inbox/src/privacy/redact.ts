/**
 * PII redaction applied before anything is stored as a reusable example.
 * Deliberately conservative: it is better to lose a detail from a few-shot
 * example than to replay one customer's data into another customer's reply.
 */
const PATTERNS: ReadonlyArray<{ label: string; pattern: RegExp }> = [
  { label: "[email]", pattern: /[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}/gi },
  { label: "[card]", pattern: /\b(?:\d[ -]?){13,19}\b/g },
  { label: "[id-number]", pattern: /\b\d{3}-\d{2}-\d{4}\b/g },
  { label: "[iban]", pattern: /\b[A-Z]{2}\d{2}(?: ?[A-Z0-9]{4}){3,7}\b/g },
  { label: "[phone]", pattern: /(?:\+\d{1,3}[ .-]?)?\(?\d{3}\)?[ .-]?\d{3}[ .-]?\d{4}\b/g },
  { label: "[secret]", pattern: /\b(password|passcode|pwd|api[ _-]?key|token)\s*[:=]\s*\S+/gi },
];

function escapeRegExp(value: string): string {
  return value.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
}

/** Replaces structured PII and the sender's own name parts. */
export function redactPii(text: string, personNames: readonly string[] = []): string {
  let out = text;
  for (const { label, pattern } of PATTERNS) {
    out = out.replace(pattern, (match, group1: unknown) =>
      label === "[secret]" && typeof group1 === "string" ? `${group1}: [secret]` : label,
    );
  }
  const parts = personNames
    .flatMap((n) => n.split(/\s+/))
    .map((p) => p.replace(/[^\p{L}'-]/gu, ""))
    .filter((p) => p.length >= 2);
  for (const part of new Set(parts)) {
    out = out.replace(new RegExp(`\\b${escapeRegExp(part)}\\b`, "gu"), "[name]");
  }
  return out;
}
