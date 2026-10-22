import { THRESHOLDS } from "../config/thresholds";
import { hasRealLlm, readEnv, type Env } from "../config/env";
import { FakeBrainClient } from "../brain/fake-client";
import { HttpBrainClient } from "../brain/http-client";
import { McpBrainClient } from "../brain/mcp-client";
import type { BrainClient } from "../brain/schemas";
import { ActionsRepo } from "../db/actions-repo";
import { Db } from "../db/database";
import { DraftsRepo } from "../db/drafts-repo";
import { EmailsRepo } from "../db/emails-repo";
import { EvalRunsRepo, LlmCallsRepo, SettingsRepo } from "../db/ops-repo";
import { PlaybookRepo } from "../db/playbook-repo";
import { TrustRepo } from "../db/trust-repo";
import { HashingEmbedder, VoyageEmbedder, type Embedder } from "../embeddings/embedder";
import type { LlmCallSink, LlmClient } from "../llm/client";
import { createLlmClient } from "../llm/factory";
import { FakeLlmClient } from "../llm/fake-client";
import { heuristicHandlers } from "../llm/heuristics";
import { DEFAULT_ROUTES, type RoutingTable } from "../llm/models";
import { InboxAgent, type Repos } from "./agent";
import { DbExampleMemory } from "./memory";

export interface Services {
  db: Db;
  repos: Repos;
  llmCalls: LlmCallsRepo;
  llm: LlmClient;
  brain: BrainClient;
  embedder: Embedder;
  agent: InboxAgent;
}

export interface ServiceOptions {
  dbPath: string;
  llm: "real" | "mock" | LlmClient;
  brain: "fake" | "http" | "mcp" | BrainClient;
  embedder: "hashing" | "voyage" | Embedder;
  routes?: RoutingTable;
  clock?: () => string;
  env?: Env;
}

export function defaultOptions(env: Env = readEnv()): ServiceOptions {
  return {
    dbPath: env.INBOX_DATABASE_PATH,
    llm: hasRealLlm(env) ? "real" : "mock",
    brain: env.INBOX_BRAIN_MODE ?? "fake",
    embedder: env.VOYAGE_API_KEY ? "voyage" : "hashing",
    env,
  };
}

function buildLlm(option: ServiceOptions["llm"], routes: RoutingTable, sink: LlmCallSink, env: Env): LlmClient {
  if (typeof option !== "string") return option;
  return option === "real"
    ? createLlmClient(routes, sink, env)
    : new FakeLlmClient(heuristicHandlers(THRESHOLDS.reflectorMaxRules), routes, sink);
}

function buildBrain(option: ServiceOptions["brain"], env: Env): BrainClient {
  if (typeof option !== "string") return option;
  if (option === "http") return new HttpBrainClient(env.BRAIN_URL, env.BRAIN_API_KEY, THRESHOLDS.brainTimeoutMs);
  if (option === "mcp") {
    return new McpBrainClient({
      command: env.BRAIN_MCP_COMMAND,
      args: env.BRAIN_MCP_ARGS.split(/\s+/).filter(Boolean),
      ...(env.BRAIN_MCP_CWD ? { cwd: env.BRAIN_MCP_CWD } : {}),
      env: { BRAIN_API_KEY: env.BRAIN_API_KEY, PATH: process.env.PATH ?? "" },
    });
  }
  return new FakeBrainClient();
}

function buildEmbedder(option: ServiceOptions["embedder"], env: Env): Embedder {
  if (typeof option !== "string") return option;
  if (option === "voyage" && env.VOYAGE_API_KEY) return new VoyageEmbedder(env.VOYAGE_API_KEY, env.VOYAGE_MODEL);
  return new HashingEmbedder();
}

/** Composition root: every dependency is injected here and nowhere else. */
export function createServices(options: ServiceOptions): Services {
  const env = options.env ?? readEnv();
  const clock = options.clock ?? (() => new Date().toISOString());
  const db = new Db(options.dbPath);
  const llmCalls = new LlmCallsRepo(db, clock);
  const repos: Repos = {
    emails: new EmailsRepo(db),
    drafts: new DraftsRepo(db),
    actions: new ActionsRepo(db),
    playbook: new PlaybookRepo(db),
    trust: new TrustRepo(db),
    settings: new SettingsRepo(db),
    evalRuns: new EvalRunsRepo(db),
  };
  const llm = buildLlm(options.llm, options.routes ?? DEFAULT_ROUTES, llmCalls, env);
  const brain = buildBrain(options.brain, env);
  const embedder = buildEmbedder(options.embedder, env);
  const pipeline = { llm, brain, memory: new DbExampleMemory(repos.actions, embedder) };
  const agent = new InboxAgent({ repos, llm, pipeline, embedder, clock });
  return { db, repos, llmCalls, llm, brain, embedder, agent };
}

const GLOBAL_KEY = Symbol.for("inbox-agent.services");
type GlobalWithServices = typeof globalThis & { [GLOBAL_KEY]?: Services };

/** Process-wide singleton for the Next.js server (survives dev hot reloads). */
export function getServices(): Services {
  const g = globalThis as GlobalWithServices;
  g[GLOBAL_KEY] ??= createServices(defaultOptions());
  return g[GLOBAL_KEY];
}
