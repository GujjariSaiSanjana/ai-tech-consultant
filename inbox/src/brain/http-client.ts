import type { z } from "zod";
import {
  AskResponseSchema,
  BrainErrorSchema,
  BrainUnavailableError,
  SearchResponseSchema,
  type AskRequest,
  type AskResponse,
  type BrainClient,
  type SearchRequest,
  type SearchResponse,
} from "./schemas";

/** Typed HTTP client for the Knowledge Brain. Never imports Brain code: contract only. */
export class HttpBrainClient implements BrainClient {
  readonly name = "brain-http";

  constructor(
    private readonly baseUrl: string,
    private readonly apiKey: string,
    private readonly timeoutMs: number,
  ) {}

  ask(request: AskRequest): Promise<AskResponse> {
    return this.post("/v1/ask", request, AskResponseSchema);
  }

  search(request: SearchRequest): Promise<SearchResponse> {
    return this.post("/v1/search", request, SearchResponseSchema);
  }

  private async post<T>(path: string, body: unknown, schema: z.ZodType<T>): Promise<T> {
    let response: Response;
    try {
      response = await fetch(new URL(path, this.baseUrl), {
        method: "POST",
        headers: { "content-type": "application/json", "x-api-key": this.apiKey },
        body: JSON.stringify(body),
        signal: AbortSignal.timeout(this.timeoutMs),
      });
    } catch (error) {
      throw new BrainUnavailableError(`Knowledge Brain unreachable at ${this.baseUrl}`, error);
    }
    const json: unknown = await response.json().catch(() => null);
    if (!response.ok) {
      const parsed = BrainErrorSchema.safeParse(json);
      const detail = parsed.success ? `${parsed.data.error.code}: ${parsed.data.error.message}` : `HTTP ${response.status}`;
      throw new BrainUnavailableError(`Knowledge Brain ${path} failed (${detail})`);
    }
    const parsed = schema.safeParse(json);
    if (!parsed.success) throw new BrainUnavailableError(`Knowledge Brain ${path} returned an off-contract body`, parsed.error);
    return parsed.data;
  }
}
