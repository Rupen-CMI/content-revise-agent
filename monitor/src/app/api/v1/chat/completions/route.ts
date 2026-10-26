import { NextRequest, NextResponse, after } from "next/server";
import { locationFromHeaders } from "@/lib/events";
import { callCost, extractUsage } from "@/lib/pricing";
import { errorBody, proxyLimits, sanitizeRequest, sanitizeUpstream } from "@/lib/proxy";
import { findActiveToken, recordCall, spendTodayAll } from "@/lib/tokens";

export const runtime = "nodejs";
// Real calls peak at ~47 s (3,975 logged calls). 120 s is generous, and well under Vercel's 300 s cap.
export const maxDuration = 120;

const UPSTREAM = (process.env.DEEPSEEK_BASE_URL ?? "https://api.deepseek.com").replace(/\/+$/, "");
const NO_STORE = { "cache-control": "no-store" };

function fail(status: number, message: string, type: string, code: string, retry = false, extra: Record<string, string> = {}) {
  return NextResponse.json(errorBody(message, type, code), {
    status,
    // The OpenAI SDK auto-retries 429/5xx; for budget/auth/validation errors a retry is pure waste.
    headers: { ...NO_STORE, "x-should-retry": retry ? "true" : "false", ...extra },
  });
}

/**
 * POST /api/v1/chat/completions — OpenAI-compatible pass-through to DeepSeek.
 * The agent points its OpenAI client's base_url at <monitor>/api/v1 and uses its per-install
 * token (cmi_...) as the api_key; the real DeepSeek key exists only in this server's env.
 */
export async function POST(req: NextRequest) {
  const started = Date.now();
  const limits = proxyLimits();

  const apiKey = process.env.DEEPSEEK_API_KEY;
  if (!apiKey) return fail(500, "proxy is not configured", "server_error", "not_configured");

  // --- auth + budget + rate in ONE database round trip
  const auth = req.headers.get("authorization") ?? "";
  const bearer = auth.toLowerCase().startsWith("bearer ") ? auth.slice(7).trim() : "";
  let token;
  try {
    token = bearer ? await findActiveToken(bearer) : null;
  } catch (e) {
    console.error("[proxy] token lookup failed:", e);
    return fail(500, "storage failure", "server_error", "storage_failure");
  }
  if (!token) return fail(401, "invalid or revoked token", "invalid_request_error", "invalid_api_key");

  if (token.spend_today >= token.daily_budget_usd) {
    return fail(429, "daily budget reached for this install; try again tomorrow (UTC)", "insufficient_quota", "daily_budget_exceeded");
  }
  if (token.calls_last_minute >= limits.maxCallsPerMinute) {
    return fail(429, "too many requests; slow down", "rate_limit_error", "rate_limited", false, { "retry-after": "30" });
  }

  // --- validate + sanitise the request (whitelisted params, allowed model, size caps)
  const raw = await req.text();
  if (raw.length > limits.maxBodyChars + 50_000) {
    return fail(413, "request is too large", "invalid_request_error", "prompt_too_large");
  }
  let body: unknown;
  try {
    body = JSON.parse(raw);
  } catch {
    return fail(400, "invalid JSON", "invalid_request_error", "invalid_json");
  }
  const clean = sanitizeRequest(body, limits);
  if (!clean.ok) return fail(clean.status, clean.message, "invalid_request_error", clean.code);

  try {
    const globalCap = Number.parseFloat(process.env.GLOBAL_DAILY_BUDGET_USD ?? "");
    if (Number.isFinite(globalCap) && globalCap > 0 && (await spendTodayAll()) >= globalCap) {
      return fail(429, "service daily budget reached; try again tomorrow (UTC)", "insufficient_quota", "global_budget_exceeded");
    }
  } catch (e) {
    console.error("[proxy] global budget check failed:", e);
    return fail(500, "storage failure", "server_error", "storage_failure");
  }

  // --- forward
  const loc = locationFromHeaders(req.headers);
  const installId = (req.headers.get("x-install-id") ?? "").slice(0, 64);
  const requestedModel = String(clean.payload.model);
  let upstream: Response;
  let text: string;
  try {
    upstream = await fetch(`${UPSTREAM}/chat/completions`, {
      method: "POST",
      headers: { "content-type": "application/json", authorization: `Bearer ${apiKey}` },
      body: JSON.stringify(clean.payload),
      signal: AbortSignal.timeout(limits.upstreamTimeoutMs),
    });
    text = await upstream.text();
  } catch (e) {
    const timedOut = e instanceof Error && (e.name === "TimeoutError" || e.name === "AbortError");
    console.error("[proxy] upstream failure:", timedOut ? "timeout" : e);
    after(() => log(token, installId, requestedModel, timedOut ? 504 : 502, started, loc, null));
    return timedOut
      ? fail(504, "the model did not answer in time", "api_error", "upstream_timeout", true)
      : fail(502, "could not reach the model server", "api_error", "upstream_unreachable", true);
  }

  if (!upstream.ok) {
    // The real status/body stay in OUR logs; the agent only gets a generic error (see sanitizeUpstream).
    console.error(`[proxy] upstream HTTP ${upstream.status}:`, text.slice(0, 300));
  }
  const out = sanitizeUpstream(upstream.status, text, upstream.headers.get("retry-after"));

  let parsed: unknown = null;
  if (upstream.ok) {
    try {
      parsed = JSON.parse(text);
    } catch {
      /* non-JSON 2xx body: pass through unchanged, record zero usage */
    }
  }
  after(() => log(token, installId, requestedModel, out.status, started, loc, parsed));

  const headers: Record<string, string> = {
    ...NO_STORE,
    "content-type": upstream.ok ? (upstream.headers.get("content-type") ?? "application/json") : "application/json",
  };
  if (!upstream.ok) headers["x-should-retry"] = out.retry ? "true" : "false";
  if (out.retryAfter) headers["retry-after"] = out.retryAfter;
  return new NextResponse(out.body, { status: out.status, headers });
}

async function log(
  token: { id: string },
  installId: string,
  requestedModel: string,
  httpStatus: number,
  started: number,
  loc: { city: string; region: string; country: string },
  parsed: unknown,
) {
  try {
    const u = extractUsage(parsed);
    const model = u?.model || requestedModel;
    const cost = u ? callCost(model, u.usage).cost : 0;
    await recordCall({
      tokenId: token.id,
      installId,
      model,
      promptTokens: u?.usage.prompt_tokens ?? 0,
      completionTokens: u?.usage.completion_tokens ?? 0,
      cachedTokens: u?.usage.cached_tokens ?? 0,
      costUsd: cost,
      durationMs: Date.now() - started,
      httpStatus,
      city: loc.city,
      region: loc.region,
      country: loc.country,
    });
  } catch (e) {
    console.error("[proxy] could not record call:", e); // never fail the user's request over bookkeeping
  }
}
