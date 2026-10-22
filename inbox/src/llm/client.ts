import Anthropic from "@anthropic-ai/sdk";
import { zodOutputFormat } from "@anthropic-ai/sdk/helpers/zod";
import type { z } from "zod";
import { costUsd, ZERO_USAGE, type TokenUsage } from "./cost";
import { LlmError, toLlmError } from "./errors";
import type { LlmPurpose, RoutingTable } from "./models";

export interface LlmRequest<T> {
  purpose: LlmPurpose;
  /** Stable instructions. Must not contain per-request data so it stays cacheable. */
  system: string;
  /** Per-request content. Untrusted text must already be wrapped in data blocks. */
  user: string;
  schema: z.ZodType<T>;
}

export interface LlmResult<T> {
  data: T;
  model: string;
  usage: TokenUsage;
  costUsd: number;
  latencyMs: number;
  mocked: boolean;
}

export interface LlmCallRecord {
  purpose: LlmPurpose;
  model: string;
  usage: TokenUsage;
  costUsd: number;
  latencyMs: number;
  stopReason: string | null;
  error: string | null;
  mocked: boolean;
}

/** Where every model call is logged (the llm_calls table in production). */
export interface LlmCallSink {
  record(call: LlmCallRecord): void;
}

export const NULL_SINK: LlmCallSink = { record: () => undefined };

/** The only interface pipeline code depends on. Real and fake implementations are interchangeable. */
export interface LlmClient {
  readonly mocked: boolean;
  parse<T>(request: LlmRequest<T>): Promise<LlmResult<T>>;
}

function usageFrom(u: Anthropic.Usage): TokenUsage {
  return {
    inputTokens: u.input_tokens,
    outputTokens: u.output_tokens,
    cacheReadTokens: u.cache_read_input_tokens ?? 0,
    cacheWriteTokens: u.cache_creation_input_tokens ?? 0,
  };
}

export class AnthropicLlmClient implements LlmClient {
  readonly mocked = false;

  constructor(
    private readonly client: Anthropic,
    private readonly routes: RoutingTable,
    private readonly sink: LlmCallSink,
  ) {}

  async parse<T>(request: LlmRequest<T>): Promise<LlmResult<T>> {
    const route = this.routes[request.purpose];
    const started = Date.now();
    try {
      const response = await this.client.messages.parse({
        model: route.model,
        max_tokens: route.maxTokens,
        system: [{ type: "text", text: request.system, cache_control: { type: "ephemeral" } }],
        messages: [{ role: "user", content: request.user }],
        output_config: { effort: route.effort, format: zodOutputFormat(request.schema) },
      });
      const latencyMs = Date.now() - started;
      const usage = usageFrom(response.usage);
      const cost = costUsd(response.model, usage);
      const base = { purpose: request.purpose, model: response.model, usage, costUsd: cost, latencyMs, mocked: false };

      if (response.stop_reason === "refusal") {
        const category = response.stop_details?.category ?? "unspecified";
        this.sink.record({ ...base, stopReason: "refusal", error: `refusal:${category}` });
        throw new LlmError("refusal", `Model declined the ${request.purpose} request (${category})`);
      }
      if (response.stop_reason === "max_tokens") {
        this.sink.record({ ...base, stopReason: "max_tokens", error: "max_tokens" });
        throw new LlmError("max_tokens", `${request.purpose} output was cut off at max_tokens`);
      }
      const parsed = response.parsed_output;
      if (parsed === null) {
        this.sink.record({ ...base, stopReason: response.stop_reason, error: "no_structured_output" });
        throw new LlmError("parse", `${request.purpose} returned no structured output`);
      }
      this.sink.record({ ...base, stopReason: response.stop_reason, error: null });
      return { data: parsed as T, model: response.model, usage, costUsd: cost, latencyMs, mocked: false };
    } catch (error) {
      if (error instanceof LlmError) throw error;
      const mapped = toLlmError(error);
      this.sink.record({
        purpose: request.purpose,
        model: route.model,
        usage: ZERO_USAGE,
        costUsd: 0,
        latencyMs: Date.now() - started,
        stopReason: null,
        error: `${mapped.kind}: ${mapped.message}`.slice(0, 500),
        mocked: false,
      });
      throw mapped;
    }
  }
}

export function createAnthropicClient(routes: RoutingTable, sink: LlmCallSink): AnthropicLlmClient {
  return new AnthropicLlmClient(new Anthropic({ maxRetries: 3, timeout: 120_000 }), routes, sink);
}
