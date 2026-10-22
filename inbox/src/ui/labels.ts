import type { Outcome, TrustMode } from "../domain/schemas";

/** Plain-language labels for people who never read the code. */
export const MODE_LABEL: Record<TrustMode, string> = {
  shadow: "Learning",
  assist: "Drafts for review",
  auto: "Sends on its own",
};

export const MODE_HELP: Record<TrustMode, string> = {
  shadow: "New kind of email. The agent drafts, a person reviews every reply, and the agent watches what changes.",
  assist: "The agent drafts; a person sends with one click or edits first.",
  auto: "Drafts this kind went out unchanged so often that clean drafts now send automatically. Every auto-send is logged.",
};

export const OUTCOME_LABEL: Record<Outcome, string> = {
  unchanged: "Sent as drafted",
  minor_edit: "Small edit",
  material_edit: "Real edit",
  rewrite: "Rewritten",
};

export function relativeAge(iso: string, now: number = Date.now()): string {
  const minutes = Math.max(0, Math.round((now - Date.parse(iso)) / 60_000));
  if (minutes < 60) return `${minutes}m`;
  const hours = Math.round(minutes / 60);
  if (hours < 48) return `${hours}h`;
  const days = Math.round(hours / 24);
  return `${days}d`;
}
