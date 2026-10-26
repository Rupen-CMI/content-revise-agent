import type { DailyCost } from "./events";

export interface ChartPoint {
  day: string; // YYYY-MM-DD (UTC)
  cost: number;
  runs: number;
}

const DAY_MS = 86_400_000;

/** One point per UTC day for the last `days` days, ending today: days without runs become zeros so the
 * x-axis is continuous instead of squeezing the active days together. */
export function fillDays(data: DailyCost[], days = 30, now: Date = new Date()): ChartPoint[] {
  const byDay = new Map(data.map((d) => [d.day, d]));
  const end = Date.UTC(now.getUTCFullYear(), now.getUTCMonth(), now.getUTCDate());
  const out: ChartPoint[] = [];
  for (let i = days - 1; i >= 0; i--) {
    const day = new Date(end - i * DAY_MS).toISOString().slice(0, 10);
    const hit = byDay.get(day);
    out.push({ day, cost: Number(hit?.cost ?? 0), runs: Number(hit?.runs ?? 0) });
  }
  return out;
}

const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

/** "2026-10-03" -> "Oct 3" (no locale dependence, so server and client render the same text). */
export function shortDay(day: string): string {
  const [, m, d] = day.split("-");
  return `${MONTHS[Number(m) - 1] ?? m} ${Number(d)}`;
}

/** Compact y-axis label: $0.05, $1.2, $12. */
export function axisCost(v: number): string {
  if (v === 0) return "$0";
  if (v < 1) return `$${v.toFixed(2)}`;
  if (v < 10) return `$${v.toFixed(1)}`;
  return `$${Math.round(v)}`;
}
