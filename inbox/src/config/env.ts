import { z } from "zod";

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
});
export type Env = z.infer<typeof EnvSchema>;

export function readEnv(source: Record<string, string | undefined> = process.env): Env {
  const cleaned = Object.fromEntries(Object.entries(source).filter(([, v]) => v !== undefined && v !== ""));
  return EnvSchema.parse(cleaned);
}

export function hasRealLlm(env: Env): boolean {
  return Boolean(env.ANTHROPIC_API_KEY) && env.INBOX_MOCK_LLM !== "1";
}
