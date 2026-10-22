import { z } from "zod";
import { getServices } from "../../../src/services/container";
import { handle } from "../../../src/ui/http";

export const dynamic = "force-dynamic";

/** Re-scores the active playbook; a drop below its promotion score pauses all auto-sending. */
export async function POST(request: Request) {
  return handle(request, z.object({}), async () => getServices().agent.qualityCheck(`ui-${Date.now()}`));
}
