import { beforeEach, describe, expect, it, vi } from "vitest";

// `after()` only works inside a Next request scope; run its callback inline in tests.
vi.mock("next/server", async (orig) => ({ ...(await orig<typeof import("next/server")>()), after: (fn: () => unknown) => fn() }));

const tokens = vi.hoisted(() => ({
  findActiveToken: vi.fn(),
  spendTodayAll: vi.fn(),
  recordCall: vi.fn(),
}));
vi.mock("@/lib/tokens", () => tokens);

import { NextRequest } from "next/server";
import { POST } from "@/app/api/v1/chat/completions/route";

const TOKEN = { id: "11111111-1111-1111-1111-111111111111", label: "Priya", daily_budget_usd: 5, spend_today: 0, calls_last_minute: 0 };
const goodBody = { model: "deepseek-chat", messages: [{ role: "user", content: "hello" }], response_format: { type: "json_object" } };

function call(body: unknown, headers: Record<string, string> = { authorization: "Bearer cmi_good" }) {
  return POST(
    new NextRequest("http://localhost/api/v1/chat/completions", {
      method: "POST",
      headers: { "content-type": "application/json", "x-vercel-ip-city": "Mumbai", "x-vercel-ip-country": "IN", ...headers },
      body: typeof body === "string" ? body : JSON.stringify(body),
    }),
  );
}

beforeEach(() => {
  vi.restoreAllMocks();
  Object.values(tokens).forEach((m) => m.mockReset());
  tokens.findActiveToken.mockImplementation(async (t: string) => (t === "cmi_good" ? TOKEN : null));
  tokens.spendTodayAll.mockResolvedValue(0);
  tokens.recordCall.mockResolvedValue(undefined);
  process.env.DEEPSEEK_API_KEY = "sk-test-not-real";
  delete process.env.GLOBAL_DAILY_BUDGET_USD;
});

describe("POST /api/v1/chat/completions", () => {
  it("401s without a valid token and never reaches upstream", async () => {
    const f = vi.spyOn(globalThis, "fetch");
    expect((await call(goodBody, {})).status).toBe(401);
    expect((await call(goodBody, { authorization: "Bearer cmi_wrong" })).status).toBe(401);
    expect(f).not.toHaveBeenCalled();
  });

  it("400s on invalid JSON, streaming and disallowed models without calling upstream", async () => {
    const f = vi.spyOn(globalThis, "fetch");
    expect((await call("{not json")).status).toBe(400);
    expect((await call({ ...goodBody, stream: true })).status).toBe(400);
    expect((await call({ ...goodBody, model: "gpt-4" })).status).toBe(400);
    expect(f).not.toHaveBeenCalled();
  });

  it("429s (no retry hint) once the token's daily budget is spent", async () => {
    tokens.findActiveToken.mockImplementation(async (t: string) => (t === "cmi_good" ? { ...TOKEN, spend_today: 5 } : null));
    const f = vi.spyOn(globalThis, "fetch");
    const res = await call(goodBody);
    expect(res.status).toBe(429);
    expect(res.headers.get("x-should-retry")).toBe("false");
    expect((await res.json()).error.code).toBe("daily_budget_exceeded");
    expect(f).not.toHaveBeenCalled();
  });

  it("429s when the global daily cap is reached", async () => {
    process.env.GLOBAL_DAILY_BUDGET_USD = "10";
    tokens.spendTodayAll.mockResolvedValue(10);
    expect((await call(goodBody)).status).toBe(429);
  });

  it("forwards with the server-side key, returns the upstream body verbatim, and records usage + cost + location", async () => {
    const upstreamBody = JSON.stringify({
      model: "deepseek-flash",
      choices: [{ message: { role: "assistant", content: "{}" } }],
      usage: { prompt_tokens: 1000, completion_tokens: 500, prompt_tokens_details: { cached_tokens: 200 } },
    });
    const f = vi.spyOn(globalThis, "fetch").mockResolvedValue(new Response(upstreamBody, { status: 200, headers: { "content-type": "application/json" } }));

    const res = await call(goodBody, { authorization: "Bearer cmi_good", "x-install-id": "inst-123" });
    expect(res.status).toBe(200);
    expect(await res.text()).toBe(upstreamBody);

    const [url, init] = f.mock.calls[0];
    expect(String(url)).toBe("https://api.deepseek.com/chat/completions");
    const headers = (init as RequestInit).headers as Record<string, string>;
    expect(headers.authorization).toBe("Bearer sk-test-not-real"); // the install token is NOT forwarded
    const sent = JSON.parse(String((init as RequestInit).body));
    expect(sent.max_tokens).toBe(20000); // default cap applied
    expect(sent).not.toHaveProperty("stream");

    expect(tokens.recordCall).toHaveBeenCalledTimes(1);
    const rec = tokens.recordCall.mock.calls[0][0];
    expect(rec).toMatchObject({
      tokenId: TOKEN.id, installId: "inst-123", model: "deepseek-flash", promptTokens: 1000,
      completionTokens: 500, cachedTokens: 200, httpStatus: 200, city: "Mumbai", country: "IN",
    });
    expect(rec.costUsd).toBeGreaterThan(0);
  });

  it("hides upstream account errors from the agent (402 -> generic 502) and records zero usage", async () => {
    vi.spyOn(console, "error").mockImplementation(() => {});
    vi.spyOn(globalThis, "fetch").mockResolvedValue(new Response(JSON.stringify({ error: { message: "Insufficient Balance" } }), { status: 402 }));
    const res = await call(goodBody);
    expect(res.status).toBe(502);
    const text = await res.text();
    expect(text).not.toMatch(/Balance/i);
    expect(JSON.parse(text).error.code).toBe("upstream_unavailable");
    expect(res.headers.get("x-should-retry")).toBe("true");
    expect(tokens.recordCall.mock.calls[0][0]).toMatchObject({ httpStatus: 502, promptTokens: 0, costUsd: 0 });
  });

  it("rate-limits a token that is calling too fast (429, no retry)", async () => {
    tokens.findActiveToken.mockImplementation(async (t: string) => (t === "cmi_good" ? { ...TOKEN, calls_last_minute: 60 } : null));
    const f = vi.spyOn(globalThis, "fetch");
    const res = await call(goodBody);
    expect(res.status).toBe(429);
    expect((await res.json()).error.code).toBe("rate_limited");
    expect(res.headers.get("x-should-retry")).toBe("false");
    expect(f).not.toHaveBeenCalled();
  });

  it("marks every response no-store", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(new Response("{}", { status: 200 }));
    expect((await call(goodBody)).headers.get("cache-control")).toBe("no-store");
    expect((await call(goodBody, {})).headers.get("cache-control")).toBe("no-store");
  });

  it("answers 504 on upstream timeout and 502 when unreachable", async () => {
    vi.spyOn(console, "error").mockImplementation(() => {});
    const timeout = Object.assign(new Error("t"), { name: "TimeoutError" });
    vi.spyOn(globalThis, "fetch").mockRejectedValueOnce(timeout);
    expect((await call(goodBody)).status).toBe(504);
    vi.spyOn(globalThis, "fetch").mockRejectedValueOnce(new TypeError("fetch failed"));
    expect((await call(goodBody)).status).toBe(502);
  });

  it("does not fail the request when bookkeeping fails", async () => {
    vi.spyOn(console, "error").mockImplementation(() => {});
    tokens.recordCall.mockRejectedValue(new Error("db down"));
    vi.spyOn(globalThis, "fetch").mockResolvedValue(new Response(JSON.stringify({ usage: { prompt_tokens: 1, completion_tokens: 1 } }), { status: 200 }));
    expect((await call(goodBody)).status).toBe(200);
  });

  it("500s cleanly when the server has no DeepSeek key", async () => {
    delete process.env.DEEPSEEK_API_KEY;
    expect((await call(goodBody)).status).toBe(500);
  });
});
