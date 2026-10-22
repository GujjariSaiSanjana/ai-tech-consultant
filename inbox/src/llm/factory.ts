import { fallbackProviders, type Env } from "../config/env";
import { createAnthropicClient, type LlmCallSink, type LlmClient } from "./client";
import { FallbackLlmClient, type NamedLlmClient } from "./fallback-client";
import type { FallbackProvider, RoutingTable } from "./models";
import { OpenAICompatLlmClient, type FetchLike, type OpenAICompatConfig } from "./openai-compat-client";

function providerConfig(provider: FallbackProvider, env: Env, fetch?: FetchLike): OpenAICompatConfig | null {
  const fields =
    provider === "gemini"
      ? { apiKey: env.GEMINI_API_KEY, model: env.GEMINI_MODEL, baseUrl: env.GEMINI_BASE_URL }
      : { apiKey: env.NVIDIA_API_KEY, model: env.NVIDIA_MODEL, baseUrl: env.NVIDIA_BASE_URL };
  if (!fields.apiKey) return null;
  return { provider, apiKey: fields.apiKey, model: fields.model, baseUrl: fields.baseUrl, ...(fetch ? { fetch } : {}) };
}

/**
 * Builds the real model client from the environment: Claude when ANTHROPIC_API_KEY
 * is set, with keyed fallback providers behind it in LLM_FALLBACK_ORDER. Plain
 * Claude (no wrapper) when no fallback is configured. Callers pick the offline
 * fake only when `hasRealLlm(env)` is false.
 */
export function createLlmClient(routes: RoutingTable, sink: LlmCallSink, env: Env, options: { fetch?: FetchLike } = {}): LlmClient {
  const primary = env.ANTHROPIC_API_KEY ? createAnthropicClient(routes, sink, env.ANTHROPIC_API_KEY) : null;
  const fallbacks: NamedLlmClient[] = fallbackProviders(env).flatMap((provider) => {
    const config = providerConfig(provider, env, options.fetch);
    return config ? [{ name: provider, client: new OpenAICompatLlmClient(config, routes, sink) }] : [];
  });
  if (primary && fallbacks.length === 0) return primary;
  return new FallbackLlmClient(primary, fallbacks);
}
