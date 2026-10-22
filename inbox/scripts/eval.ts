/**
 * npm run eval [-- --models claude-sonnet-5-5,claude-opus-5-5 --split dev|heldout --mock --reps 1 --with-learning --limit N]
 *
 * Runs the full pipeline on labelled emails and writes one row per case to
 * ../evals/results/inbox/<run_id>.jsonl (docs/contracts/eval-results.md).
 * Without any model key (or with --mock) it runs the offline fake model and
 * every row says mocked: true. With fallback keys set, a case Claude could not
 * serve is drafted by Gemini/NVIDIA and its row carries that model name. Baseline = empty playbook and no examples;
 * --with-learning uses the active playbook and stored examples from
 * INBOX_DATABASE_PATH, so the two runs show whether learning helps or hurts.
 */
import { mkdirSync, writeFileSync } from "node:fs";
import { resolve } from "node:path";
import { parseArgs } from "node:util";
import { FakeBrainClient } from "../src/brain/fake-client";
import { HttpBrainClient } from "../src/brain/http-client";
import { McpBrainClient } from "../src/brain/mcp-client";
import type { BrainClient } from "../src/brain/schemas";
import { hasRealLlm, readEnv, type Env } from "../src/config/env";
import { THRESHOLDS } from "../src/config/thresholds";
import { SplitSchema } from "../src/domain/schemas";
import { bySplit, loadDataset } from "../src/evals/dataset";
import { CaseUsageSink, runEval, summarize, type EvalCaseDetail } from "../src/evals/runner";
import { createLlmClient } from "../src/llm/factory";
import { FakeLlmClient } from "../src/llm/fake-client";
import { heuristicHandlers } from "../src/llm/heuristics";
import { DEFAULT_ROUTES, isModelId, withDraftModel, type ModelId } from "../src/llm/models";
import type { ExampleMemory, PlaybookContext } from "../src/pipeline/run";
import { createServices } from "../src/services/container";
import { playbookContext } from "../src/services/learning-loop";
import { DbExampleMemory } from "../src/services/memory";

const NO_MEMORY: ExampleMemory = { nearest: async () => [] };
const EMPTY_PLAYBOOK: PlaybookContext = { versionId: 0, rules: [] };

function brainFor(env: Env, mocked: boolean): BrainClient {
  if (mocked) return new FakeBrainClient();
  const mode = env.INBOX_BRAIN_MODE ?? "fake";
  if (mode === "http") return new HttpBrainClient(env.BRAIN_URL, env.BRAIN_API_KEY, THRESHOLDS.brainTimeoutMs);
  if (mode === "mcp") return new McpBrainClient({ command: env.BRAIN_MCP_COMMAND, args: env.BRAIN_MCP_ARGS.split(/\s+/).filter(Boolean) });
  return new FakeBrainClient();
}

function pct(x: number): string {
  return `${(x * 100).toFixed(1)}%`;
}

async function main(): Promise<void> {
  const { values } = parseArgs({
    options: {
      models: { type: "string", default: DEFAULT_ROUTES.draft.model },
      split: { type: "string", default: "dev" },
      mock: { type: "boolean", default: false },
      reps: { type: "string", default: "1" },
      limit: { type: "string" },
      "with-learning": { type: "boolean", default: false },
    },
  });
  const env = readEnv();
  const split = SplitSchema.parse(values.split);
  const models = (values.models ?? "").split(",").map((m) => m.trim()).filter(Boolean);
  const bad = models.filter((m) => !isModelId(m));
  if (bad.length > 0) throw new Error(`Unknown model(s): ${bad.join(", ")}`);
  const mocked = values.mock === true || !hasRealLlm(env);
  const reps = Math.max(1, Number(values.reps));
  const all = bySplit(loadDataset(), split);
  const cases = values.limit ? all.slice(0, Number(values.limit)) : all;

  let memory = NO_MEMORY;
  let playbook = EMPTY_PLAYBOOK;
  if (values["with-learning"]) {
    const store = createServices({ dbPath: env.INBOX_DATABASE_PATH, llm: "mock", brain: "fake", embedder: env.VOYAGE_API_KEY ? "voyage" : "hashing", env });
    memory = new DbExampleMemory(store.repos.actions, store.embedder);
    playbook = playbookContext(store.repos.playbook, store.repos.playbook.requireActiveVersion().id);
  }

  const stamp = new Date().toISOString().replace(/[:.]/g, "-");
  const runId = `inbox-${split}-${values["with-learning"] ? "learned" : "baseline"}-${mocked ? "mock" : "live"}-${stamp}`;
  const details: EvalCaseDetail[] = [];
  for (const model of models as ModelId[]) {
    const sink = new CaseUsageSink();
    const routes = withDraftModel(DEFAULT_ROUTES, model);
    const llm = mocked ? new FakeLlmClient(heuristicHandlers(THRESHOLDS.reflectorMaxRules), routes, sink) : createLlmClient(routes, sink, env);
    details.push(...(await runEval({ runId, cases, reps, llm, sink, routeModel: model, brain: brainFor(env, mocked), memory, playbook })));
  }

  const outDir = resolve(process.cwd(), "../evals/results/inbox");
  mkdirSync(outDir, { recursive: true });
  const outFile = resolve(outDir, `${runId}.jsonl`);
  writeFileSync(outFile, `${details.map((d) => JSON.stringify(d.row)).join("\n")}\n`);

  const lines = [
    `Run ${runId}`,
    `${cases.length} cases x ${reps} rep(s), split=${split}, playbook=${values["with-learning"] ? `v${playbook.versionId} (${playbook.rules.length} rules)` : "baseline"}${mocked ? ", MOCKED (no API key or --mock): numbers describe the offline heuristics, not a model" : ""}`,
    "",
    "| model | cases | errors | pass rate (95% CI) | mean score | intent acc | risk acc | safety | cost USD | p50 ms | p95 ms |",
    "|---|---|---|---|---|---|---|---|---|---|---|",
    ...summarize(details).map(
      (s) =>
        `| ${s.model}${s.mocked ? " (mock)" : ""} | ${s.cases} | ${s.errors} | ${pct(s.passRate)} (${pct(s.passRateLow)}-${pct(s.passRateHigh)}) | ${s.meanScore.toFixed(3)} | ${pct(s.intentAccuracy)} | ${pct(s.riskAccuracy)} | ${pct(s.safetyRate)} | ${s.costUsd.toFixed(4)} | ${s.p50LatencyMs} | ${s.p95LatencyMs} |`,
    ),
    "",
    `Rows written to ${outFile}`,
  ];
  console.log(lines.join("\n"));
}

main().catch((error: unknown) => {
  console.error(error);
  process.exit(1);
});
