import { beforeEach, describe, expect, it, vi } from "vitest";

const events = vi.hoisted(() => ({ insertEvent: vi.fn(), upsertInstallName: vi.fn() }));
const tokens = vi.hoisted(() => ({ lookupToken: vi.fn() }));
vi.mock("@/lib/tokens", () => tokens);
vi.mock("@/lib/events", async (orig) => ({ ...(await orig<typeof import("@/lib/events")>()), insertEvent: events.insertEvent, upsertInstallName: events.upsertInstallName }));

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
  events.upsertInstallName.mockReset().mockResolvedValue(undefined);
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

  describe("display names", () => {
    const AUTH = { authorization: "Bearer cmi_good" };

    it("a name_updated event only updates the install's name (no events row)", async () => {
      const res = await call({ ...EVENT, type: "name_updated", user_name: "  Priya   Nair " }, AUTH);
      expect(res.status).toBe(202);
      expect(events.insertEvent).not.toHaveBeenCalled();
      expect(events.upsertInstallName).toHaveBeenCalledWith(EVENT.install_id, "Priya Nair", expect.any(String));
    });

    it("name_updated without a usable name is rejected", async () => {
      expect((await call({ ...EVENT, type: "name_updated" }, AUTH)).status).toBe(400);
      expect((await call({ ...EVENT, type: "name_updated", user_name: "x" }, AUTH)).status).toBe(400);
      expect(events.upsertInstallName).not.toHaveBeenCalled();
    });

    it("every ordinary event also carries the current name (self-healing)", async () => {
      await call({ ...EVENT, user_name: "Priya Nair" }, AUTH);
      expect(events.insertEvent).toHaveBeenCalledTimes(1);
      expect(events.upsertInstallName).toHaveBeenCalledWith(EVENT.install_id, "Priya Nair", expect.any(String));
    });

    it("strips control characters and caps the length", async () => {
      await call({ ...EVENT, user_name: "Pri\u0000ya\n\tNair" + "x".repeat(200) }, AUTH);
      const stored = events.upsertInstallName.mock.calls[0][1] as string;
      expect(stored.startsWith("Priya Nair")).toBe(true);
      expect(stored.length).toBeLessThanOrEqual(60);
      expect(/[\u0000-\u001f]/.test(stored)).toBe(false);
    });

    it("an event without a name never overwrites one (empty names are ignored by the upsert)", async () => {
      await call(EVENT, AUTH);
      expect(events.upsertInstallName).toHaveBeenCalledWith(EVENT.install_id, "", expect.any(String));
    });
  });

  describe("run_progress (per-step cost reports)", () => {
    const AUTH = { authorization: "Bearer cmi_good" };
    const PROGRESS = {
      type: "run_progress",
      event_id: "7c1f9a52-0d3e-4b8a-9a77-3f1d2e6b8c10",
      install_id: "inst-1234567890",
      run_id: "run-abc",
      title: "Market report",
      step: "step2",
      duration_s: 41.2,
      cost_usd: 0.0123,
      prompt_tokens: 15000,
      completion_tokens: 2100,
      cached_tokens: 4000,
      ts: new Date().toISOString(),
    };

    it("is accepted and stored with its step, run id and the cost that step added", async () => {
      const res = await call(PROGRESS, AUTH);
      expect(res.status).toBe(202);
      expect(events.insertEvent).toHaveBeenCalledTimes(1);
      const stored = events.insertEvent.mock.calls[0][0];
      expect(stored).toMatchObject({ type: "run_progress", run_id: "run-abc", step: "step2", cost_usd: 0.0123, prompt_tokens: 15000 });
    });

    it("takes the user label from the token, like every other event", async () => {
      await call({ ...PROGRESS, user_label: "Forged" }, AUTH);
      expect(events.insertEvent.mock.calls[0][0].user_label).toBe("Priya");
    });

    it("an unpriced step (null cost) is fine, a negative or absurd cost is not", async () => {
      expect((await call({ ...PROGRESS, cost_usd: null }, AUTH)).status).toBe(202);
      expect((await call({ ...PROGRESS, event_id: "7c1f9a52-0d3e-4b8a-9a77-3f1d2e6b8c11", cost_usd: -1 }, AUTH)).status).toBe(400);
      expect((await call({ ...PROGRESS, event_id: "7c1f9a52-0d3e-4b8a-9a77-3f1d2e6b8c12", cost_usd: 1e9 }, AUTH)).status).toBe(400);
    });

    it("caps the free-text fields and needs credentials", async () => {
      await call({ ...PROGRESS, step: "s".repeat(80), title: "t".repeat(500) }, AUTH);
      const stored = events.insertEvent.mock.calls[0][0];
      expect(stored.step.length).toBe(24);
      expect(stored.title.length).toBe(200);
      expect((await call(PROGRESS)).status).toBe(401);
    });

    it("also carries the display name, so the Users table updates from a progress event", async () => {
      await call({ ...PROGRESS, user_name: "Rupen Kumar" }, AUTH);
      expect(events.upsertInstallName).toHaveBeenCalledWith(PROGRESS.install_id, "Rupen Kumar", expect.any(String));
    });
  });
});
