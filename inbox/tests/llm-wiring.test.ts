import { describe, expect, it } from "vitest";
import { z } from "zod";
import { fallbackProviders, hasRealLlm, readEnv } from "../src/config/env";
import { AnthropicLlmClient, type LlmCallRecord } from "../src/llm/client";
import { createLlmClient } from "../src/llm/factory";
import { FakeLlmClient } from "../src/llm/fake-client";
import { FallbackLlmClient } from "../src/llm/fallback-client";
import { DEFAULT_ROUTES } from "../src/llm/models";
import type { FetchLike } from "../src/llm/openai-compat-client";
import { createServices, defaultOptions } from "../src/services/container";

const base = { INBOX_DATABASE_PATH: ":memory:" };

function sink() {
  const records: LlmCallRecord[] = [];
  return { records, sink: { record: (r: LlmCallRecord) => records.push(r) } };
}

describe("fallback env", () => {
  it("applies the contract defaults", () => {
    const env = readEnv(base);
    expect(env).toMatchObject({
      GEMINI_MODEL: "gemini-2.5-flash",
      GEMINI_BASE_URL: "https://generativelanguage.googleapis.com/v1beta/openai/",
      NVIDIA_MODEL: "meta/llama-3.3-70b-instruct",
      NVIDIA_BASE_URL: "https://integrate.api.nvidia.com/v1",
      LLM_FALLBACK_ORDER: "gemini,nvidia",
    });
  });

  it("keeps the configured order, skips keyless providers, and an empty order disables fallback", () => {
    const both = { ...base, GEMINI_API_KEY: "g", NVIDIA_API_KEY: "n" };
    expect(fallbackProviders(readEnv(both))).toEqual(["gemini", "nvidia"]);
    expect(fallbackProviders(readEnv({ ...both, LLM_FALLBACK_ORDER: " nvidia , gemini " }))).toEqual(["nvidia", "gemini"]);
    expect(fallbackProviders(readEnv({ ...base, NVIDIA_API_KEY: "n" }))).toEqual(["nvidia"]);
    expect(fallbackProviders(readEnv({ ...both, LLM_FALLBACK_ORDER: "" }))).toEqual([]);
    expect(() => fallbackProviders(readEnv({ ...both, LLM_FALLBACK_ORDER: "gemini,openai" }))).toThrow(/openai/);
  });

  it("uses the fake only when no key at all exists (or mock is forced)", () => {
    expect(hasRealLlm(readEnv(base))).toBe(false);
    expect(hasRealLlm(readEnv({ ...base, GEMINI_API_KEY: "g" }))).toBe(true);
    expect(hasRealLlm(readEnv({ ...base, GEMINI_API_KEY: "g", LLM_FALLBACK_ORDER: "" }))).toBe(false);
    expect(hasRealLlm(readEnv({ ...base, GEMINI_API_KEY: "g", INBOX_MOCK_LLM: "1" }))).toBe(false);
    expect(hasRealLlm(readEnv({ ...base, ANTHROPIC_API_KEY: "a" }))).toBe(true);
  });
});

describe("createLlmClient", () => {
  it("returns plain Claude when no fallback key is set", () => {
    const llm = createLlmClient(DEFAULT_ROUTES, sink().sink, readEnv({ ...base, ANTHROPIC_API_KEY: "a" }));
    expect(llm).toBeInstanceOf(AnthropicLlmClient);
  });

  it("puts keyed fallbacks behind Claude in order", () => {
    const env = readEnv({ ...base, ANTHROPIC_API_KEY: "a", GEMINI_API_KEY: "g", NVIDIA_API_KEY: "n", LLM_FALLBACK_ORDER: "nvidia,gemini" });
    const llm = createLlmClient(DEFAULT_ROUTES, sink().sink, env);
    expect(llm).toBeInstanceOf(FallbackLlmClient);
    expect((llm as FallbackLlmClient).providerNames).toEqual(["anthropic", "nvidia", "gemini"]);
  });

  it("with only GEMINI_API_KEY, serves calls from Gemini and records them", async () => {
    const urls: string[] = [];
    const fetch: FetchLike = async (url) => {
      urls.push(url);
      const body = { choices: [{ finish_reason: "stop", message: { content: '{"ok":true}' } }], usage: { prompt_tokens: 10, completion_tokens: 5 } };
      return new Response(JSON.stringify(body), { status: 200 });
    };
    const s = sink();
    const env = readEnv({ ...base, GEMINI_API_KEY: "g" });
    const llm = createLlmClient(DEFAULT_ROUTES, s.sink, env, { fetch });
    expect((llm as FallbackLlmClient).providerNames).toEqual(["gemini"]);
    const result = await llm.parse({ purpose: "classify", system: "s", user: "u", schema: z.object({ ok: z.boolean() }) });
    expect(result).toMatchObject({ data: { ok: true }, model: "gemini-2.5-flash", mocked: false });
    expect(urls).toEqual(["https://generativelanguage.googleapis.com/v1beta/openai/chat/completions"]);
    expect(s.records).toEqual([expect.objectContaining({ model: "gemini-2.5-flash", error: null, mocked: false })]);
  });

  it("the service container goes live with only GEMINI_API_KEY", () => {
    const env = readEnv({ ...base, GEMINI_API_KEY: "g" });
    expect(defaultOptions(env).llm).toBe("real");
    const services = createServices({ ...defaultOptions(env), brain: "fake", embedder: "hashing" });
    expect(services.llm).toBeInstanceOf(FallbackLlmClient);
    expect(services.llm.mocked).toBe(false);
    const offline = createServices({ ...defaultOptions(readEnv(base)), brain: "fake", embedder: "hashing" });
    expect(offline.llm).toBeInstanceOf(FakeLlmClient);
  });
});
