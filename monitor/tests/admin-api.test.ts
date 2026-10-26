import { beforeEach, describe, expect, it, vi } from "vitest";

const tokens = vi.hoisted(() => ({
  listTokens: vi.fn(),
  createToken: vi.fn(),
  revokeToken: vi.fn(),
}));
vi.mock("@/lib/tokens", () => tokens);

import { NextRequest } from "next/server";
import { DELETE, GET, POST } from "@/app/api/tokens/route";
import { createSessionToken, SESSION_COOKIE } from "@/lib/session";

const SECRET = "test-session-secret-0123456789abcdef";
const ID = "11111111-1111-1111-1111-111111111111";

async function authed(headers: Record<string, string> = {}) {
  return { cookie: `${SESSION_COOKIE}=${await createSessionToken(SECRET)}`, host: "dash.example.app", ...headers };
}

function call(method: string, url: string, headers: Record<string, string>, body?: unknown) {
  const req = new NextRequest(`https://dash.example.app${url}`, {
    method,
    headers: { "content-type": "application/json", ...headers },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  return { GET, POST, DELETE }[method as "GET" | "POST" | "DELETE"](req);
}

beforeEach(() => {
  process.env.SESSION_SECRET = SECRET;
  tokens.listTokens.mockReset().mockResolvedValue([]);
  tokens.createToken.mockReset().mockResolvedValue({ id: ID, token: "cmi_secret" });
  tokens.revokeToken.mockReset().mockResolvedValue(true);
});

describe("/api/tokens re-checks the session itself (middleware is not the only gate)", () => {
  it("401 without a cookie, with a forged cookie, and with another secret's cookie", async () => {
    expect((await call("GET", "/api/tokens", {})).status).toBe(401);
    expect((await call("GET", "/api/tokens", { cookie: `${SESSION_COOKIE}=9999999999.deadbeef` })).status).toBe(401);
    const foreign = { cookie: `${SESSION_COOKIE}=${await createSessionToken("some-other-secret")}` };
    expect((await call("POST", "/api/tokens", foreign, { label: "x" })).status).toBe(401);
    expect((await call("DELETE", `/api/tokens?id=${ID}`, {})).status).toBe(401);
    expect(tokens.listTokens).not.toHaveBeenCalled();
    expect(tokens.createToken).not.toHaveBeenCalled();
    expect(tokens.revokeToken).not.toHaveBeenCalled();
  });

  it("an authenticated same-origin admin can list, create (token shown once, no-store) and revoke", async () => {
    expect((await call("GET", "/api/tokens", await authed())).status).toBe(200);
    const created = await call("POST", "/api/tokens", await authed(), { label: "Priya", daily_budget_usd: 5 });
    expect(created.status).toBe(201);
    expect(created.headers.get("cache-control")).toBe("no-store");
    expect((await created.json()).token).toBe("cmi_secret");
    expect((await call("DELETE", `/api/tokens?id=${ID}`, await authed())).status).toBe(200);
  });

  it("refuses cross-site mutations even with a valid session (CSRF)", async () => {
    const h = await authed({ "sec-fetch-site": "cross-site", origin: "https://evil.example" });
    expect((await call("POST", "/api/tokens", h, { label: "x" })).status).toBe(403);
    expect((await call("DELETE", `/api/tokens?id=${ID}`, h)).status).toBe(403);
    expect(tokens.createToken).not.toHaveBeenCalled();
    expect(tokens.revokeToken).not.toHaveBeenCalled();
  });

  it("validates input: bad bodies, absurd budgets and non-UUID ids", async () => {
    expect((await call("POST", "/api/tokens", await authed(), { label: "" })).status).toBe(400);
    expect((await call("POST", "/api/tokens", await authed(), { label: "x", daily_budget_usd: 1e9 })).status).toBe(400);
    expect((await call("DELETE", "/api/tokens?id=1%27%3B%20DROP%20TABLE%20install_tokens%3B--", await authed())).status).toBe(400);
  });
});
