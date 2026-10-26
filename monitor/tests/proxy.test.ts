import { describe, expect, it } from "vitest";
import { callCost, extractUsage, isPeak } from "@/lib/pricing";
import { sanitizeRequest, sanitizeUpstream } from "@/lib/proxy";
import { generateToken, hashToken } from "@/lib/tokens";

const limits = { maxBodyChars: 1000, maxCallsPerMinute: 60, maxTokensCap: 100, upstreamTimeoutMs: 1000, allowedModels: ["deepseek-chat"] };
const base = { model: "deepseek-chat", messages: [{ role: "user", content: "hi" }] };

describe("sanitizeRequest", () => {
  it("passes a plain call and applies the max_tokens cap", () => {
    const r = sanitizeRequest({ ...base, temperature: 0.3, response_format: { type: "json_object" }, max_tokens: 5000 }, limits);
    expect(r.ok).toBe(true);
    if (r.ok) expect(r.payload.max_tokens).toBe(100);
  });

  it("sets a default max_tokens when the client sends none", () => {
    const r = sanitizeRequest(base, limits);
    expect(r.ok && r.payload.max_tokens).toBe(100);
  });

  it("drops params outside the whitelist (tools etc.)", () => {
    const r = sanitizeRequest({ ...base, tools: [{ type: "function" }], user: "x", n: 5 }, limits);
    expect(r.ok).toBe(true);
    if (r.ok) {
      expect(r.payload).not.toHaveProperty("tools");
      expect(r.payload).not.toHaveProperty("user");
      expect(r.payload).not.toHaveProperty("n");
    }
  });

  it("rejects streaming, disallowed models, oversized prompts and bad shapes", () => {
    expect(sanitizeRequest({ ...base, stream: true }, limits)).toMatchObject({ ok: false, code: "stream_unsupported" });
    expect(sanitizeRequest({ ...base, model: "deepseek-reasoner" }, limits)).toMatchObject({ ok: false, code: "model_not_allowed" });
    expect(
      sanitizeRequest({ ...base, messages: [{ role: "user", content: "x".repeat(2000) }] }, limits),
    ).toMatchObject({ ok: false, status: 413 });
    expect(sanitizeRequest({ model: "deepseek-chat" }, limits)).toMatchObject({ ok: false, status: 400 });
    expect(sanitizeRequest({ ...base, messages: [{ role: "tool", content: "x" }] }, limits)).toMatchObject({ ok: false });
    expect(sanitizeRequest(null, limits)).toMatchObject({ ok: false });
  });

  it("treats stream:false as not streaming", () => {
    expect(sanitizeRequest({ ...base, stream: false }, limits).ok).toBe(true);
  });
});

describe("pricing", () => {
  it("matches backend/llm.py _call_cost for deepseek-flash (off-peak)", () => {
    // Sunday 12:00 UTC is off-peak
    const sunday = new Date("2026-10-25T12:00:00Z");
    const { cost, priced } = callCost("deepseek-flash", { prompt_tokens: 22000, cached_tokens: 12000, completion_tokens: 6000 }, sunday);
    // 12000*0.003 + 10000*0.15 + 6000*0.60 = 36 + 1500 + 3600 = 5136 per 1M => 0.005136
    expect(cost).toBeCloseTo(0.005136, 9);
    expect(priced).toBe(true);
  });

  it("uses peak rates 01-04 and 06-10 UTC on weekdays only", () => {
    expect(isPeak(new Date("2026-10-26T07:00:00Z"))).toBe(true); // Monday 07:00
    expect(isPeak(new Date("2026-10-26T05:00:00Z"))).toBe(false); // gap between windows
    expect(isPeak(new Date("2026-10-26T10:00:00Z"))).toBe(false); // end is exclusive
    expect(isPeak(new Date("2026-10-25T07:00:00Z"))).toBe(false); // Sunday
  });

  it("bills an unknown model at the dearest known rate instead of zero", () => {
    const u = { prompt_tokens: 1000, cached_tokens: 0, completion_tokens: 1000 };
    const r = callCost("mystery-model", u, new Date("2026-10-25T12:00:00Z"));
    expect(r.priced).toBe(false);
    expect(r.cost).toBeGreaterThan(0);
  });

  it("extracts usage from both OpenAI-style and DeepSeek-style cache fields", () => {
    expect(
      extractUsage({ model: "deepseek-flash", usage: { prompt_tokens: 10, completion_tokens: 5, prompt_tokens_details: { cached_tokens: 4 } } }),
    ).toEqual({ model: "deepseek-flash", usage: { prompt_tokens: 10, completion_tokens: 5, cached_tokens: 4 } });
    expect(extractUsage({ usage: { prompt_tokens: 10, completion_tokens: 5, prompt_cache_hit_tokens: 7 } })?.usage.cached_tokens).toBe(7);
    expect(extractUsage({ error: "x" })).toBeNull();
    expect(extractUsage(null)).toBeNull();
  });
});

describe("tokens", () => {
  it("generates unique, prefixed, high-entropy tokens and hashes them stably", () => {
    const a = generateToken();
    const b = generateToken();
    expect(a).toMatch(/^cmi_[A-Za-z0-9_-]{43}$/);
    expect(a).not.toBe(b);
    expect(hashToken(a)).toBe(hashToken(a));
    expect(hashToken(a)).toMatch(/^[0-9a-f]{64}$/);
    expect(hashToken(a)).not.toBe(hashToken(b));
  });
});

describe("sanitizeUpstream", () => {
  const ok = JSON.stringify({ choices: [] });

  it("passes 2xx bodies through untouched", () => {
    expect(sanitizeUpstream(200, ok)).toEqual({ status: 200, body: ok, retry: false });
  });

  it("never leaks DeepSeek's account-state errors to the agent", () => {
    for (const [status, text] of [
      [401, '{"error":{"message":"Authentication Fails (governor)"}}'],
      [402, '{"error":{"message":"Insufficient Balance"}}'],
      [403, "forbidden"],
      [500, "<html>internal secret-host.deepseek.internal</html>"],
    ] as const) {
      const r = sanitizeUpstream(status, text);
      expect(r.status).toBe(502);
      expect(r.body).not.toMatch(/Balance|Authentication|governor|secret-host|forbidden/i);
      expect(JSON.parse(r.body).error.code).toBe("upstream_unavailable");
      expect(r.retry).toBe(true);
    }
  });

  it("maps 400/422 to a non-retryable 400 and 429 to a retryable 429 with retry-after", () => {
    expect(sanitizeUpstream(422, "details about the prompt")).toMatchObject({ status: 400, retry: false });
    expect(sanitizeUpstream(422, "details about the prompt").body).not.toContain("details about");
    expect(sanitizeUpstream(429, "x", "7")).toMatchObject({ status: 429, retry: true, retryAfter: "7" });
  });
});
