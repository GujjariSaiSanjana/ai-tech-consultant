import { describe, expect, it } from "vitest";
import { z } from "zod";
import type { LlmCallRecord } from "../src/llm/client";
import { DEFAULT_ROUTES } from "../src/llm/models";
import { OpenAICompatLlmClient, type FetchLike, type OpenAICompatConfig } from "../src/llm/openai-compat-client";

const Schema = z.object({ ok: z.boolean() });
const request = { purpose: "classify" as const, system: "sys", user: "u", schema: Schema };

interface Call {
  url: string;
  headers: Record<string, string>;
  body: Record<string, unknown>;
}

function completion(content: string | null, extra: { finish_reason?: string; refusal?: string; usage?: object } = {}) {
  return {
    model: "served-model",
    choices: [{ finish_reason: extra.finish_reason ?? "stop", message: { content, refusal: extra.refusal ?? null } }],
    usage: extra.usage ?? { prompt_tokens: 1000, completion_tokens: 200 },
  };
}

/** Each queued reply is either a JSON body (HTTP 200), an [status, text] error, or an Error to throw. */
function stubFetch(replies: Array<object | [number, string] | Error>) {
  const calls: Call[] = [];
  const fetch: FetchLike = async (url, init) => {
    calls.push({ url, headers: init.headers as Record<string, string>, body: JSON.parse(String(init.body)) as Record<string, unknown> });
    const reply = replies.shift();
    if (!reply) throw new Error("no reply queued");
    if (reply instanceof Error) throw reply;
    if (Array.isArray(reply)) return new Response(reply[1], { status: reply[0] });
    return new Response(JSON.stringify(reply), { status: 200, headers: { "content-type": "application/json" } });
  };
  return { fetch, calls };
}

function sink() {
  const records: LlmCallRecord[] = [];
  return { records, sink: { record: (r: LlmCallRecord) => records.push(r) } };
}

function client(fetch: FetchLike, over: Partial<OpenAICompatConfig> = {}) {
  const s = sink();
  const config: OpenAICompatConfig = {
    provider: "gemini",
    apiKey: "g-key",
    model: "gemini-2.5-flash",
    baseUrl: "https://generativelanguage.googleapis.com/v1beta/openai/",
    fetch,
    ...over,
  };
  return { llm: new OpenAICompatLlmClient(config, DEFAULT_ROUTES, s.sink), ...s };
}

describe("OpenAICompatLlmClient", () => {
  it("posts a strict json_schema request with bearer auth and no sampling parameters", async () => {
    const { fetch, calls } = stubFetch([completion('{"ok":true}')]);
    const { llm, records } = client(fetch);
    const result = await llm.parse(request);

    expect(result).toMatchObject({ data: { ok: true }, model: "gemini-2.5-flash", mocked: false });
    const call = calls[0];
    expect(call?.url).toBe("https://generativelanguage.googleapis.com/v1beta/openai/chat/completions");
    expect(call?.headers.authorization).toBe("Bearer g-key");
    expect(call?.body.model).toBe("gemini-2.5-flash");
    expect(call?.body.messages).toEqual([
      { role: "system", content: "sys" },
      { role: "user", content: "u" },
    ]);
    expect(call?.body.response_format).toEqual({
      type: "json_schema",
      json_schema: { name: "classify", schema: expect.objectContaining({ type: "object", required: ["ok"] }), strict: true },
    });
    for (const forbidden of ["temperature", "top_p", "top_k", "seed", "tool_choice"]) expect(call?.body).not.toHaveProperty(forbidden);
    expect(records).toHaveLength(1);
  });

  it("reads usage from prompt/completion tokens, prices it and records it under the provider model name", async () => {
    const { fetch } = stubFetch([completion('{"ok":false}', { usage: { prompt_tokens: 1_000_000, completion_tokens: 1_000_000 } })]);
    const { llm, records } = client(fetch);
    const result = await llm.parse(request);
    expect(result.usage).toEqual({ inputTokens: 1_000_000, outputTokens: 1_000_000, cacheReadTokens: 0, cacheWriteTokens: 0 });
    expect(result.costUsd).toBeCloseTo(0.3 + 2.5);
    expect(records[0]).toMatchObject({ purpose: "classify", model: "gemini-2.5-flash", error: null, mocked: false, stopReason: "stop" });
    expect(records[0]?.costUsd).toBeCloseTo(2.8);
  });

  it("prices NVIDIA calls at 0 and joins the base URL without a trailing slash", async () => {
    const { fetch, calls } = stubFetch([completion('{"ok":true}')]);
    const { llm, records } = client(fetch, { provider: "nvidia", model: "meta/llama-3.3-70b-instruct", baseUrl: "https://integrate.api.nvidia.com/v1" });
    const result = await llm.parse(request);
    expect(calls[0]?.url).toBe("https://integrate.api.nvidia.com/v1/chat/completions");
    expect(result.costUsd).toBe(0);
    expect(records[0]).toMatchObject({ model: "meta/llama-3.3-70b-instruct", costUsd: 0 });
  });

  it("retries once in json_object mode with the schema in the system prompt when json_schema gets a 400", async () => {
    const { fetch, calls } = stubFetch([[400, "response_format json_schema not supported"], completion('```json\n{"ok":true}\n```')]);
    const { llm, records } = client(fetch);
    const result = await llm.parse(request);
    expect(result.data).toEqual({ ok: true });
    expect(calls).toHaveLength(2);
    expect(calls[1]?.body.response_format).toEqual({ type: "json_object" });
    const system = (calls[1]?.body.messages as Array<{ content: string }>)[0]?.content ?? "";
    expect(system.startsWith("sys")).toBe(true);
    expect(system).toContain('"ok"');
    expect(records.map((r) => r.error?.split(":")[0] ?? null)).toEqual(["bad_request", null]);
  });

  it("does not retry the json_object fallback more than once", async () => {
    const { fetch, calls } = stubFetch([
      [400, "nope"],
      [400, "still nope"],
    ]);
    const { llm } = client(fetch);
    await expect(llm.parse(request)).rejects.toMatchObject({ kind: "bad_request" });
    expect(calls).toHaveLength(2);
  });

  it("maps a zod validation failure and non-JSON content to parse", async () => {
    for (const content of ['{"ok":"yes"}', "not json at all"]) {
      const { fetch } = stubFetch([completion(content)]);
      const { llm, records } = client(fetch);
      await expect(llm.parse(request)).rejects.toMatchObject({ kind: "parse", retryable: false });
      expect(records[0]?.error).toMatch(/^parse/);
      expect(records[0]?.usage.inputTokens).toBe(1000);
    }
  });

  it("maps refusals, length cut-offs and HTTP statuses to error kinds", async () => {
    const cases: Array<[object | [number, string] | Error, string]> = [
      [completion(null, { refusal: "I can't help with that" }), "refusal"],
      [completion(null, { finish_reason: "content_filter" }), "refusal"],
      [completion('{"ok":', { finish_reason: "length" }), "max_tokens"],
      [[401, "bad key"], "auth"],
      [[429, "slow down"], "rate_limit"],
      [[503, "busy"], "overloaded"],
      [[500, "boom"], "server"],
      [new TypeError("fetch failed"), "connection"],
    ];
    for (const [reply, kind] of cases) {
      const { fetch } = stubFetch([reply]);
      const { llm, records } = client(fetch);
      await expect(llm.parse(request)).rejects.toMatchObject({ kind });
      expect(records).toHaveLength(1);
    }
  });
});
