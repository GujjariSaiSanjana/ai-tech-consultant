import { z } from "zod";
import { getServices } from "../../../src/services/container";
import { handle } from "../../../src/ui/http";

export const dynamic = "force-dynamic";

/** Runs the pipeline on every email that has no draft yet. */
export async function POST(request: Request) {
  return handle(request, z.object({}), async () => ({ processed: await getServices().agent.processAllNew() }));
}
