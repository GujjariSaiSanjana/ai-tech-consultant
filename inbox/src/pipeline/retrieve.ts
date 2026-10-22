import { THRESHOLDS } from "../config/thresholds";
import type { BrainClient } from "../brain/schemas";
import type { Classification, EmailInput, Fact, Retrieval } from "../domain/schemas";
import { redactPii } from "../privacy/redact";

const QUERY_BODY_CHARS = 800;
const SEARCH_TEXT_CHARS = 700;

export function buildQuery(email: EmailInput): string {
  const body = redactPii(email.body, [email.from_name]).slice(0, QUERY_BODY_CHARS);
  return `${email.subject}\n${body}`.trim();
}

/**
 * Step 3: facts from the Knowledge Brain (ask + search). If the Brain is down
 * or has nothing, the draft is marked "needs facts" and the drafter is told not
 * to invent anything. Spam is not looked up.
 */
export async function retrieveFacts(brain: BrainClient, email: EmailInput, classification: Classification): Promise<Retrieval> {
  if (classification.intent === "spam") {
    return { status: "ok", facts: [], setting_path: null, warnings: [], gap_ticket_id: null, error: null };
  }
  const query = buildQuery(email);
  const [ask, search] = await Promise.allSettled([
    brain.ask({ question: query, top_k: THRESHOLDS.brainAskTopK }),
    brain.search({ query, top_k: THRESHOLDS.brainSearchTopK }),
  ]);

  const facts: Fact[] = [];
  const seen = new Set<string>();
  const add = (f: Omit<Fact, "id">) => {
    const key = `${f.url}|${f.text.slice(0, 80)}`;
    if (seen.has(key) || f.text.trim() === "") return;
    seen.add(key);
    facts.push({ ...f, id: `F${facts.length + 1}` });
  };

  let settingPath: string | null = null;
  let warnings: string[] = [];
  let gapTicket: string | null = null;
  if (ask.status === "fulfilled" && ask.value.status === "answered") {
    const a = ask.value;
    settingPath = a.setting_path;
    warnings = a.warnings;
    const extras = [a.setting_path ? `Setting path: ${a.setting_path}.` : "", ...a.warnings.map((w) => `Warning: ${w}`)].filter(Boolean);
    add({
      source: "ask",
      title: "Knowledge Brain answer",
      url: a.citations[0]?.url ?? "",
      text: [a.answer, ...extras].join("\n"),
      image_url: null,
    });
    for (const c of a.citations) add({ source: "ask", title: c.title, url: c.url, text: c.quote, image_url: c.image_url });
  } else if (ask.status === "fulfilled") {
    gapTicket = ask.value.gap_ticket_id;
  }
  if (search.status === "fulfilled") {
    for (const r of search.value.results) {
      add({ source: "search", title: r.title, url: r.url, text: r.text.slice(0, SEARCH_TEXT_CHARS), image_url: null });
    }
  }

  const errors = [ask, search]
    .filter((r): r is PromiseRejectedResult => r.status === "rejected")
    .map((r) => (r.reason instanceof Error ? r.reason.message : String(r.reason)));
  const error = errors.length > 0 ? errors.join("; ") : null;
  return {
    status: facts.length > 0 ? "ok" : "needs_facts",
    facts,
    setting_path: settingPath,
    warnings,
    gap_ticket_id: gapTicket,
    error,
  };
}
