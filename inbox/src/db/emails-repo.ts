import { z } from "zod";
import { GoldLabelsSchema, SplitSchema, type EmailInput, type GoldLabels, type Split } from "../domain/schemas";
import { bool, json, type Db } from "./database";
import { boolColumn, jsonColumn } from "./json-columns";

export const EMAIL_STATUSES = ["new", "drafted", "sent", "auto_sent", "rejected"] as const;
export type EmailStatus = (typeof EMAIL_STATUSES)[number];

const EmailRowSchema = z.object({
  id: z.string(),
  from_name: z.string(),
  from_email: z.string(),
  subject: z.string(),
  body: z.string(),
  received_at: z.string(),
  split: SplitSchema.nullable(),
  tags_json: jsonColumn(z.array(z.string())),
  gold_json: z.string().nullable().transform((t) => (t === null ? null : GoldLabelsSchema.parse(JSON.parse(t)))),
  simulated: boolColumn,
  status: z.enum(EMAIL_STATUSES),
});
export type EmailRow = z.infer<typeof EmailRowSchema>;

export interface NewEmail extends EmailInput {
  split: Split | null;
  tags: string[];
  gold: GoldLabels | null;
  simulated: boolean;
}

export class EmailsRepo {
  constructor(private readonly db: Db) {}

  insert(email: NewEmail): void {
    this.db.run(
      `INSERT INTO emails (id, from_name, from_email, subject, body, received_at, split, tags_json, gold_json, simulated, status)
       VALUES (:id, :from_name, :from_email, :subject, :body, :received_at, :split, :tags, :gold, :simulated, 'new')`,
      {
        id: email.id,
        from_name: email.from_name,
        from_email: email.from_email,
        subject: email.subject,
        body: email.body,
        received_at: email.received_at,
        split: email.split,
        tags: json(email.tags),
        gold: email.gold ? json(email.gold) : null,
        simulated: bool(email.simulated),
      },
    );
  }

  get(id: string): EmailRow | null {
    return this.db.get(EmailRowSchema, "SELECT * FROM emails WHERE id = :id", { id });
  }

  listByStatus(status: EmailStatus): EmailRow[] {
    return this.db.all(EmailRowSchema, "SELECT * FROM emails WHERE status = :status ORDER BY received_at", { status });
  }

  listAll(): EmailRow[] {
    return this.db.all(EmailRowSchema, "SELECT * FROM emails ORDER BY received_at DESC");
  }

  setStatus(id: string, status: EmailStatus): void {
    this.db.run("UPDATE emails SET status = :status WHERE id = :id", { id, status });
  }

  count(): number {
    return this.db.get(z.object({ n: z.number() }), "SELECT COUNT(*) AS n FROM emails")?.n ?? 0;
  }
}
