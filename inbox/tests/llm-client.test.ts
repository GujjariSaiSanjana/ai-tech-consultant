import Anthropic from "@anthropic-ai/sdk";
import { describe, expect, it } from "vitest";
import { z } from "zod";
import { AnthropicLlmClient, type LlmCallRecord } from "../src/llm/client";
import { LlmError, toLlmError } from "../src/llm/errors";
import { FakeLlmClient } from "../src/llm/fake-client";
import { DEFAULT_ROUTES } from "../src/llm/models";

const Schema = z.object({ ok: z.boolean() });

interface FakeResponse {
  model: string;
  stop_reason: string;
  stop_details: { category: string | null } | null;
  parsed_output: unknown;
  usage: Partial<Anthropic.Usage> & { input_tokens: number; output_tokens: number };
}

function stubClient(respond: (params: Record<string, unknown>) => FakeResponse | Promise<FakeResponse>) {
  const calls: Array<Record<string, unknown>> = [];
  const client = {
    messages: {
      parse: async (params: Record<string, unknown>) => {
        calls.push(params);
        return respond(params);
      },
    },
  } as unknown as Anthropic;
  return { client, calls };
}

function sink() {
  const records: LlmCallRecord[] = [];
  return { records, sink: { record: (r: LlmCallRecord) => records.push(r) } };
}

const request = { purpose: "classify" as const, system: "sys", user: "u", schema: Schema };

describe("AnthropicLlmClient", () => {
  it("routes, sends no sampling parameters, parses, prices and logs", async () => {
    const { client, calls } = stubClient(() => ({
      model: "claude-haiku-5-5",
      stop_reason: "end_turn",
      stop_details: null,
      parsed_output: { ok: true },
      usage: { input_tokens: 1000, output_tokens: 200, cache_read_input_tokens: 0, cache_creation_input_tokens: 0 },
    }));
    const s = sink();
    const llm = new AnthropicLlmClient(client, DEFAULT_ROUTES, s.sink);
    const result = await llm.parse(request);
    expect(result.data).toEqual({ ok: true });
    expect(result.costUsd).toBeCloseTo((1000 * 0.1 + 200 * 0.5) / 1_000_000);
    const params = calls[0] ?? {};
    expect(params.model).toBe("claude-haiku-5-5");
    for (const forbidden of ["temperature", "top_p", "top_k", "thinking", "tool_choice"]) expect(params).not.toHaveProperty(forbidden);
    expect(params.output_config).toMatchObject({ effort: "low" });
    expect(s.records).toHaveLength(1);
    expect(s.records[0]).toMatchObject({ purpose: "classify", error: null, mocked: false });
  });

  it("turns a refusal into a typed error and still logs the spend", async () => {
    const { client } = stubClient(() => ({
      model: "claude-haiku-5-5",
      stop_reason: "refusal",
      stop_details: { category: "cyber" },
      parsed_output: null,
      usage: { input_tokens: 10, output_tokens: 0 },
    }));
    const s = sink();
    const llm = new AnthropicLlmClient(client, DEFAULT_ROUTES, s.sink);
    await expect(llm.parse(request)).rejects.toMatchObject({ kind: "refusal" });
    expect(s.records[0]?.error).toBe("refusal:cyber");
  });

  it("treats max_tokens and missing output as errors", async () => {
    for (const [stop, kind] of [
      ["max_tokens", "max_tokens"],
      ["end_turn", "parse"],
    ] as const) {
      const { client } = stubClient(() => ({ model: "m", stop_reason: stop, stop_details: null, parsed_output: null, usage: { input_tokens: 1, output_tokens: 1 } }));
      const llm = new AnthropicLlmClient(client, DEFAULT_ROUTES, sink().sink);
      await expect(llm.parse(request)).rejects.toMatchObject({ kind });
    }
  });

  it("maps SDK exceptions to error kinds", async () => {
    const { client } = stubClient(() => {
      throw new Anthropic.APIConnectionError({ message: "down" });
    });
    const s = sink();
    const llm = new AnthropicLlmClient(client, DEFAULT_ROUTES, s.sink);
    await expect(llm.parse(request)).rejects.toMatchObject({ kind: "connection", retryable: true });
    expect(s.records[0]?.error).toContain("connection");
  });
});

describe("toLlmError", () => {
  it("keeps LlmError and classifies unknown errors", () => {
    const e = new LlmError("auth", "x");
    expect(toLlmError(e)).toBe(e);
    expect(toLlmError(new Error("boom")).kind).toBe("unknown");
    expect(toLlmError(new Anthropic.AnthropicError("bad json")).kind).toBe("parse");
  });
});

describe("FakeLlmClient", () => {
  it("validates handler output with the request schema and reports zero cost", async () => {
    const s = sink();
    const good = new FakeLlmClient({ classify: () => ({ ok: true }) }, DEFAULT_ROUTES, s.sink);
    const result = await good.parse(request);
    expect(result).toMatchObject({ data: { ok: true }, costUsd: 0, mocked: true });
    const bad = new FakeLlmClient({ classify: () => ({ ok: "yes" }) }, DEFAULT_ROUTES, s.sink);
    await expect(bad.parse(request)).rejects.toMatchObject({ kind: "parse" });
    expect(s.records.every((r) => r.mocked)).toBe(true);
  });
});
