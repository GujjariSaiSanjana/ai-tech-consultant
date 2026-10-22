import { z } from "zod";

/** Provider interface so the similarity signal can be swapped without touching callers. */
export interface Embedder {
  /** Stored alongside every vector; vectors from different embedders are never compared. */
  readonly id: string;
  embed(texts: readonly string[]): Promise<number[][]>;
}

const FNV_OFFSET = 0x811c9dc5;
const FNV_PRIME = 0x01000193;

function fnv1a(text: string): number {
  let hash = FNV_OFFSET;
  for (let i = 0; i < text.length; i++) {
    hash ^= text.charCodeAt(i);
    hash = Math.imul(hash, FNV_PRIME);
  }
  return hash >>> 0;
}

function tokens(text: string): string[] {
  return text.toLowerCase().match(/[\p{L}\p{N}]+/gu) ?? [];
}

/**
 * Deterministic, offline feature-hashing embedder (unigrams + bigrams, signed
 * hashing, L2-normalised). Good enough to tell "same reply" from "different
 * reply" and to rank examples in tests and demos; not a semantic model.
 */
export class HashingEmbedder implements Embedder {
  readonly id: string;

  constructor(private readonly dimensions = 512) {
    this.id = `hashing-${dimensions}`;
  }

  async embed(texts: readonly string[]): Promise<number[][]> {
    return texts.map((t) => this.embedOne(t));
  }

  embedOne(text: string): number[] {
    const vector = new Array<number>(this.dimensions).fill(0);
    const words = tokens(text);
    const features = [...words, ...words.slice(1).map((w, i) => `${words[i]}_${w}`)];
    for (const feature of features) {
      const h = fnv1a(feature);
      const index = h % this.dimensions;
      vector[index] = (vector[index] ?? 0) + ((h >>> 31) === 1 ? -1 : 1);
    }
    const norm = Math.sqrt(vector.reduce((s, v) => s + v * v, 0));
    return norm === 0 ? vector : vector.map((v) => v / norm);
  }
}

const VoyageResponseSchema = z.object({
  data: z.array(z.object({ embedding: z.array(z.number()), index: z.number() })),
});

/** Voyage AI embeddings over HTTP, used when VOYAGE_API_KEY is set. */
export class VoyageEmbedder implements Embedder {
  readonly id: string;

  constructor(
    private readonly apiKey: string,
    private readonly model: string,
  ) {
    this.id = `voyage-${model}`;
  }

  async embed(texts: readonly string[]): Promise<number[][]> {
    if (texts.length === 0) return [];
    const response = await fetch("https://api.voyageai.com/v1/embeddings", {
      method: "POST",
      headers: { "content-type": "application/json", authorization: `Bearer ${this.apiKey}` },
      body: JSON.stringify({ input: texts, model: this.model }),
      signal: AbortSignal.timeout(20_000),
    });
    if (!response.ok) throw new Error(`Voyage embeddings failed with HTTP ${response.status}`);
    const parsed = VoyageResponseSchema.parse(await response.json());
    return [...parsed.data].sort((a, b) => a.index - b.index).map((d) => d.embedding);
  }
}
