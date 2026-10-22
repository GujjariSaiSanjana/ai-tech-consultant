import { COMPANY } from "../config/company";
import type { NewEmail } from "../db/emails-repo";
import type { Intent } from "../domain/schemas";

/**
 * Scripted reviewer used only by `npm run seed` to create a visible learning
 * history. Everything it produces is stored with simulated = 1 and labelled
 * as simulated in the UI.
 */
export const REVIEWER_PREFERENCES: Partial<Record<Intent, string>> = {
  how_to: "If the screen looks different on your side, reply with a screenshot and we can walk you through it.",
  bug_report: "If it still happens after that, send us the page address and the time you saw it so we can trace it.",
  billing: "You can see every invoice and payment we have on file in your client portal under Billing.",
  follow_up: "Thanks for your patience while we get this sorted.",
  complaint: "Our team lead has your message and is reviewing what went wrong.",
  cancellation: "Your site and data stay exactly as they are; only the service plan stops.",
};

const REMOVED_BY_REVIEWER: Partial<Record<Intent, string>> = {
  complaint: "Let me know if anything is unclear.",
};

/** Applies the reviewer's habits to a draft: add the preferred line before the sign-off, drop disliked lines. */
export function simulatedReviewerEdit(draft: string, intent: Intent): string {
  const preferred = REVIEWER_PREFERENCES[intent];
  const removed = REMOVED_BY_REVIEWER[intent];
  let text = draft;
  if (removed) text = text.replace(`\n\n${removed}`, "");
  if (preferred && !text.includes(preferred)) {
    const signOffAt = text.lastIndexOf(COMPANY.signOff);
    text = signOffAt >= 0 ? `${text.slice(0, signOffAt).trimEnd()}\n\n${preferred}\n\n${text.slice(signOffAt)}` : `${text}\n\n${preferred}`;
  }
  return text;
}

const FIRST = ["Ava", "Noah", "Mia", "Liam", "Zoe", "Ethan", "Lena", "Omar", "Ivy", "Raj", "Tess", "Hugo", "Nora", "Felix", "Iris", "Leo", "Ruth", "Sami", "Kai", "Mara"];
const LAST = ["Hart", "Quinn", "Reyes", "Novak", "Ito", "Berg", "Moss", "Silva", "Kerr", "Lund"];
const TOPICS = [
  "connect Stripe so members can pay by card",
  "import our members from a spreadsheet",
  "switch the site to a different design template",
  "set meta titles for our directory pages",
  "edit the welcome email template",
  "add a new membership plan",
  "point our own domain at the site",
  "find the add-ons for an events calendar",
  "customize the featured members widget",
  "rebuild the search index",
];
const PHRASES = ["How do I {t}?", "How can I {t}?", "Where do I go to {t}?", "Is there a way to {t}?"];
const BUSINESSES = ["tutors", "plumbers", "florists", "coaches", "realtors", "vets", "bakers", "guides"];

/** Deterministic synthetic how-to emails from existing customers, enough to walk one kind up the trust ladder. */
export function simulatedHowToEmails(count: number, startIso: string, stepHours: number): NewEmail[] {
  const start = Date.parse(startIso);
  return Array.from({ length: count }, (_, i) => {
    const first = FIRST[i % FIRST.length] ?? "Sam";
    const last = LAST[(i * 7) % LAST.length] ?? "Lee";
    const business = BUSINESSES[i % BUSINESSES.length] ?? "pros";
    const topic = TOPICS[i % TOPICS.length] ?? TOPICS[0] ?? "";
    const phrase = (PHRASES[(i >> 1) % PHRASES.length] ?? "How do I {t}?").replace("{t}", topic);
    return {
      id: `sim-${String(i + 1).padStart(3, "0")}`,
      from_name: `${first} ${last}`,
      from_email: `${first.toLowerCase()}@${business}-${i + 1}.example`,
      subject: phrase.replace(/\?$/, ""),
      body: `Hi Brightpath,\n\n${phrase} We look after our ${business} directory ourselves and want to get this right.\n\nThanks,\n${first}`,
      received_at: new Date(start + i * stepHours * 3_600_000).toISOString(),
      split: null,
      tags: ["simulated"],
      gold: null,
      simulated: true,
    };
  });
}
