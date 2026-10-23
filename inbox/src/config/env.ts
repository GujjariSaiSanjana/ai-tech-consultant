import { z } from "zod";
import { isFallbackProvider, type FallbackProvider } from "../llm/models";

const EnvSchema = z.object({
  ANTHROPIC_API_KEY: z.string().optional(),
  VOYAGE_API_KEY: z.string().optional(),
  VOYAGE_MODEL: z.string().default("voyage-3.5"),
  INBOX_DATABASE_PATH: z.string().default("./data/runtime/inbox.db"),
  /** fake: built-in simulated knowledge base. http: Brain REST API. mcp: Brain MCP server over stdio. */
  INBOX_BRAIN_MODE: z.enum(["fake", "http", "mcp"]).optional(),
  BRAIN_URL: z.string().default("http://localhost:8000"),
  BRAIN_API_KEY: z.string().default("dev-brain-key"),
  BRAIN_MCP_COMMAND: z.string().default("python"),
  BRAIN_MCP_ARGS: z.string().default("-m brain.mcp_server"),
  BRAIN_MCP_CWD: z.string().optional(),
  /** Set to "1" to force the offline fake model even when an API key exists. */
  INBOX_MOCK_LLM: z.string().optional(),
  /** Fallback providers behind Claude, over their OpenAI-compatible Chat Completions API. */
  GEMINI_API_KEY: z.string().optional(),
  GEMINI_MODEL: z.string().default("gemini-2.5-flash"),
  GEMINI_BASE_URL: z.string().default("https://generativelanguage.googleapis.com/v1beta/openai/"),
  NVIDIA_API_KEY: z.string().optional(),
  NVIDIA_MODEL: z.string().default("openai/gpt-oss-20b"),
  NVIDIA_BASE_URL: z.string().default("https://integrate.api.nvidia.com/v1"),
  /** Comma-separated fallback order. An empty string disables fallback. */
  LLM_FALLBACK_ORDER: z.string().default("gemini,nvidia"),
});
export type Env = z.infer<typeof EnvSchema>;

/** Variables where an explicit empty string means something (so it is not treated as unset). */
const EMPTY_IS_MEANINGFUL: ReadonlySet<string> = new Set(["LLM_FALLBACK_ORDER"]);

export function readEnv(source: Record<string, string | undefined> = process.env): Env {
  const cleaned = Object.fromEntries(
    Object.entries(source).filter(([k, v]) => v !== undefined && (v !== "" || EMPTY_IS_MEANINGFUL.has(k))),
  );
  return EnvSchema.parse(cleaned);
}

/** Fallback providers in the configured order, keeping only those with a key. Unknown names fail loudly. */
export function fallbackProviders(env: Env): FallbackProvider[] {
  const names = env.LLM_FALLBACK_ORDER.split(",")
    .map((n) => n.trim().toLowerCase())
    .filter(Boolean);
  const unknown = names.filter((n) => !isFallbackProvider(n));
  if (unknown.length > 0) throw new Error(`LLM_FALLBACK_ORDER has unknown provider(s): ${unknown.join(", ")}`);
  const keyed = (p: FallbackProvider) => Boolean(p === "gemini" ? env.GEMINI_API_KEY : env.NVIDIA_API_KEY);
  return [...new Set(names as FallbackProvider[])].filter(keyed);
}

/** A real model is used when Claude or any enabled fallback has a key; the fake runs only when there is none. */
export function hasRealLlm(env: Env): boolean {
  if (env.INBOX_MOCK_LLM === "1") return false;
  return Boolean(env.ANTHROPIC_API_KEY) || fallbackProviders(env).length > 0;
}
