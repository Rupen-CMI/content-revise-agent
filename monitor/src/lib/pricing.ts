// DeepSeek cost estimate — mirrors backend/llm.py (_PRICING_PER_1M_USD, _is_peak, _call_cost).
// Keep the two in sync: the table below is the one the proxy bills against.
// $/1M tokens. "deepseek-chat" is a legacy alias that DeepSeek bills as "deepseek-flash".

interface Rates {
  cache_hit: number;
  cache_miss: number;
  output: number;
}

const PRICING: Record<string, { off_peak: Rates; peak: Rates }> = {
  "deepseek-flash": {
    off_peak: { cache_hit: 0.003, cache_miss: 0.15, output: 0.6 },
    peak: { cache_hit: 0.006, cache_miss: 0.3, output: 1.2 },
  },
};

// Unknown/renamed model: bill at the dearest known rate rather than 0, so a model the table
// doesn't know can never be used to dodge the daily budget.
const FALLBACK = PRICING["deepseek-flash"].peak;

// Peak: 01:00-04:00 and 06:00-10:00 UTC, Monday-Friday (holidays not modelled; estimate only).
const PEAK_UTC_WINDOWS: ReadonlyArray<readonly [number, number]> = [
  [1, 4],
  [6, 10],
];

export function isPeak(d: Date): boolean {
  const dow = d.getUTCDay(); // 0 Sun .. 6 Sat
  if (dow === 0 || dow === 6) return false;
  const h = d.getUTCHours();
  return PEAK_UTC_WINDOWS.some(([s, e]) => h >= s && h < e);
}

export interface Usage {
  prompt_tokens: number;
  completion_tokens: number;
  cached_tokens: number;
}

export function callCost(model: string, u: Usage, at: Date = new Date()): { cost: number; priced: boolean } {
  const table = PRICING[model];
  const rates = table ? (isPeak(at) ? table.peak : table.off_peak) : FALLBACK;
  const miss = Math.max(0, u.prompt_tokens - u.cached_tokens);
  const cost = (u.cached_tokens * rates.cache_hit + miss * rates.cache_miss + u.completion_tokens * rates.output) / 1_000_000;
  return { cost, priced: Boolean(table) };
}

function asCount(v: unknown): number {
  return typeof v === "number" && Number.isFinite(v) && v >= 0 ? Math.floor(v) : 0;
}

/** Pull token counts out of a (parsed) chat-completions response. Accepts both the OpenAI-style
 * `prompt_tokens_details.cached_tokens` (what llm.py reads) and DeepSeek's `prompt_cache_hit_tokens`. */
export function extractUsage(resp: unknown): { model: string; usage: Usage } | null {
  if (!resp || typeof resp !== "object") return null;
  const r = resp as { model?: unknown; usage?: Record<string, unknown> };
  const u = r.usage;
  if (!u || typeof u !== "object") return null;
  const details = (u.prompt_tokens_details ?? {}) as Record<string, unknown>;
  const cached = asCount(details.cached_tokens) || asCount(u.prompt_cache_hit_tokens);
  return {
    model: typeof r.model === "string" ? r.model.slice(0, 80) : "",
    usage: {
      prompt_tokens: asCount(u.prompt_tokens),
      completion_tokens: asCount(u.completion_tokens),
      cached_tokens: cached,
    },
  };
}
