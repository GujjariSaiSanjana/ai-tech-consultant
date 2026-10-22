import { z } from "zod";
import { InputError } from "../../../../../src/services/agent";
import { getServices } from "../../../../../src/services/container";
import { handle } from "../../../../../src/ui/http";

export const dynamic = "force-dynamic";

const BodySchema = z.object({
  action: z.enum(["send", "edit_send", "rewrite", "reject", "auto_rewrite"]),
  text: z.string().max(20_000).optional(),
});

/** Records what the person did with a draft; this is what the agent learns from. */
export async function POST(request: Request, context: { params: Promise<{ id: string }> }) {
  const { id } = await context.params;
  const draftId = Number(id);
  return handle(request, BodySchema, async (body) => {
    if (!Number.isInteger(draftId)) throw new InputError("Draft id must be a number");
    return getServices().agent.recordAction({ draftId, action: body.action, ...(body.text !== undefined ? { text: body.text } : {}) });
  });
}
