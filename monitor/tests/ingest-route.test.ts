import { beforeEach, describe, expect, it, vi } from "vitest";

const events = vi.hoisted(() => ({ insertEvent: vi.fn() }));
const tokens = vi.hoisted(() => ({ lookupToken: vi.fn() }));
vi.mock("@/lib/tokens", () => tokens);
vi.mock("@/lib/events", async (orig) => ({ ...(await orig<typeof import("@/lib/events")>()), insertEvent: events.insertEvent }));

import { NextRequest } from "next/server";
import { POST } from "@/app/api/ingest/route";

const EVENT = {
  type: "run_completed",
  event_id: "5b0e4d51-7f0f-4f33-9d0a-0e4d7d5c9a11",
  install_id: "inst-1234567890",
  user_label: "Forged Name",
  ts: new Date().toISOString(),
  status: "ok",
  title: "A title",
  cost_usd: 0.01,
};

function call(body: unknown, headers: Record<string, string> = {}) {
  return POST(
    new NextRequest("http://localhost/api/ingest", {
      method: "POST",
      headers: { "content-type": "application/json", ...headers },
      body: typeof body === "string" ? body : JSON.stringify(body),
    }),
  );
}

beforeEach(() => {
  vi.restoreAllMocks();
  events.insertEvent.mockReset().mockResolvedValue(true);
  tokens.lookupToken
    .mockReset()
    .mockImplementation(async (t: string) => (t === "cmi_good" ? { id: "t1", label: "Priya", daily_budget_usd: 5 } : null));
  process.env.INGEST_KEY = "shared-secret-key";
});

describe("POST /api/ingest", () => {
  it("rejects missing, wrong and revoked credentials", async () => {
    expect((await call(EVENT)).status).toBe(401);
    expect((await call(EVENT, { "x-ingest-key": "nope" })).status).toBe(401);
    expect((await call(EVENT, { authorization: "Bearer cmi_revoked" })).status).toBe(401);
    expect(events.insertEvent).not.toHaveBeenCalled();
  });

  it("with an install token, the label comes from the TOKEN, not the payload (no impersonation)", async () => {
    const res = await call(EVENT, { authorization: "Bearer cmi_good" });
    expect(res.status).toBe(202);
    expect(events.insertEvent.mock.calls[0][0].user_label).toBe("Priya");
  });

  it("a bad bearer token is not rescued by a valid shared key", async () => {
    const res = await call(EVENT, { authorization: "Bearer cmi_bad", "x-ingest-key": "shared-secret-key" });
    expect(res.status).toBe(401);
  });

  it("the legacy shared key still works for --direct builds and keeps the payload label", async () => {
    const res = await call(EVENT, { "x-ingest-key": "shared-secret-key" });
    expect(res.status).toBe(202);
    expect(events.insertEvent.mock.calls[0][0].user_label).toBe("Forged Name");
  });

  it("an unset INGEST_KEY never matches an empty header", async () => {
    delete process.env.INGEST_KEY;
    expect((await call(EVENT, { "x-ingest-key": "" })).status).toBe(401);
  });

  it("caps the body size and rejects bad JSON / bad events", async () => {
    const h = { authorization: "Bearer cmi_good" };
    expect((await call("x".repeat(20_000), h)).status).toBe(413);
    expect((await call("{nope", h)).status).toBe(400);
    expect((await call({ ...EVENT, event_id: "not-a-uuid" }, h)).status).toBe(400);
  });

  it("clamps a forged far-future timestamp", async () => {
    await call({ ...EVENT, ts: "2099-01-01T00:00:00Z" }, { authorization: "Bearer cmi_good" });
    const ts = Date.parse(events.insertEvent.mock.calls[0][0].ts);
    expect(ts).toBeLessThanOrEqual(Date.now() + 1000); // a forged year-2099 timestamp is recorded as "now"
  });

  it("responses are no-store", async () => {
    expect((await call(EVENT)).headers.get("cache-control")).toBe("no-store");
  });
});
