import { z } from "zod";
import { EmailInputSchema } from "../../../src/domain/schemas";
import { getServices } from "../../../src/services/container";
import { handle } from "../../../src/ui/http";

export const dynamic = "force-dynamic";

/** Ingests one inbound email (e.g. from a mail webhook) and drafts a reply for it. */
export async function POST(request: Request) {
  return handle(request, EmailInputSchema.extend({ process: z.boolean().default(true) }), async (body) => {
    const { agent } = getServices();
    agent.ingest({ ...body, split: null, tags: [], gold: null, simulated: false });
    return body.process ? agent.processEmail(body.id) : { ingested: body.id };
  });
}
