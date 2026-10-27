import { z } from "zod";
import { demoEnabled } from "./demo";

// USD -> INR exchange rate, fetched on the SERVER only (the browser never contacts a rate provider, so the
// CSP's connect-src 'self' is untouched). Cached by Next's data cache, so a page view almost never waits
// for it. This is a display convenience, not a banking rate.

export interface RateInfo {
  rate: number; // INR per 1 USD
  asOf: string; // the provider's date, or "fixed" / "demo"
  source: "env" | "ecb" | "open-er" | "demo";
}

// A USD->INR rate outside this range is a bad response, not a market move. Refuse it rather than show nonsense.
const MIN_RATE = 10;
const MAX_RATE = 1000;
const REVALIDATE_S = 12 * 3600; // ECB publishes once per working day
const TIMEOUT_MS = 3000;
// After both providers fail, do not retry for a few minutes: a provider outage must not slow every page view.
const RETRY_AFTER_FAILURE_MS = 5 * 60_000;

let failedAt = 0;

function sane(n: unknown): n is number {
  return typeof n === "number" && Number.isFinite(n) && n >= MIN_RATE && n <= MAX_RATE;
}

const day = (d: Date) => d.toISOString().slice(0, 10);

interface Provider {
  source: "ecb" | "open-er";
  url: string;
  parse: (json: unknown) => { rate: number; asOf: string } | null;
}

const PROVIDERS: Provider[] = [
  {
    // ECB reference rates via Frankfurter: free, no key, updated each working day
    source: "ecb",
    url: "https://api.frankfurter.dev/v1/latest?base=USD&symbols=INR",
    parse: (json) => {
      const r = z.object({ date: z.string().max(10), rates: z.object({ INR: z.number() }) }).safeParse(json);
      return r.success ? { rate: r.data.rates.INR, asOf: r.data.date } : null;
    },
  },
  {
    source: "open-er",
    url: "https://open.er-api.com/v6/latest/USD",
    parse: (json) => {
      const r = z
        .object({ result: z.literal("success"), time_last_update_unix: z.number().optional(), rates: z.object({ INR: z.number() }) })
        .safeParse(json);
      if (!r.success) return null;
      const t = r.data.time_last_update_unix;
      return { rate: r.data.rates.INR, asOf: day(t ? new Date(t * 1000) : new Date()) };
    },
  },
];

/** The current USD->INR rate, or null when none can be had (the dashboard then simply stays in USD).
 * Precedence: USD_INR_RATE (a fixed rate you choose) > live providers, in order. */
export async function getUsdInrRate(): Promise<RateInfo | null> {
  const fixed = Number.parseFloat(process.env.USD_INR_RATE ?? "");
  if (sane(fixed)) return { rate: fixed, asOf: "fixed", source: "env" };
  if (demoEnabled()) return { rate: 88, asOf: "demo", source: "demo" };
  if (failedAt && Date.now() - failedAt < RETRY_AFTER_FAILURE_MS) return null;

  for (const p of PROVIDERS) {
    try {
      const res = await fetch(p.url, {
        signal: AbortSignal.timeout(TIMEOUT_MS),
        headers: { accept: "application/json" },
        next: { revalidate: REVALIDATE_S },
      });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const got = p.parse(await res.json());
      if (got && sane(got.rate)) {
        failedAt = 0;
        return { rate: got.rate, asOf: got.asOf, source: p.source };
      }
      throw new Error("unusable response");
    } catch (e) {
      console.error("[rate] provider failed:", p.source, e instanceof Error ? e.message : "error");
    }
  }
  failedAt = Date.now();
  return null;
}

/** Test-only: forget a remembered failure. */
export function _resetRateForTests(): void {
  failedAt = 0;
}

export function describeRate(info: RateInfo): string {
  const label = { ecb: "ECB reference rate", "open-er": "open.er-api.com", env: "fixed rate (USD_INR_RATE)", demo: "demo rate" }[info.source];
  const when = info.asOf === "fixed" || info.asOf === "demo" ? "" : `, ${info.asOf}`;
  return `1 USD = ₹${info.rate.toFixed(2)} (${label}${when})`;
}
