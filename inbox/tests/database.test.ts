import { mkdtempSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { afterEach, describe, expect, it } from "vitest";
import { z } from "zod";
import { Db, resetDatabase } from "../src/db/database";

const Count = z.object({ n: z.number() });

describe("resetDatabase", () => {
  const dirs: string[] = [];
  afterEach(() => {
    for (const dir of dirs.splice(0)) rmSync(dir, { recursive: true, force: true });
  });

  it("empties the file in place so a connection that is already open sees the reset", () => {
    const dir = mkdtempSync(join(tmpdir(), "inbox-db-"));
    dirs.push(dir);
    const path = join(dir, "inbox.db");
    const server = new Db(path);
    server.run("INSERT INTO emails (id, from_name, from_email, subject, body, received_at) VALUES ('e1', 'A', 'a@b.c', 's', 'b', 'now')");

    resetDatabase(path);
    const reseeded = new Db(path);
    reseeded.close();

    expect(server.get(Count, "SELECT count(*) AS n FROM emails")?.n).toBe(0);
    server.close();
  });
});
