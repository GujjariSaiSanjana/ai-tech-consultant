import { FakeBrainClient } from "../src/brain/fake-client";
import type { BrainClient } from "../src/brain/schemas";
import { readEnv } from "../src/config/env";
import { THRESHOLDS } from "../src/config/thresholds";
import type { NewEmail } from "../src/db/emails-repo";
import type { GoldLabels } from "../src/domain/schemas";
import { HashingEmbedder } from "../src/embeddings/embedder";
import { NULL_SINK, type LlmClient } from "../src/llm/client";
import { FakeLlmClient, type FakeHandlers } from "../src/llm/fake-client";
import { heuristicHandlers } from "../src/llm/heuristics";
import { DEFAULT_ROUTES } from "../src/llm/models";
import { createServices, type Services } from "../src/services/container";

export function testClock(): () => string {
  let t = Date.parse("2026-09-01T00:00:00Z");
  return () => {
    t += 60_000;
    return new Date(t).toISOString();
  };
}

export function makeServices(
  options: { handlers?: FakeHandlers; brain?: BrainClient; wrapLlm?: (llm: LlmClient) => LlmClient } = {},
): Services {
  const handlers = { ...heuristicHandlers(THRESHOLDS.reflectorMaxRules), ...options.handlers };
  const fake = new FakeLlmClient(handlers, DEFAULT_ROUTES, NULL_SINK);
  return createServices({
    dbPath: ":memory:",
    llm: options.wrapLlm ? options.wrapLlm(fake) : fake,
    brain: options.brain ?? new FakeBrainClient(),
    embedder: new HashingEmbedder(),
    clock: testClock(),
    env: readEnv({ INBOX_DATABASE_PATH: ":memory:", VOYAGE_MODEL: "x", BRAIN_URL: "http://x", BRAIN_API_KEY: "k", BRAIN_MCP_COMMAND: "python", BRAIN_MCP_ARGS: "" }),
  });
}

let counter = 0;

export function email(over: Partial<NewEmail> = {}): NewEmail {
  counter += 1;
  return {
    id: `t-${counter}`,
    from_name: "Ana Lee",
    from_email: "ana@lee.example",
    subject: "How do I connect Stripe?",
    body: "Hi, how do I connect Stripe so members can pay by card? Thanks, Ana",
    received_at: new Date(Date.parse("2026-09-01T00:00:00Z") + counter * 3_600_000).toISOString(),
    split: null,
    tags: [],
    gold: null,
    simulated: false,
    ...over,
  };
}

export const STRIPE_GOLD: GoldLabels = { intent: "how_to", risk_class: "none", must_mention: ["Stripe"], must_not_say: ["forbidden"] };
