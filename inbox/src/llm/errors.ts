import Anthropic from "@anthropic-ai/sdk";

export type LlmErrorKind =
  | "refusal"
  | "max_tokens"
  | "parse"
  | "rate_limit"
  | "overloaded"
  | "auth"
  | "permission"
  | "not_found"
  | "bad_request"
  | "server"
  | "connection"
  | "unknown";

const RETRYABLE: ReadonlySet<LlmErrorKind> = new Set<LlmErrorKind>(["rate_limit", "overloaded", "server", "connection"]);

export class LlmError extends Error {
  readonly kind: LlmErrorKind;
  readonly retryable: boolean;
  override readonly cause: unknown;

  constructor(kind: LlmErrorKind, message: string, cause?: unknown) {
    super(message);
    this.name = "LlmError";
    this.kind = kind;
    this.retryable = RETRYABLE.has(kind);
    this.cause = cause;
  }
}

/** Maps SDK exceptions to our error kinds, most specific class first. */
export function toLlmError(error: unknown): LlmError {
  if (error instanceof LlmError) return error;
  if (error instanceof Anthropic.AuthenticationError) return new LlmError("auth", "Invalid or missing Anthropic API key", error);
  if (error instanceof Anthropic.PermissionDeniedError) return new LlmError("permission", error.message, error);
  if (error instanceof Anthropic.NotFoundError) return new LlmError("not_found", error.message, error);
  if (error instanceof Anthropic.BadRequestError) return new LlmError("bad_request", error.message, error);
  if (error instanceof Anthropic.RateLimitError) return new LlmError("rate_limit", error.message, error);
  if (error instanceof Anthropic.InternalServerError) {
    return new LlmError(error.status === 529 ? "overloaded" : "server", error.message, error);
  }
  if (error instanceof Anthropic.APIConnectionError) return new LlmError("connection", error.message, error);
  if (error instanceof Anthropic.APIError) return new LlmError("unknown", error.message, error);
  if (error instanceof Anthropic.AnthropicError) return new LlmError("parse", error.message, error);
  return new LlmError("unknown", error instanceof Error ? error.message : String(error), error);
}
