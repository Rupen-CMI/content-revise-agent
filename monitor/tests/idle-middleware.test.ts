import { beforeEach, describe, expect, it } from "vitest";
import { NextRequest } from "next/server";
import { POST as ping } from "@/app/api/session/ping/route";
import { POST as logout } from "@/app/logout/route";
import { middleware } from "@/middleware";
import { createSessionToken, SESSION_COOKIE, SESSION_IDLE_S } from "@/lib/session";

const SECRET = "test-session-secret-0123456789abcdef";
const nowS = () => Math.floor(Date.now() / 1000);

function get(path: string, token?: string, headers: Record<string, string> = {}) {
  return new NextRequest(`https://dash.example.app${path}`, {
    method: path.startsWith("/logout") || path.startsWith("/api/session") ? "POST" : "GET",
    headers: { host: "dash.example.app", ...(token ? { cookie: `${SESSION_COOKIE}=${token}` } : {}), ...headers },
  });
}

beforeEach(() => {
  process.env.SESSION_SECRET = SECRET;
});

describe("middleware sliding idle timeout", () => {
  it("renews the cookie (idle clock restarted, 1 h max-age) for an idle-ish but valid session", async () => {
    const token = await createSessionToken(SECRET, nowS() - 600);
    const res = await middleware(get("/", token));
    expect(res.status).toBe(200);
    const setCookie = res.headers.get("set-cookie") ?? "";
    expect(setCookie).toContain(`${SESSION_COOKIE}=`);
    expect(setCookie).toMatch(/Max-Age=3600/i);
    expect(setCookie).toMatch(/HttpOnly/i);
    expect(setCookie).toMatch(/Secure/i);
    expect(setCookie).toMatch(/SameSite=lax/i);
    expect(setCookie).toMatch(/Path=\//);
  });

  it("does not re-sign a session renewed seconds ago (no Set-Cookie on every request)", async () => {
    const token = await createSessionToken(SECRET, nowS() - 5);
    expect((await middleware(get("/", token))).headers.get("set-cookie")).toBeNull();
  });

  it("a prefetch by <Link> does not count as activity", async () => {
    const token = await createSessionToken(SECRET, nowS() - 600);
    const prefetchHeaders: Record<string, string>[] = [
      { "next-router-prefetch": "1" },
      { purpose: "prefetch" },
      { "sec-purpose": "prefetch;prerender" },
    ];
    for (const h of prefetchHeaders) {
      const res = await middleware(get("/tokens", token, h));
      expect(res.status).toBe(200);
      expect(res.headers.get("set-cookie")).toBeNull();
    }
  });

  it("sign-out is never turned back into a fresh session", async () => {
    const token = await createSessionToken(SECRET, nowS() - 600);
    expect((await middleware(get("/logout", token))).headers.get("set-cookie")).toBeNull();
  });

  it("a session idle for over an hour is bounced to /login?reason=expired", async () => {
    const stale = await createSessionToken(SECRET, nowS() - SESSION_IDLE_S - 5);
    const res = await middleware(get("/", stale));
    expect(res.status).toBe(307);
    const loc = new URL(res.headers.get("location") ?? "");
    expect(loc.pathname).toBe("/login");
    expect(loc.searchParams.get("reason")).toBe("expired");
    expect(res.headers.get("set-cookie")).toBeNull();
  });

  it("no cookie at all goes to a plain /login (no 'expired' notice) and the API answers 401", async () => {
    const res = await middleware(get("/"));
    expect(new URL(res.headers.get("location") ?? "").search).toBe("");
    expect((await middleware(get("/api/tokens"))).status).toBe(401);
    const stale = await createSessionToken(SECRET, nowS() - SESSION_IDLE_S - 5);
    expect((await middleware(get("/api/tokens", stale))).status).toBe(401);
  });
});

describe("/logout", () => {
  it("expires the __Host- cookie with Secure + Path=/ (otherwise browsers ignore the deletion)", async () => {
    const res = await logout(get("/logout", await createSessionToken(SECRET)));
    expect(res.status).toBe(303);
    const setCookie = res.headers.get("set-cookie") ?? "";
    expect(setCookie).toContain(`${SESSION_COOKIE}=;`);
    expect(setCookie).toMatch(/Secure/i);
    expect(setCookie).toMatch(/Path=\//);
    expect(setCookie).toMatch(/Max-Age=0/i);
    expect(setCookie).not.toMatch(/Domain=/i);
  });

  it("refuses cross-site sign-out requests", async () => {
    const res = await logout(get("/logout", await createSessionToken(SECRET), { "sec-fetch-site": "cross-site" }));
    expect(res.status).toBe(403);
  });
});

describe("/api/session/ping", () => {
  it("204 and no-store for a valid same-origin session; 401 without one; 403 cross-site", async () => {
    const token = await createSessionToken(SECRET);
    const ok = await ping(get("/api/session/ping", token));
    expect(ok.status).toBe(204);
    expect(ok.headers.get("cache-control")).toBe("no-store");
    expect((await ping(get("/api/session/ping"))).status).toBe(401);
    const stale = await createSessionToken(SECRET, nowS() - SESSION_IDLE_S - 5);
    expect((await ping(get("/api/session/ping", stale))).status).toBe(401);
    expect((await ping(get("/api/session/ping", token, { "sec-fetch-site": "cross-site" }))).status).toBe(403);
  });
});
