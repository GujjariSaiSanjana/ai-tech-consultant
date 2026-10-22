import { describe, expect, it } from "vitest";
import { redactPii } from "../src/privacy/redact";

describe("redactPii", () => {
  it("removes cards, ids, emails, phones, secrets and the sender's name", () => {
    const text =
      "Tony Russo here. Card 4111 1111 1111 1111, SSN 123-45-6789, mail tony@russo.com, call +1 415-555-0100, password: Summer2026!";
    const out = redactPii(text, ["Tony Russo"]);
    expect(out).not.toContain("4111");
    expect(out).not.toContain("123-45-6789");
    expect(out).not.toContain("tony@russo.com");
    expect(out).not.toContain("555-0100");
    expect(out).not.toContain("Summer2026");
    expect(out).not.toContain("Tony");
    expect(out).toContain("[card]");
    expect(out).toContain("password: [secret]");
  });

  it("leaves ordinary text alone", () => {
    expect(redactPii("Go to Settings > Members and click Save.")).toBe("Go to Settings > Members and click Save.");
  });
});
