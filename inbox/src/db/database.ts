import { mkdirSync } from "node:fs";
import { dirname } from "node:path";
import { DatabaseSync, type SQLInputValue } from "node:sqlite";
import type { z } from "zod";
import { SCHEMA_SQL } from "./schema";

export type Params = Record<string, SQLInputValue>;

/** Thin typed wrapper over node:sqlite: every row read is validated with a Zod schema. */
export class Db {
  readonly raw: DatabaseSync;

  constructor(path: string) {
    if (path !== ":memory:") mkdirSync(dirname(path), { recursive: true });
    this.raw = new DatabaseSync(path);
    this.raw.exec("PRAGMA journal_mode = WAL; PRAGMA foreign_keys = ON; PRAGMA busy_timeout = 5000;");
    this.raw.exec(SCHEMA_SQL);
  }

  all<T>(schema: z.ZodType<T>, sql: string, params: Params = {}): T[] {
    return this.raw
      .prepare(sql)
      .all(params)
      .map((row) => schema.parse(row));
  }

  get<T>(schema: z.ZodType<T>, sql: string, params: Params = {}): T | null {
    const row = this.raw.prepare(sql).get(params);
    return row === undefined ? null : schema.parse(row);
  }

  run(sql: string, params: Params = {}): { changes: number; lastInsertRowid: number } {
    const result = this.raw.prepare(sql).run(params);
    return { changes: Number(result.changes), lastInsertRowid: Number(result.lastInsertRowid) };
  }

  transaction<T>(fn: () => T): T {
    this.raw.exec("BEGIN IMMEDIATE");
    try {
      const value = fn();
      this.raw.exec("COMMIT");
      return value;
    } catch (error) {
      this.raw.exec("ROLLBACK");
      throw error;
    }
  }

  close(): void {
    this.raw.close();
  }
}

export function json(value: unknown): string {
  return JSON.stringify(value);
}

export function bool(value: boolean): number {
  return value ? 1 : 0;
}
