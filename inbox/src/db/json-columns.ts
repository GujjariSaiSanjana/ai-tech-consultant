import { z } from "zod";

/** A TEXT column holding JSON, parsed and validated with the given schema. */
export function jsonColumn<T>(schema: z.ZodType<T>) {
  return z.string().transform((text, ctx): T => {
    let value: unknown;
    try {
      value = JSON.parse(text);
    } catch {
      ctx.addIssue({ code: "custom", message: "invalid JSON column" });
      return z.NEVER;
    }
    const parsed = schema.safeParse(value);
    if (!parsed.success) {
      ctx.addIssue({ code: "custom", message: parsed.error.message });
      return z.NEVER;
    }
    return parsed.data;
  });
}

export const boolColumn = z.number().transform((n) => n === 1);
export const nullableBoolColumn = z.number().nullable().transform((n) => (n === null ? null : n === 1));
