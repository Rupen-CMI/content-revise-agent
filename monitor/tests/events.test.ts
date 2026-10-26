import { describe, expect, it } from "vitest";
import {
  ingestSchema,
  locationFromHeaders,
  type IngestEvent,
} from "@/lib/events";

const base = {
  event_id: "11111111-2222-3333-4444-555555555555",
  install_id: "8f0a1b2c-3d4e-5f60-7182-93a4b5c6d7e8",
  user_label: "Acme Realty",
  app_version: "1.0.0",
  ts: "2026-10-26T10:00:00.123+00:00",
};

describe("ingestSchema", () => {
  it("accepts a valid run_completed event and fills defaults", () => {
    const parsed = ingestSchema.parse({
      ...base,
      type: "run_completed",
      run_id: "abc123",
      title: "Top 10 Widget Markets",
      word_count: 1540,
      status: "ok",
      duration_s: 42.3,
      cost_usd: 0.0123,
      prompt_tokens: 100,
      completion_tokens: 50,
      cached_tokens: 7,
    });
    expect(parsed.type).toBe("run_completed");
    expect("status" in parsed && parsed.status).toBe("ok");
    expect(parsed.ts).toBe("2026-10-26T10:00:00.123Z");
  });

  it("accepts a valid app_started event", () => {
    const parsed = ingestSchema.parse({
      ...base,
      type: "app_started",
      // run-only fields must not be required here
    }) as Extract<IngestEvent, { type: "app_started" }>;
    expect(parsed.type).toBe("app_started");
    expect(parsed.user_label).toBe("Acme Realty");
  });

  it("rejects an unknown type", () => {
    const res = ingestSchema.safeParse({ ...base, type: "heartbeat" });
    expect(res.success).toBe(false);
  });

  it("rejects a missing/wrong event_id and a bad ts", () => {
    expect(
      ingestSchema.safeParse({ ...base, type: "app_started", event_id: "short" })
        .success,
    ).toBe(false);
    expect(
      ingestSchema.safeParse({ ...base, type: "app_started", ts: "not a date" })
        .success,
    ).toBe(false);
  });

  it("strips unknown fields (no document body sneaks in)", () => {
    const parsed = ingestSchema.parse({
      ...base,
      type: "app_started",
      body_text: "should not be stored",
      raw_ip: "1.2.3.4",
    });
    expect(JSON.stringify(parsed)).not.toContain("should not be stored");
    expect(JSON.stringify(parsed)).not.toContain("1.2.3.4");
  });

  it("caps over-long strings instead of rejecting them", () => {
    const longTitle = "x".repeat(500);
    const parsed = ingestSchema.parse({
      ...base,
      type: "run_completed",
      title: longTitle,
      status: "ok",
    }) as Extract<IngestEvent, { type: "run_completed" }>;
    expect(parsed.title.length).toBeLessThanOrEqual(200);
  });

  it("accepts epoch-number ts (seconds or ms)", () => {
    const s = ingestSchema.parse({ ...base, type: "app_started", ts: 1_772_000_000 });
    expect(s.ts).toBe(new Date(1_772_000_000_000).toISOString());
  });

  it("rejects a run_completed without status", () => {
    const noStatus: Record<string, unknown> = {
      ...base,
      type: "run_completed",
      status: "ok",
    };
    delete noStatus.status;
    expect(ingestSchema.safeParse(noStatus).success).toBe(false);
  });
});

describe("locationFromHeaders", () => {
  it("URL-decodes the city and caps field lengths", () => {
    const headers = new Headers({
      "x-vercel-ip-city": "S%C3%A3o%20Paulo",
      "x-vercel-ip-country-region": "SP",
      "x-vercel-ip-country": "BR",
    });
    expect(locationFromHeaders(headers)).toEqual({
      city: "São Paulo",
      region: "SP",
      country: "BR",
    });
  });

  it("returns empty strings when Vercel headers are absent (local dev)", () => {
    expect(locationFromHeaders(new Headers())).toEqual({
      city: "",
      region: "",
      country: "",
    });
  });

  it("keeps a non-decodable city value rather than throwing", () => {
    const headers = new Headers({ "x-vercel-ip-city": "%ZZinvalid" });
    expect(locationFromHeaders(headers).city).toBe("%ZZinvalid");
  });
});
