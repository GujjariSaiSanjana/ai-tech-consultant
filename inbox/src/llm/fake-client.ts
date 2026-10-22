import { ZERO_USAGE } from "./cost";
import { LlmError } from "./errors";
import type { LlmCallSink, LlmClient, LlmRequest, LlmResult } from "./client";
import type { LlmPurpose, RoutingTable } from "./models";

export type FakeHandler = (request: LlmRequest<unknown>) => unknown;
export type FakeHandlers = Partial<Record<LlmPurpose, FakeHandler>>;

/**
 * Offline stand-in for the model. Handlers return plain objects which are
 * validated with the request's own Zod schema, so a fake can never produce an
 * output the real structured-output path would reject. Usage and cost are
 * reported as zero: mocked calls must never look like real spend.
 */
export class FakeLlmClient implements LlmClient {
  readonly mocked = true;

  constructor(
    private readonly handlers: FakeHandlers,
    private readonly routes: RoutingTable,
    private readonly sink: LlmCallSink,
  ) {}

  async parse<T>(request: LlmRequest<T>): Promise<LlmResult<T>> {
    const started = Date.now();
    const model = this.routes[request.purpose].model;
    const handler = this.handlers[request.purpose];
    if (!handler) throw new LlmError("unknown", `No fake handler for ${request.purpose}`);
    const raw = handler(request as LlmRequest<unknown>);
    const parsed = request.schema.safeParse(raw);
    const latencyMs = Date.now() - started;
    if (!parsed.success) {
      this.sink.record({ purpose: request.purpose, model, usage: ZERO_USAGE, costUsd: 0, latencyMs, stopReason: null, error: "parse", mocked: true });
      throw new LlmError("parse", `Fake ${request.purpose} output failed validation: ${parsed.error.message}`);
    }
    this.sink.record({ purpose: request.purpose, model, usage: ZERO_USAGE, costUsd: 0, latencyMs, stopReason: "end_turn", error: null, mocked: true });
    return { data: parsed.data, model, usage: ZERO_USAGE, costUsd: 0, latencyMs, mocked: true };
  }
}
