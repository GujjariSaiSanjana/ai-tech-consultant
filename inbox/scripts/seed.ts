/**
 * npm run seed
 *
 * Resets the inbox database, loads data/emails.jsonl and replays a scripted
 * reviewer over most of it so the trust meter, playbook versions and learning
 * chart have history to show. Runs fully offline (fake model + simulated
 * knowledge base) unless --live is passed. Every scripted action is stored with
 * simulated = 1 and the UI labels it as simulated.
 */
import { readEnv } from "../src/config/env";
import { resetDatabase } from "../src/db/database";
import { loadDataset } from "../src/evals/dataset";
import { createServices } from "../src/services/container";
import { simulatedHowToEmails, simulatedReviewerEdit } from "../src/services/simulation";

const PENDING_DEV_LEFT = 8;
const SIMULATED_HOW_TO = 36;
const AUTO_SENDS_BEFORE_BREAKER = 3;
const RECOVERY_EMAILS = 3;

function simulatedClock(startIso: string, stepMinutes: number): () => string {
  let t = Date.parse(startIso);
  return () => {
    t += stepMinutes * 60_000;
    return new Date(t).toISOString();
  };
}

async function main(): Promise<void> {
  const live = process.argv.includes("--live");
  const env = readEnv();
  const dbPath = env.INBOX_DATABASE_PATH;
  resetDatabase(dbPath);

  const clock = simulatedClock("2026-08-31T08:00:00Z", 45);
  const services = createServices({
    dbPath,
    llm: live ? "real" : "mock",
    brain: live ? (env.INBOX_BRAIN_MODE ?? "http") : "fake",
    embedder: live && env.VOYAGE_API_KEY ? "voyage" : "hashing",
    clock,
    env,
  });
  const { agent, repos } = services;

  const dataset = loadDataset();
  for (const email of dataset) agent.ingest({ ...email, simulated: false });
  const simulated = simulatedHowToEmails(SIMULATED_HOW_TO + AUTO_SENDS_BEFORE_BREAKER + RECOVERY_EMAILS, "2026-08-31T09:00:00Z", 24);
  for (const email of simulated) agent.ingest(email);

  const dev = dataset.filter((e) => e.split === "dev");
  const reviewed = new Set(dev.slice(0, dev.length - PENDING_DEV_LEFT).map((e) => e.id));
  const order = [...repos.emails.listByStatus("new")].sort((a, b) => a.received_at.localeCompare(b.received_at));

  let actions = 0;
  let autoSent = 0;
  let breakerTripped = false;
  for (const email of order) {
    const isSimulatedHowTo = email.simulated;
    const { draftId, autoSent: wasAuto } = await agent.processEmail(email.id);
    if (wasAuto) {
      autoSent += 1;
      if (!breakerTripped && autoSent === AUTO_SENDS_BEFORE_BREAKER) {
        const draft = repos.drafts.get(draftId);
        if (draft) {
          const corrected = `${draft.reply_text.replace("Let me know if anything is unclear.", "This one needs a different setting for your plan, so I've added the exact steps below.")}`;
          await agent.recordAction({ draftId, action: "auto_rewrite", text: corrected, simulated: true });
          breakerTripped = true;
          actions += 1;
        }
      }
      continue;
    }
    if (!isSimulatedHowTo && !reviewed.has(email.id)) continue;
    const draft = repos.drafts.get(draftId);
    const classification = draft ? repos.drafts.classification(draft.classification_id) : null;
    if (!draft || !classification) continue;
    if (classification.intent === "spam" || draft.reply_text === "") {
      await agent.recordAction({ draftId, action: "reject", simulated: true });
    } else {
      const edited = simulatedReviewerEdit(draft.reply_text, classification.intent);
      await agent.recordAction({ draftId, action: edited === draft.reply_text ? "send" : "edit_send", text: edited, simulated: true });
    }
    actions += 1;
  }

  const check = await agent.qualityCheck(`seed-${Date.now()}`);
  const versions = repos.playbook.versions();
  console.log(
    [
      `Seeded ${repos.emails.count()} emails into ${dbPath} (${live ? "live model" : "offline fake model"}).`,
      `Simulated reviewer actions: ${actions}. Auto-sent: ${autoSent}. Breaker demo: ${breakerTripped ? "tripped" : "not reached"}.`,
      `Playbook versions: ${versions.length} (active v${repos.playbook.activeVersionId()}). Examples stored: ${repos.actions.countExamples()}.`,
      `Quality check score: ${check.score.toFixed(3)}${services.llm.mocked ? " (mocked)" : ""}.`,
      ...repos.trust.all().slice(0, 6).map((t) => `  ${t.kindKey}: ${t.mode}, streak ${t.streak}, ${t.countedTotal} counted`),
    ].join("\n"),
  );
  services.db.close();
}

main().catch((error: unknown) => {
  console.error(error);
  process.exit(1);
});
