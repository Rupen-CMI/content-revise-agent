// DEMO DATA for looking at the dashboard without a database (`npm run demo`).
// Hard-disabled in production builds: it needs MONITOR_DEMO=1 AND NODE_ENV !== "production",
// and `next build`/Vercel always run with NODE_ENV=production, so this can never serve fake data live.
import type { DailyCost, InstallRow, LocationRow, RunRow, Stats } from "./events";
import type { TokenListRow } from "./tokens";

export function demoEnabled(): boolean {
  return process.env.MONITOR_DEMO === "1" && process.env.NODE_ENV !== "production";
}

const USERS = [
  { id: "9f1c2a7e-4b1d-4c55-8a10-aaaa00000001", label: "Priya Nair", city: "Mumbai", region: "MH", country: "IN", runs: 14 },
  { id: "3be8d4f0-77a2-49b6-9d3e-aaaa00000002", label: "Rahul Verma", city: "Bengaluru", region: "KA", country: "IN", runs: 9 },
  { id: "c04a6e91-2f38-4d7c-b5a1-aaaa00000003", label: "Aisha Khan", city: "Dubai", region: "DU", country: "AE", runs: 6 },
  { id: "5d7f3b12-90cc-4e84-a6f7-aaaa00000004", label: "Daniel Brooks", city: "London", region: "ENG", country: "GB", runs: 4 },
  { id: "e2a9c8d5-1f46-4a0b-8c3d-aaaa00000005", label: "Mei Tan", city: "Singapore", region: "SG", country: "SG", runs: 2 },
];

const TITLES = [
  "Global Wealth Management Market: Digital Advisory Reshapes Client Onboarding",
  "Web Scraping Software Market Outlook: Anti-Bot Defenses Drive Proxy Demand",
  "Marketing Automation Platforms: Why Mid-Market Buyers Are Consolidating Stacks",
  "Family Office Services Market Surges as Intergenerational Wealth Transfers Accelerate",
  "Data Extraction Tools Market: AI Parsers Replace Rule-Based Scrapers",
  "Customer Data Platform Market Trends: Privacy Rules Reshape Identity Resolution",
  "Robo-Advisory Market: Hybrid Models Win the Affluent Segment",
  "Email Marketing Software Market: Deliverability Becomes the Differentiator",
  "Private Banking Technology Market: Core Modernisation Gathers Pace",
  "Social Media Analytics Market: Brands Shift Budgets to Creator Measurement",
];

// small deterministic PRNG so the demo looks the same on every start
function rng(seed: number) {
  let s = seed;
  return () => ((s = (s * 1664525 + 1013904223) % 4294967296) / 4294967296);
}

const DAY = 86_400_000;

function allRuns(): RunRow[] {
  const rand = rng(42);
  const rows: RunRow[] = [];
  let n = 0;
  for (const u of USERS) {
    for (let i = 0; i < u.runs; i++) {
      const ageMs = (i * 1.7 + rand() * 1.5) * DAY + rand() * 6 * 3_600_000;
      const prompt = Math.round(14_000 + rand() * 9_000);
      const completion = Math.round(2_500 + rand() * 7_500);
      rows.push({
        event_id: `demo-${n++}`,
        install_id: u.id,
        user_label: u.label,
        title: TITLES[Math.floor(rand() * TITLES.length)],
        status: rand() > 0.93 ? "error" : "ok",
        word_count: Math.round(1_100 + rand() * 900),
        cost_usd: (prompt * 0.15 + completion * 0.6) / 1_000_000 * (1.6 + rand() * 1.4),
        duration_s: 40 + rand() * 110,
        prompt_tokens: prompt,
        completion_tokens: completion,
        city: u.city,
        region: u.region,
        country: u.country,
        ts: new Date(Date.now() - ageMs).toISOString(),
      });
    }
  }
  return rows.sort((a, b) => b.ts.localeCompare(a.ts));
}

export function demoInstalls(): InstallRow[] {
  const runs = allRuns();
  return USERS.map((u) => {
    const mine = runs.filter((r) => r.install_id === u.id);
    return {
      install_id: u.id,
      label: u.label,
      last_seen: mine[0].ts,
      last_location: `${u.city}, ${u.country}`,
      runs: mine.length,
      total_cost: mine.reduce((s, r) => s + (r.cost_usd ?? 0), 0),
      total_tokens: String(mine.reduce((s, r) => s + (r.prompt_tokens ?? 0) + (r.completion_tokens ?? 0), 0)),
    };
  }).sort((a, b) => b.last_seen.localeCompare(a.last_seen));
}

export function demoStats(): Stats {
  const runs = allRuns();
  const week = Date.now() - 7 * DAY;
  const recent = runs.filter((r) => Date.parse(r.ts) > week);
  return {
    active_users: new Set(recent.map((r) => r.install_id)).size,
    total_runs: runs.length,
    total_cost: runs.reduce((s, r) => s + (r.cost_usd ?? 0), 0),
    cost_7d: recent.reduce((s, r) => s + (r.cost_usd ?? 0), 0),
  };
}

export const demoRecentRuns = (limit: number): RunRow[] => allRuns().slice(0, limit);
export const demoUserRuns = (installId: string): RunRow[] => allRuns().filter((r) => r.install_id === installId);
export const demoUserLabel = (installId: string): string => USERS.find((u) => u.id === installId)?.label ?? "";

export function demoUserLocations(installId: string): LocationRow[] {
  const u = USERS.find((x) => x.id === installId);
  if (!u) return [];
  const mine = demoUserRuns(installId);
  const out: LocationRow[] = [{ city: u.city, region: u.region, country: u.country, visits: mine.length, last_seen: mine[0].ts }];
  if (u.country === "IN") out.push({ city: "Pune", region: "MH", country: "IN", visits: 2, last_seen: mine[mine.length - 1].ts });
  return out;
}

export function demoDailyCost(days: number): DailyCost[] {
  const byDay = new Map<string, DailyCost>();
  for (const r of allRuns()) {
    if (Date.parse(r.ts) < Date.now() - days * DAY) continue;
    const day = r.ts.slice(0, 10);
    const cur = byDay.get(day) ?? { day, cost: 0, runs: 0 };
    cur.cost += r.cost_usd ?? 0;
    cur.runs += 1;
    byDay.set(day, cur);
  }
  return [...byDay.values()].sort((a, b) => a.day.localeCompare(b.day));
}

export function demoTokens(): TokenListRow[] {
  const runs = allRuns();
  return USERS.map((u, i) => {
    const mine = runs.filter((r) => r.install_id === u.id);
    const sum = (f: (r: RunRow) => boolean) => mine.filter(f).reduce((s, r) => s + (r.cost_usd ?? 0), 0);
    const today = new Date().toISOString().slice(0, 10);
    return {
      id: `${i + 1}0000000-aaaa-4bbb-8ccc-00000000000${i + 1}`,
      label: u.label,
      daily_budget_usd: i === 3 ? 0.05 : 5,
      created_at: new Date(Date.now() - (20 - i) * DAY).toISOString(),
      revoked_at: i === 4 ? new Date(Date.now() - 2 * DAY).toISOString() : null,
      last_used_at: mine[0].ts,
      calls: mine.length * 11,
      spend_today: i === 3 ? 0.062 : sum((r) => r.ts.startsWith(today)),
      spend_30d: sum(() => true),
      spend_total: sum(() => true),
    };
  });
}
