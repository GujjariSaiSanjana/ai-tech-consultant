import { createServer, type Server } from "node:http";
import type { AddressInfo } from "node:net";
import { afterAll, beforeAll, describe, expect, it } from "vitest";
import { FakeBrainClient } from "../src/brain/fake-client";
import { HttpBrainClient } from "../src/brain/http-client";
import { BrainUnavailableError, type AskResponse } from "../src/brain/schemas";
import { retrieveFacts } from "../src/pipeline/retrieve";

const ASK: AskResponse = {
  status: "answered",
  answer: "Open Settings > Payment Settings and choose Stripe.",
  label: "setting",
  setting_path: "Settings > Payment Settings",
  warnings: [],
  citations: [{ article_id: "1", title: "Stripe", url: "https://help.example/1", quote: "Choose Stripe and paste your keys.", image_url: null }],
  confidence: 0.9,
  gap_ticket_id: null,
  model: "brain-model",
  usage: { input_tokens: 1, output_tokens: 1, cost_usd: 0, latency_ms: 1 },
};

let server: Server;
let baseUrl = "";
const seenKeys: string[] = [];

beforeAll(async () => {
  server = createServer((req, res) => {
    seenKeys.push(String(req.headers["x-api-key"]));
    res.setHeader("content-type", "application/json");
    if (req.url === "/v1/ask") return res.end(JSON.stringify(ASK));
    if (req.url === "/v1/search") return res.end(JSON.stringify({ results: [{ nope: true }] }));
    res.statusCode = 404;
    res.end(JSON.stringify({ error: { code: "not_found", message: "no" } }));
  });
  await new Promise<void>((resolve) => server.listen(0, "127.0.0.1", resolve));
  baseUrl = `http://127.0.0.1:${(server.address() as AddressInfo).port}`;
});

afterAll(() => {
  server.close();
});

const email = { id: "e", from_name: "Ana Lee", from_email: "ana@x.example", subject: "Stripe", body: "How do I connect Stripe?", received_at: "t" };
const classification = { intent: "how_to" as const, confidence: 0.9, customer_type: "existing" as const, risk_class: "none" as const };

describe("HttpBrainClient", () => {
  it("sends the API key and validates the ask contract", async () => {
    const brain = new HttpBrainClient(baseUrl, "secret", 2000);
    const answer = await brain.ask({ question: "q" });
    expect(answer.setting_path).toBe("Settings > Payment Settings");
    expect(seenKeys).toContain("secret");
  });

  it("rejects off-contract bodies", async () => {
    const brain = new HttpBrainClient(baseUrl, "secret", 2000);
    await expect(brain.search({ query: "q" })).rejects.toBeInstanceOf(BrainUnavailableError);
  });

  it("reports an unreachable Brain as unavailable", async () => {
    const brain = new HttpBrainClient("http://127.0.0.1:9", "k", 500);
    await expect(brain.ask({ question: "q" })).rejects.toBeInstanceOf(BrainUnavailableError);
  });
});

describe("retrieveFacts", () => {
  it("numbers facts from ask and keeps going when search fails", async () => {
    const r = await retrieveFacts(new HttpBrainClient(baseUrl, "k", 2000), email, classification);
    expect(r.status).toBe("ok");
    expect(r.facts.map((f) => f.id)).toEqual(["F1", "F2"]);
    expect(r.facts[0]?.text).toContain("Setting path: Settings > Payment Settings.");
    expect(r.error).toContain("off-contract");
  });

  it("marks needs_facts when the Brain is down, never inventing facts", async () => {
    const r = await retrieveFacts(new HttpBrainClient("http://127.0.0.1:9", "k", 500), email, classification);
    expect(r.status).toBe("needs_facts");
    expect(r.facts).toEqual([]);
    expect(r.error).not.toBeNull();
  });

  it("skips lookups for spam", async () => {
    const r = await retrieveFacts(new FakeBrainClient(false), email, { ...classification, intent: "spam" });
    expect(r).toMatchObject({ status: "ok", facts: [] });
  });

  it("the fake Brain answers known topics and opens a gap for unknown ones", async () => {
    const brain = new FakeBrainClient();
    expect((await brain.ask({ question: "connect stripe payment gateway keys" })).status).toBe("answered");
    const gap = await brain.ask({ question: "zebra quantum lasagna" });
    expect(gap.status).toBe("no_answer");
    expect(gap.gap_ticket_id).not.toBeNull();
  });
});
