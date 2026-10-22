import { Client } from "@modelcontextprotocol/sdk/client/index.js";
import { StdioClientTransport } from "@modelcontextprotocol/sdk/client/stdio.js";
import type { z } from "zod";
import {
  AskResponseSchema,
  BrainUnavailableError,
  SearchResponseSchema,
  type AskRequest,
  type AskResponse,
  type BrainClient,
  type SearchRequest,
  type SearchResponse,
} from "./schemas";

export interface McpBrainOptions {
  command: string;
  args: string[];
  cwd?: string;
  env?: Record<string, string>;
}

/**
 * Talks to the Brain MCP server (`python -m brain.mcp_server`, stdio) using tools
 * kb_ask and kb_search. Outputs are validated against the same schemas as HTTP.
 */
export class McpBrainClient implements BrainClient {
  readonly name = "brain-mcp";
  private connection: Promise<Client> | null = null;

  constructor(private readonly options: McpBrainOptions) {}

  ask(request: AskRequest): Promise<AskResponse> {
    return this.call("kb_ask", request, AskResponseSchema);
  }

  search(request: SearchRequest): Promise<SearchResponse> {
    return this.call("kb_search", request, SearchResponseSchema);
  }

  async close(): Promise<void> {
    if (!this.connection) return;
    const client = await this.connection.catch(() => null);
    this.connection = null;
    await client?.close();
  }

  private connect(): Promise<Client> {
    if (!this.connection) {
      const client = new Client({ name: "inbox-agent", version: "0.1.0" });
      const transport = new StdioClientTransport({
        command: this.options.command,
        args: this.options.args,
        ...(this.options.cwd ? { cwd: this.options.cwd } : {}),
        ...(this.options.env ? { env: this.options.env } : {}),
      });
      this.connection = client.connect(transport).then(() => client);
      this.connection.catch(() => {
        this.connection = null;
      });
    }
    return this.connection;
  }

  private async call<T>(tool: string, args: Record<string, unknown>, schema: z.ZodType<T>): Promise<T> {
    let result: Awaited<ReturnType<Client["callTool"]>>;
    try {
      const client = await this.connect();
      result = await client.callTool({ name: tool, arguments: args });
    } catch (error) {
      throw new BrainUnavailableError(`Brain MCP tool ${tool} failed`, error);
    }
    if (result.isError === true) throw new BrainUnavailableError(`Brain MCP tool ${tool} returned an error`);
    const payload = result.structuredContent ?? firstTextAsJson(result.content);
    const parsed = schema.safeParse(payload);
    if (!parsed.success) throw new BrainUnavailableError(`Brain MCP tool ${tool} returned an off-contract body`, parsed.error);
    return parsed.data;
  }
}

function firstTextAsJson(content: unknown): unknown {
  if (!Array.isArray(content)) return null;
  for (const block of content) {
    if (typeof block === "object" && block !== null && "type" in block && block.type === "text" && "text" in block) {
      try {
        return JSON.parse(String(block.text));
      } catch {
        return null;
      }
    }
  }
  return null;
}
