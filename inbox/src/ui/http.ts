import { NextResponse } from "next/server";
import type { z } from "zod";
import { isInputError } from "../services/agent";

/** Parses a JSON body with a schema and maps known errors to 400s. */
export async function handle<T>(request: Request, schema: z.ZodType<T>, run: (body: T) => Promise<unknown>): Promise<NextResponse> {
  let raw: unknown = {};
  try {
    const text = await request.text();
    raw = text.length > 0 ? JSON.parse(text) : {};
  } catch {
    return NextResponse.json({ error: { code: "bad_json", message: "Body must be JSON" } }, { status: 400 });
  }
  const parsed = schema.safeParse(raw);
  if (!parsed.success) {
    return NextResponse.json({ error: { code: "invalid_body", message: parsed.error.message } }, { status: 400 });
  }
  try {
    return NextResponse.json(await run(parsed.data));
  } catch (error) {
    if (isInputError(error)) {
      return NextResponse.json({ error: { code: "invalid_request", message: error.message } }, { status: 400 });
    }
    console.error(error);
    return NextResponse.json({ error: { code: "internal", message: "Something went wrong" } }, { status: 500 });
  }
}
