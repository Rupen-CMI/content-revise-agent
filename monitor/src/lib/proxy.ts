import { z } from "zod";

// ---------------------------------------------------------------- limits (env-overridable)

function intEnv(name: string, fallback: number): number {
  const n = Number.parseInt(process.env[name] ?? "", 10);
  return Number.isFinite(n) && n > 0 ? n : fallback;
}

export function proxyLimits() {
  return {
    // Real prompts peak ~110 KB (22k tokens); 400k chars is ~3.5x that and already beyond the model's context.
    maxBodyChars: intEnv("PROXY_MAX_BODY_CHARS", 400_000),
    // A real run makes ~25 calls over several minutes; 60/min per token only stops runaway loops.
    maxCallsPerMinute: intEnv("PROXY_MAX_CALLS_PER_MIN", 60),
    // Largest completion seen in the run logs was ~13k tokens.
    maxTokensCap: intEnv("PROXY_MAX_TOKENS_CAP", 20_000),
    // Must stay below the route's maxDuration (120 s) so we answer 504 ourselves instead of Vercel's.
    upstreamTimeoutMs: intEnv("PROXY_UPSTREAM_TIMEOUT_MS", 110_000),
    allowedModels: (process.env.ALLOWED_MODELS ?? "deepseek-chat")
      .split(",")
      .map((m) => m.trim())
      .filter(Boolean),
  };
}

// ---------------------------------------------------------------- request sanitising

const messageSchema = z.object({
  role: z.enum(["system", "user", "assistant"]),
  content: z.string(),
});

// Whitelist: anything not listed here (tools, functions, logprobs, stream, n, user, ...) is dropped
// so a leaked token can only do plain chat completions.
const requestSchema = z.object({
  model: z.string().min(1).max(80),
  messages: z.array(messageSchema).min(1).max(60),
  temperature: z.number().min(0).max(2).optional(),
  top_p: z.number().min(0).max(1).optional(),
  max_tokens: z.number().int().min(1).optional(),
  presence_penalty: z.number().min(-2).max(2).optional(),
  frequency_penalty: z.number().min(-2).max(2).optional(),
  stop: z.union([z.string().max(100), z.array(z.string().max(100)).max(4)]).optional(),
  response_format: z.object({ type: z.enum(["json_object", "text"]) }).optional(),
  stream: z.unknown().optional(),
});

export type SanitizeResult =
  | { ok: true; payload: Record<string, unknown> }
  | { ok: false; status: number; message: string; code: string };

export function sanitizeRequest(body: unknown, limits = proxyLimits()): SanitizeResult {
  const parsed = requestSchema.safeParse(body);
  if (!parsed.success) {
    const first = parsed.error.issues[0];
    return {
      ok: false,
      status: 400,
      code: "invalid_request",
      message: `invalid request: ${first.path.join(".") || "body"} ${first.message}`,
    };
  }
  const { stream, ...req } = parsed.data;
  if (stream) {
    return { ok: false, status: 400, code: "stream_unsupported", message: "streaming is not supported by this proxy" };
  }
  if (!limits.allowedModels.includes(req.model)) {
    return { ok: false, status: 400, code: "model_not_allowed", message: `model '${req.model}' is not allowed` };
  }
  const promptChars = req.messages.reduce((n, m) => n + m.content.length, 0);
  if (promptChars > limits.maxBodyChars) {
    return { ok: false, status: 413, code: "prompt_too_large", message: "prompt is too large" };
  }
  return {
    ok: true,
    payload: { ...req, max_tokens: Math.min(req.max_tokens ?? limits.maxTokensCap, limits.maxTokensCap) },
  };
}

/** OpenAI-style error body so the OpenAI SDK in the agent surfaces a readable message. */
export function errorBody(message: string, type: string, code: string) {
  return { error: { message, type, code } };
}

// ---------------------------------------------------------------- upstream error hygiene

export interface UpstreamResult {
  status: number;
  /** body to send the agent */
  body: string;
  /** true -> the OpenAI SDK may retry */
  retry: boolean;
  retryAfter?: string | null;
}

/** DeepSeek's own error text can reveal account state ("Insufficient Balance", "Authentication Fails"),
 * which end users must not see. 2xx passes through untouched; failures become generic OpenAI-style
 * errors with a mapped status. The real status/body are logged server-side by the caller. */
export function sanitizeUpstream(status: number, text: string, retryAfter?: string | null): UpstreamResult {
  if (status >= 200 && status < 300) return { status, body: text, retry: false };
  const generic = (st: number, message: string, type: string, code: string, retry: boolean): UpstreamResult => ({
    status: st,
    body: JSON.stringify(errorBody(message, type, code)),
    retry,
    retryAfter: st === 429 ? retryAfter : undefined,
  });
  if (status === 400 || status === 422) return generic(400, "the model rejected this request", "invalid_request_error", "upstream_rejected", false);
  if (status === 429) return generic(429, "the model is busy; retry shortly", "rate_limit_error", "upstream_rate_limited", true);
  // 401/402/403/404/5xx/anything else: an operator problem, never the user's to see
  return generic(502, "the model service is temporarily unavailable", "api_error", "upstream_unavailable", true);
}
