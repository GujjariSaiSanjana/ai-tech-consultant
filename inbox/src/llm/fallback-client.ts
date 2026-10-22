import type { LlmClient, LlmRequest, LlmResult } from "./client";
import { LlmError } from "./errors";

/**
 * Whether a primary (Claude) failure may be retried on a fallback provider.
 * Only transient trouble qualifies: rate limits, overload/5xx, network and
 * timeouts (`LlmError.retryable`), and only after the SDK's own retries.
 * Refusal, max_tokens, parse, auth and bad-request errors never fall back: a
 * refusal must stay a refusal because the guardrail and trust ladder rely on it.
 */
export function shouldFallBack(error: unknown): error is LlmError {
  return error instanceof LlmError && error.retryable;
}

export interface NamedLlmClient {
  name: string;
  client: LlmClient;
}

/**
 * Claude first, then each fallback in order. `primary` is null when there is no
 * ANTHROPIC_API_KEY, in which case the fallbacks are the only providers.
 * Providers without a key are never constructed, so they are skipped by the
 * caller. When every fallback fails the original error is rethrown, except
 * that a fallback refusal is surfaced as-is (a refusal is never retried away).
 */
export class FallbackLlmClient implements LlmClient {
  readonly mocked = false;

  constructor(
    private readonly primary: LlmClient | null,
    private readonly fallbacks: readonly NamedLlmClient[],
  ) {
    if (!primary && fallbacks.length === 0) throw new Error("FallbackLlmClient needs a primary or at least one fallback");
  }

  get providerNames(): string[] {
    return [...(this.primary ? ["anthropic"] : []), ...this.fallbacks.map((f) => f.name)];
  }

  async parse<T>(request: LlmRequest<T>): Promise<LlmResult<T>> {
    let original: unknown = null;
    if (this.primary) {
      try {
        return await this.primary.parse(request);
      } catch (error) {
        if (!shouldFallBack(error)) throw error;
        original = error;
      }
    }

    for (const { client } of this.fallbacks) {
      try {
        return await client.parse(request);
      } catch (error) {
        if (error instanceof LlmError && error.kind === "refusal") throw error;
        original ??= error;
      }
    }
    throw original;
  }
}
