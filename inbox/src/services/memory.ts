import type { ActionsRepo } from "../db/actions-repo";
import type { Intent } from "../domain/schemas";
import type { Embedder } from "../embeddings/embedder";
import { cosineSimilarity } from "../learning/compare";
import type { ExampleMemory, RetrievedExample } from "../pipeline/run";

/** Layer A: k nearest stored (redacted) sent replies within the same intent. */
export class DbExampleMemory implements ExampleMemory {
  constructor(
    private readonly actions: ActionsRepo,
    private readonly embedder: Embedder,
  ) {}

  async nearest(intent: Intent, emailText: string, k: number): Promise<RetrievedExample[]> {
    const candidates = this.actions.examplesForIntent(intent, this.embedder.id);
    if (candidates.length === 0 || k <= 0) return [];
    const [query] = await this.embedder.embed([emailText]);
    if (!query) return [];
    return candidates
      .map((c) => ({ c, sim: c.embedding_json.length === query.length ? cosineSimilarity(query, c.embedding_json) : -1 }))
      .filter((x) => x.sim >= 0)
      .sort((a, b) => b.sim - a.sim || b.c.id - a.c.id)
      .slice(0, k)
      .map(({ c }) => ({ id: c.id, email: c.email_text, reply: c.reply_text }));
  }
}
