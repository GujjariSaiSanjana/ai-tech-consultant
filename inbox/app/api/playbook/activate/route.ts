import { z } from "zod";
import { getServices } from "../../../../src/services/container";
import { handle } from "../../../../src/ui/http";

export const dynamic = "force-dynamic";

/** One-click rollback (or roll forward): make a stored playbook version the active one. */
export async function POST(request: Request) {
  return handle(request, z.object({ versionId: z.number().int() }), async ({ versionId }) => {
    getServices().agent.rollback(versionId);
    return { active: versionId };
  });
}
