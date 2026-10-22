import { z } from "zod";

/** Zod mirror of docs/contracts/brain-api.md (v1). Only the endpoints the inbox uses. */
export const BrainLabelSchema = z.enum(["setting", "addon", "widget_customization", "custom_dev", "unknown"]);

export const CitationSchema = z.object({
  article_id: z.string(),
  title: z.string(),
  url: z.string(),
  quote: z.string(),
  image_url: z.string().nullable(),
});
export type Citation = z.infer<typeof CitationSchema>;

export const AskRequestSchema = z.object({
  question: z.string(),
  top_k: z.number().int().optional(),
  category: z.string().nullable().optional(),
});
export type AskRequest = z.infer<typeof AskRequestSchema>;

export const AskResponseSchema = z.object({
  status: z.enum(["answered", "no_answer"]),
  answer: z.string(),
  label: BrainLabelSchema,
  setting_path: z.string().nullable(),
  warnings: z.array(z.string()),
  citations: z.array(CitationSchema),
  confidence: z.number(),
  gap_ticket_id: z.string().nullable(),
  model: z.string(),
  usage: z.object({
    input_tokens: z.number(),
    output_tokens: z.number(),
    cost_usd: z.number(),
    latency_ms: z.number(),
  }),
});
export type AskResponse = z.infer<typeof AskResponseSchema>;

export const SearchRequestSchema = z.object({
  query: z.string(),
  top_k: z.number().int().optional(),
  category: z.string().nullable().optional(),
});
export type SearchRequest = z.infer<typeof SearchRequestSchema>;

export const SearchResultSchema = z.object({
  chunk_id: z.string(),
  article_id: z.string(),
  title: z.string(),
  url: z.string(),
  category: z.string(),
  folder: z.string(),
  kind: z.enum(["text", "image", "expert"]),
  text: z.string(),
  score: z.number(),
});
export type SearchResult = z.infer<typeof SearchResultSchema>;

export const SearchResponseSchema = z.object({ results: z.array(SearchResultSchema) });
export type SearchResponse = z.infer<typeof SearchResponseSchema>;

export const BrainErrorSchema = z.object({ error: z.object({ code: z.string(), message: z.string() }) });

/** The two read operations the inbox needs. HTTP, MCP and fake implementations share it. */
export interface BrainClient {
  readonly name: string;
  ask(request: AskRequest): Promise<AskResponse>;
  search(request: SearchRequest): Promise<SearchResponse>;
}

export class BrainUnavailableError extends Error {
  constructor(message: string, override readonly cause?: unknown) {
    super(message);
    this.name = "BrainUnavailableError";
  }
}
