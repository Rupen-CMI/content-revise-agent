import { beforeEach, describe, expect, it, vi } from "vitest";
import { NextRequest } from "next/server";
import { sameOriginOnly } from "@/lib/auth";
import { demoEnabled } from "@/lib/demo";
import { clientIp, ipKey } from "@/lib/throttle";
import { isPublicPath } from "@/middleware";
import { SESSION_COOKIE, SESSION_IDLE_S, SESSION_MAX_S } from "@/lib/session";

const req = (headers: Record<string, string>) =>
  new NextRequest("https://dash.example.app/api/tokens", { method: "POST", headers });

describe("middleware public paths", () => {
  it("allows exactly login, ingest and the v1 proxy", () => {
    expect(isPublicPath("/login")).toBe(true);
    expect(isPublicPath("/api/ingest")).toBe(true);
    expect(isPublicPath("/api/v1/chat/completions")).toBe(true);
  });

  it("does NOT treat lookalikes or admin routes as public", () => {
    for (const p of ["/loginx", "/login/x", "/api/ingestx", "/api/ingest/x", "/api/v10/x", "/api/tokens", "/tokens", "/", "/user/abc", "/logout"]) {
      expect(isPublicPath(p), p).toBe(false);
    }
  });
});

describe("sameOriginOnly (CSRF)", () => {
  it("refuses cross-site fetches by Sec-Fetch-Site and by Origin", () => {
    expect(sameOriginOnly(req({ "sec-fetch-site": "cross-site", host: "dash.example.app" }))?.status).toBe(403);
    expect(sameOriginOnly(req({ origin: "https://evil.example", host: "dash.example.app" }))?.status).toBe(403);
    expect(sameOriginOnly(req({ origin: "not a url", host: "dash.example.app" }))?.status).toBe(403);
  });

  it("allows same-origin browsers and header-less non-browser clients", () => {
    expect(
      sameOriginOnly(req({ "sec-fetch-site": "same-origin", origin: "https://dash.example.app", host: "dash.example.app" })),
    ).toBeNull();
    expect(sameOriginOnly(req({ host: "dash.example.app" }))).toBeNull();
  });
});

describe("login throttle keys", () => {
  it("takes the first X-Forwarded-For hop and never stores the raw address", () => {
    expect(clientIp(new Headers({ "x-forwarded-for": "203.0.113.9, 10.0.0.1" }))).toBe("203.0.113.9");
    expect(clientIp(new Headers())).toBe("unknown");
    const k = ipKey("203.0.113.9", "salt");
    expect(k).toMatch(/^[0-9a-f]{32}$/);
    expect(k).not.toContain("203");
    expect(ipKey("203.0.113.9", "other-salt")).not.toBe(k);
  });
});

describe("session + demo hardening", () => {
  beforeEach(() => vi.unstubAllEnvs());

  it("uses a __Host- cookie, a 1 hour idle timeout and a 12 hour absolute limit", () => {
    expect(SESSION_COOKIE.startsWith("__Host-")).toBe(true);
    expect(SESSION_IDLE_S).toBe(3600);
    expect(SESSION_MAX_S).toBe(12 * 3600);
  });

  it("demo data can never be enabled in production", () => {
    vi.stubEnv("MONITOR_DEMO", "1");
    vi.stubEnv("NODE_ENV", "production");
    expect(demoEnabled()).toBe(false);
    vi.stubEnv("NODE_ENV", "development");
    expect(demoEnabled()).toBe(true);
  });
});
