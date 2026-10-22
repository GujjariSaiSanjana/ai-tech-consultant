import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { DatasetEmailSchema, type DatasetEmail, type Split } from "../domain/schemas";

export const DATASET_PATH = resolve(process.cwd(), "data/emails.jsonl");

/** Loads and validates the labelled email set. Throws with the line number on any bad row. */
export function loadDataset(path: string = DATASET_PATH): DatasetEmail[] {
  return readFileSync(path, "utf8")
    .split("\n")
    .map((line, i) => ({ line: line.trim(), n: i + 1 }))
    .filter(({ line }) => line.length > 0)
    .map(({ line, n }) => {
      const parsed = DatasetEmailSchema.safeParse(JSON.parse(line));
      if (!parsed.success) throw new Error(`${path}:${n}: ${parsed.error.message}`);
      return parsed.data;
    });
}

export function bySplit(emails: readonly DatasetEmail[], split: Split): DatasetEmail[] {
  return emails.filter((e) => e.split === split);
}
