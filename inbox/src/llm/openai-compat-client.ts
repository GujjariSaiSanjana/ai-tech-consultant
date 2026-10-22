import { z } from "zod";
import type { LlmCallSink, LlmClient, LlmRequest, LlmResult } from "./client";
import { costUsd, ZERO_USAGE, type TokenUsage } from "./cost";
import { LlmError, type LlmErrorKind } from "./errors";
import { providerPricing, type FallbackProvider, type ModelPricing, type ProviderModel, type RoutingTable } from "./models";

export type FetchLike = (input: string, init: RequestInit) => Promise<Response>;

export interface OpenAICompatConfig {
  provider: FallbackProvider;
  apiKey: string;
  model: ProviderModel;
  /** For example https://integrate.api.nvidia.com/v1. `/chat/completions` is appended. */
  baseUrl: string;
  /** Overrides the built-in price table for this model. */
  pricing?: ModelPricing;
  timeoutMs?: number;
  /** Injected for tests; defaults to the global fetch. */
  fetch?: FetchLike;
}

/** The parts of a Chat Completions response we read. Everything else is ignored. */
const CompletionSchema = z.object({
  model: z.string().optional(),
  choices: z
    .array(
      z.object({
        finish_reason: z.string().nullish(),
        message: z.object({ content: z.string().nullish(), refusal: z.string().nullish() }),
      }),
    )
    .min(1),
  usage: z.object({ prompt_tokens: z.number().optional(), completion_tokens: z.number().optional() }).nullish(),
});

type ResponseFormat =
  | { type: "json_schema"; json_schema: { name: string; schema: Record<string, unknown>; strict: true } }
  | { type: "json_object" };

function kindForStatus(status: number): LlmErrorKind {
  if (status === 401) return "auth";
  if (status === 403) return "permission";
  if (status === 404) return "not_found";
  if (status === 400 || status === 413 || status === 422) return "bad_request";
  if (status === 408) return "connection";
  if (status === 429) return "rate_limit";
  if (status === 503 || status === 529) return "overloaded";
  if (status >= 500) return "server";
  return "unknown";
}

function jsonSchemaFor(schema: z.ZodType): Record<string, unknown> {
  // Drop the "$schema" dialect marker: some OpenAI-compatible endpoints reject unknown top-level keys.
  const json = { ...(z.toJSONSchema(schema) as Record<string, unknown>) };
  delete json.$schema;
  return json;
}

/** Models in json_object mode sometimes wrap the object in a Markdown fence. */
function stripFence(text: string): string {
  const match = /^\s*```(?:json)?\s*([\s\S]*?)\s*```\s*$/i.exec(text);
  return match?.[1] ?? text;
}

/**
 * Fallback client for providers with an OpenAI-compatible Chat Completions API
 * (Gemini, NVIDIA NIM). Plain fetch, no SDK. Structured output uses a strict
 * json_schema response format; if the provider rejects that with a 400 it
 * retries once in json_object mode with the schema in the system prompt. The
 * reply is always validated with the request's own Zod schema. No sampling
 * parameters are sent.
 */
export class OpenAICompatLlmClient implements LlmClient {
  readonly mocked = false;
  private readonly fetch: FetchLike;
  private readonly pricing: ModelPricing | null;

  constructor(
    private readonly config: OpenAICompatConfig,
    private readonly routes: RoutingTable,
    private readonly sink: LlmCallSink,
  ) {
    this.fetch = config.fetch ?? ((input, init) => fetch(input, init));
    this.pricing = providerPricing(config.provider, config.model, config.pricing);
  }

  get provider(): FallbackProvider {
    return this.config.provider;
  }

  async parse<T>(request: LlmRequest<T>): Promise<LlmResult<T>> {
    const schema = jsonSchemaFor(request.schema);
    const strict: ResponseFormat = { type: "json_schema", json_schema: { name: request.purpose, schema, strict: true } };
    try {
      return await this.attempt(request, request.system, strict);
    } catch (error) {
      if (!(error instanceof LlmError) || error.kind !== "bad_request") throw error;
      const system = `${request.system}\n\nRespond with one JSON object only, no prose, that matches this JSON Schema:\n${JSON.stringify(schema)}`;
      return this.attempt(request, system, { type: "json_object" });
    }
  }

  private async attempt<T>(request: LlmRequest<T>, system: string, responseFormat: ResponseFormat): Promise<LlmResult<T>> {
    const { model } = this.config;
    const started = Date.now();
    const fail = (kind: LlmErrorKind, message: string, usage: TokenUsage = ZERO_USAGE, stopReason: string | null = null, cause?: unknown): never => {
      this.sink.record({
        purpose: request.purpose,
        model,
        usage,
        costUsd: costUsd(model, usage, this.pricing),
        latencyMs: Date.now() - started,
        stopReason,
        error: `${kind}: ${message}`.slice(0, 500),
        mocked: false,
      });
      throw new LlmError(kind, `${this.config.provider}: ${message}`, cause);
    };

    let response: Response;
    try {
      response = await this.fetch(`${this.config.baseUrl.replace(/\/+$/, "")}/chat/completions`, {
        method: "POST",
        headers: { authorization: `Bearer ${this.config.apiKey}`, "content-type": "application/json" },
        body: JSON.stringify({
          model,
          max_tokens: this.routes[request.purpose].maxTokens,
          messages: [
            { role: "system", content: system },
            { role: "user", content: request.user },
          ],
          response_format: responseFormat,
        }),
        signal: AbortSignal.timeout(this.config.timeoutMs ?? 120_000),
      });
    } catch (error) {
      return fail("connection", error instanceof Error ? error.message : String(error), ZERO_USAGE, null, error);
    }

    if (!response.ok) {
      const body = await response.text().catch(() => "");
      return fail(kindForStatus(response.status), `HTTP ${response.status} ${body}`.trim());
    }

    let json: unknown;
    try {
      json = await response.json();
    } catch (error) {
      return fail("parse", "response body is not JSON", ZERO_USAGE, null, error);
    }
    const completion = CompletionSchema.safeParse(json);
    if (!completion.success) return fail("parse", `unexpected response shape: ${completion.error.message}`);

    const { choices, usage: rawUsage } = completion.data;
    const choice = choices[0];
    if (!choice) return fail("parse", "no choices");
    const usage: TokenUsage = {
      inputTokens: rawUsage?.prompt_tokens ?? 0,
      outputTokens: rawUsage?.completion_tokens ?? 0,
      cacheReadTokens: 0,
      cacheWriteTokens: 0,
    };
    const stopReason = choice.finish_reason ?? null;

    if (choice.message.refusal || stopReason === "content_filter") {
      return fail("refusal", `model declined the ${request.purpose} request`, usage, "refusal");
    }
    if (stopReason === "length") return fail("max_tokens", `${request.purpose} output was cut off at max_tokens`, usage, stopReason);
    const content = choice.message.content;
    if (!content) return fail("parse", `${request.purpose} returned no content`, usage, stopReason);

    let raw: unknown;
    try {
      raw = JSON.parse(stripFence(content));
    } catch (error) {
      return fail("parse", `${request.purpose} output is not JSON`, usage, stopReason, error);
    }
    const parsed = request.schema.safeParse(raw);
    if (!parsed.success) return fail("parse", `${request.purpose} output failed validation: ${parsed.error.message}`, usage, stopReason);

    const latencyMs = Date.now() - started;
    const cost = costUsd(model, usage, this.pricing);
    this.sink.record({ purpose: request.purpose, model, usage, costUsd: cost, latencyMs, stopReason, error: null, mocked: false });
    return { data: parsed.data, model, usage, costUsd: cost, latencyMs, mocked: false };
  }
}
