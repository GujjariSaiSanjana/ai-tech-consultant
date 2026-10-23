import { describe, expect, it } from "vitest";
import { z } from "zod";
import type { LlmClient, LlmRequest, LlmResult } from "../src/llm/client";
import { ZERO_USAGE } from "../src/llm/cost";
import { LlmError, type LlmErrorKind } from "../src/llm/errors";
import { FallbackLlmClient, shouldFallBack } from "../src/llm/fallback-client";

const request = { purpose: "draft" as const, system: "sys", user: "u", schema: z.object({ ok: z.boolean() }) };

/** A client that either answers as `model` or throws an LlmError of `kind`, and counts its calls. */
function stub(model: string, fail?: LlmErrorKind) {
  const state = { calls: 0 };
  const client: LlmClient = {
    mocked: false,
    async parse<T>(_req: LlmRequest<T>): Promise<LlmResult<T>> {
      state.calls += 1;
      if (fail) throw new LlmError(fail, `${model} ${fail}`);
      return { data: { ok: true } as T, model, usage: ZERO_USAGE, costUsd: 0, latencyMs: 1, mocked: false };
    },
  };
  return Object.assign(state, { client });
}

describe("shouldFallBack", () => {
  it("is true only for retryable kinds", () => {
    for (const kind of ["rate_limit", "overloaded", "server", "connection"] as const) expect(shouldFallBack(new LlmError(kind, "x"))).toBe(true);
    for (const kind of ["refusal", "max_tokens", "parse", "auth", "permission", "bad_request", "not_found", "unknown"] as const) {
      expect(shouldFallBack(new LlmError(kind, "x"))).toBe(false);
    }
    expect(shouldFallBack(new Error("plain"))).toBe(false);
  });
});

describe("FallbackLlmClient", () => {
  it("uses Claude when it works and never touches the fallbacks", async () => {
    const claude = stub("claude-sonnet-5-5");
    const gemini = stub("gemini-2.5-flash");
    const llm = new FallbackLlmClient(claude.client, [{ name: "gemini", client: gemini.client }]);
    await expect(llm.parse(request)).resolves.toMatchObject({ model: "claude-sonnet-5-5" });
    expect(gemini.calls).toBe(0);
  });

  it.each(["rate_limit", "overloaded", "server", "connection"] as const)("falls back when Claude fails with %s", async (kind) => {
    const claude = stub("claude-sonnet-5-5", kind);
    const gemini = stub("gemini-2.5-flash");
    const llm = new FallbackLlmClient(claude.client, [{ name: "gemini", client: gemini.client }]);
    await expect(llm.parse(request)).resolves.toMatchObject({ model: "gemini-2.5-flash" });
    expect(claude.calls).toBe(1);
  });

  it.each(["refusal", "max_tokens", "parse", "auth", "bad_request"] as const)("does not fall back on %s", async (kind) => {
    const claude = stub("claude-sonnet-5-5", kind);
    const gemini = stub("gemini-2.5-flash");
    const llm = new FallbackLlmClient(claude.client, [{ name: "gemini", client: gemini.client }]);
    await expect(llm.parse(request)).rejects.toMatchObject({ kind });
    expect(gemini.calls).toBe(0);
  });

  it("tries fallbacks in order and stops at the first that answers", async () => {
    const claude = stub("claude-sonnet-5-5", "overloaded");
    const gemini = stub("gemini-2.5-flash", "rate_limit");
    const nvidia = stub("openai/gpt-oss-20b");
    const llm = new FallbackLlmClient(claude.client, [
      { name: "gemini", client: gemini.client },
      { name: "nvidia", client: nvidia.client },
    ]);
    await expect(llm.parse(request)).resolves.toMatchObject({ model: "openai/gpt-oss-20b" });
    expect([claude.calls, gemini.calls, nvidia.calls]).toEqual([1, 1, 1]);
  });

  it("rethrows the original Claude error when every fallback fails", async () => {
    const claude = stub("claude-sonnet-5-5", "overloaded");
    const gemini = stub("gemini-2.5-flash", "parse");
    const nvidia = stub("openai/gpt-oss-20b", "server");
    const llm = new FallbackLlmClient(claude.client, [
      { name: "gemini", client: gemini.client },
      { name: "nvidia", client: nvidia.client },
    ]);
    await expect(llm.parse(request)).rejects.toMatchObject({ kind: "overloaded", message: "claude-sonnet-5-5 overloaded" });
    expect([gemini.calls, nvidia.calls]).toEqual([1, 1]);
  });

  it("surfaces a fallback refusal instead of trying the next provider", async () => {
    const claude = stub("claude-sonnet-5-5", "rate_limit");
    const gemini = stub("gemini-2.5-flash", "refusal");
    const nvidia = stub("openai/gpt-oss-20b");
    const llm = new FallbackLlmClient(claude.client, [
      { name: "gemini", client: gemini.client },
      { name: "nvidia", client: nvidia.client },
    ]);
    await expect(llm.parse(request)).rejects.toMatchObject({ kind: "refusal" });
    expect(nvidia.calls).toBe(0);
  });

  it("runs on fallbacks alone when there is no Claude key", async () => {
    const gemini = stub("gemini-2.5-flash", "server");
    const nvidia = stub("openai/gpt-oss-20b");
    const llm = new FallbackLlmClient(null, [
      { name: "gemini", client: gemini.client },
      { name: "nvidia", client: nvidia.client },
    ]);
    expect(llm.providerNames).toEqual(["gemini", "nvidia"]);
    await expect(llm.parse(request)).resolves.toMatchObject({ model: "openai/gpt-oss-20b" });
    const only = new FallbackLlmClient(null, [{ name: "gemini", client: stub("gemini-2.5-flash", "server").client }]);
    await expect(only.parse(request)).rejects.toMatchObject({ kind: "server" });
  });

  it("refuses to be built with no provider at all", () => {
    expect(() => new FallbackLlmClient(null, [])).toThrow();
  });
});
