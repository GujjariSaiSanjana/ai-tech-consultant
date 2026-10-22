import { z } from "zod";
import { getServices } from "../../../../src/services/container";
import { handle } from "../../../../src/ui/http";

export const dynamic = "force-dynamic";

/** Global kill switch: when on, nothing is sent without a person. */
export async function POST(request: Request) {
  return handle(request, z.object({ on: z.boolean() }), async ({ on }) => {
    getServices().agent.setKillSwitch(on);
    return { killSwitch: on };
  });
}
