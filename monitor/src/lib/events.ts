import { z } from "zod";
import { ensureSchema, getPool, q } from "./db";
import * as demo from "./demo";

// ---------------------------------------------------------------- validation (ingest)

/** Free-text field: trim and truncate to `max` (cap, don't reject — a too-long
 * label/title should still be recorded, just shortened). Optional; defaults to "". */
function capped(max: number) {
  return z
    .string()
    .transform((s) => s.trim().slice(0, max))
    .default("");
}

// Accepts an ISO-8601 timestamp (what backend/telemetry.py sends) or an epoch
// number (seconds or ms); normalizes to ISO-8601 for the timestamptz column.
const tsSchema = z
  .union([z.string(), z.number()])
  .transform((v, ctx) => {
    const epoch = typeof v === "number" ? (v < 1e12 ? v * 1000 : v) : Date.parse(v);
    if (!Number.isFinite(epoch)) {
      ctx.addIssue({ code: z.ZodIssueCode.custom, message: "invalid timestamp" });
      return z.NEVER;
    }
    // a client clock can be a little fast, or forged: anything more than an hour ahead is recorded as "now"
    const now = Date.now();
    return new Date(epoch > now + 3_600_000 ? now : epoch).toISOString();
  });

/** A person's typed-in display name: control characters dropped, whitespace collapsed, trimmed, capped. */
function personName(max: number) {
  return z
    .string()
    // tabs/newlines become spaces first (a pasted name), then any remaining control character is dropped
    .transform((s) => s.replace(/\s+/g, " ").replace(/[\u0000-\u001f\u007f]/g, "").replace(/\s+/g, " ").trim().slice(0, max))
    .default("");
}

const baseFields = {
  event_id: z.string().trim().uuid().max(64),
  install_id: z.string().trim().min(8).max(64),
  user_label: capped(120),
  // The name the user typed into the agent. Carried by EVERY event so the dashboard heals itself if a
  // name_updated event was lost; the server only applies it when the event is the newest one seen.
  user_name: personName(60),
  app_version: capped(40),
  ts: tsSchema,
};

const runCompletedSchema = z.object({
  ...baseFields,
  type: z.literal("run_completed"),
  run_id: capped(64),
  title: capped(200),
  word_count: z.number().int().min(0).max(1_000_000).nullable().optional(),
  status: z.enum(["ok", "error"]),
  duration_s: z.number().min(0).max(2_592_000).nullable().optional(),
  cost_usd: z.number().min(0).max(1_000_000).nullable().optional(),
  prompt_tokens: z.number().int().min(0).max(1e12).nullable().optional(),
  completion_tokens: z.number().int().min(0).max(1e12).nullable().optional(),
  cached_tokens: z.number().int().min(0).max(1e12).nullable().optional(),
});

// A pipeline step ended (passed or failed). cost_usd / *_tokens are what THAT STEP added, not a running
// total, so the dashboard can simply sum every event of a run. Sent after each of steps 1-4, which is
// what lets a run abandoned halfway still show up with what it actually cost.
const runProgressSchema = z.object({
  ...baseFields,
  type: z.literal("run_progress"),
  run_id: capped(64),
  title: capped(200),
  step: capped(24),
  duration_s: z.number().min(0).max(2_592_000).nullable().optional(),
  cost_usd: z.number().min(0).max(1_000_000).nullable().optional(),
  prompt_tokens: z.number().int().min(0).max(1e12).nullable().optional(),
  completion_tokens: z.number().int().min(0).max(1e12).nullable().optional(),
  cached_tokens: z.number().int().min(0).max(1e12).nullable().optional(),
});

const appStartedSchema = z.object({
  ...baseFields,
  type: z.literal("app_started"),
});

// The user typed a new display name into the agent. Not stored in `events`; it only updates `installs`.
const nameUpdatedSchema = z.object({
  ...baseFields,
  type: z.literal("name_updated"),
});

export const ingestSchema = z.discriminatedUnion("type", [
  runCompletedSchema,
  runProgressSchema,
  appStartedSchema,
  nameUpdatedSchema,
]);

export type IngestEvent = z.infer<typeof ingestSchema>;
/** The event kinds that become rows in `events` (everything except name_updated). */
export type StoredEvent = Exclude<IngestEvent, { type: "name_updated" }>;

export const MIN_NAME_LENGTH = 2;

/** City/region/country derived by Vercel from the request edge; raw IP is never stored. */
export interface Location {
  city: string;
  region: string;
  country: string;
}

function safeDecode(value: string | null, cap: number): string {
  if (!value) return "";
  let out = value;
  try {
    out = decodeURIComponent(value); // x-vercel-ip-city arrives URL-encoded
  } catch {
    /* keep the raw value */
  }
  return out.slice(0, cap);
}

export function locationFromHeaders(headers: Headers): Location {
  return {
    city: safeDecode(headers.get("x-vercel-ip-city"), 100),
    region: safeDecode(headers.get("x-vercel-ip-country-region"), 100),
    country: safeDecode(headers.get("x-vercel-ip-country"), 8),
  };
}

/** Idempotent insert; returns true when the row was newly inserted. */
/**
 * Record the display name an install reports. Rule: when the name CHANGES, the old current_name moves
 * to previous_name and current_name becomes the new one; the very first name leaves previous_name empty.
 *
 * Every event carries the name and events can arrive late or twice (offline outbox, retries), so a name is
 * only applied when its event is NEWER than the one that set the stored name (name_updated_at). A repeat of
 * the current name just advances that timestamp, so a stale older rename arriving afterwards can't win.
 */
export async function upsertInstallName(installId: string, name: string, ts: string): Promise<void> {
  if (name.length < MIN_NAME_LENGTH) return; // events sent before the user has chosen a name carry ""
  await ensureSchema();
  await getPool().query(
    `INSERT INTO installs (install_id, current_name, name_updated_at)
     VALUES ($1, $2, $3)
     ON CONFLICT (install_id) DO UPDATE SET
       previous_name = CASE WHEN installs.current_name <> '' AND installs.current_name <> EXCLUDED.current_name
                            THEN installs.current_name ELSE installs.previous_name END,
       current_name = EXCLUDED.current_name,
       name_updated_at = EXCLUDED.name_updated_at
     WHERE installs.name_updated_at IS NULL OR EXCLUDED.name_updated_at > installs.name_updated_at`,
    [installId, name, ts],
  );
}

export async function insertEvent(event: StoredEvent, loc: Location): Promise<boolean> {
  await ensureSchema();
  const run = event.type === "run_completed" || event.type === "run_progress" ? event : null;
  const completed = event.type === "run_completed" ? event : null;
  const res = await getPool().query(
    `INSERT INTO events
       (event_id, type, install_id, user_label, app_version, ts, city, region, country,
        run_id, title, word_count, status, duration_s, cost_usd,
        prompt_tokens, completion_tokens, cached_tokens, step)
     VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14, $15, $16, $17, $18, $19)
     ON CONFLICT (event_id) DO NOTHING`,
    [
      event.event_id,
      event.type,
      event.install_id,
      event.user_label,
      event.app_version,
      event.ts,
      loc.city,
      loc.region,
      loc.country,
      run?.run_id ?? "",
      run?.title ?? "",
      completed?.word_count ?? null,
      completed?.status ?? null,
      run?.duration_s ?? null,
      run?.cost_usd ?? null,
      run?.prompt_tokens ?? null,
      run?.completion_tokens ?? null,
      run?.cached_tokens ?? null,
      event.type === "run_progress" ? event.step : completed ? "finalize" : "",
    ],
  );
  return (res.rowCount ?? 0) > 0;
}

// ---------------------------------------------------------------- dashboard queries

// A run is reported as a series of events that all share its run_id: one run_progress per pipeline step
// and a run_completed at the end. Each carries only the cost/tokens it ADDED, so a run's cost is simply the
// SUM over its events, a run abandoned after step 2 still counts what it spent, and a "Regenerate draft"
// (a second finalize on the same run) adds its cost to the same run instead of inflating the run count.
// (Events from before run_progress existed have one run_completed per run, which fits the same model.)
const RUN_EVENT = `type IN ('run_progress', 'run_completed')`;
const RUN_KEY = `COALESCE(NULLIF(run_id, ''), event_id)`;
/** A run with no completion whose last event is older than this is shown as "unfinished", not "running". */
const RUNNING_WINDOW = `interval '30 minutes'`;

export type RunStatus = "ok" | "error" | "running" | "unfinished";

export interface InstallRow {
  install_id: string;
  label: string;
  /** the name the user typed into the agent; previous_name is the one it replaced ('' until they rename) */
  current_name: string;
  previous_name: string;
  last_seen: string;
  last_location: string | null;
  runs: number;
  total_cost: number;
  total_tokens: string; // COUNT/SUM of BIGINT comes back as string from pg
}

export async function listInstalls(): Promise<InstallRow[]> {
  if (demo.demoEnabled()) return demo.demoInstalls();
  await ensureSchema();
  const rows = await q<InstallRow>(`
    SELECT e.install_id,
           (SELECT e2.user_label FROM events e2
             WHERE e2.install_id = e.install_id AND e2.user_label <> ''
             ORDER BY e2.ts DESC LIMIT 1) AS label,
           COALESCE((SELECT i.current_name FROM installs i WHERE i.install_id = e.install_id), '') AS current_name,
           COALESCE((SELECT i.previous_name FROM installs i WHERE i.install_id = e.install_id), '') AS previous_name,
           MAX(e.ts) AS last_seen,
           (SELECT CASE WHEN e3.city <> '' AND e3.country <> '' THEN e3.city || ', ' || e3.country
                        WHEN e3.city <> '' THEN e3.city
                        ELSE e3.country END
              FROM events e3
             WHERE e3.install_id = e.install_id AND (e3.city <> '' OR e3.country <> '')
             ORDER BY e3.ts DESC LIMIT 1) AS last_location,
           COUNT(DISTINCT COALESCE(NULLIF(e.run_id, ''), e.event_id))
             FILTER (WHERE e.type IN ('run_progress', 'run_completed')) AS runs,
           COALESCE(SUM(e.cost_usd) FILTER (WHERE e.type IN ('run_progress', 'run_completed')), 0) AS total_cost,
           COALESCE(SUM(COALESCE(e.prompt_tokens, 0) + COALESCE(e.completion_tokens, 0))
             FILTER (WHERE e.type IN ('run_progress', 'run_completed')), 0) AS total_tokens
    FROM events e
    GROUP BY e.install_id
    ORDER BY MAX(e.ts) DESC
  `);
  return rows;
}

export interface Stats {
  active_users: number;
  total_runs: number;
  total_cost: number;
  cost_7d: number;
  /** spend since 00:00 UTC today, the same day boundary the daily chart uses */
  cost_today: number;
}

export async function dashboardStats(): Promise<Stats> {
  if (demo.demoEnabled()) return demo.demoStats();
  await ensureSchema();
  const rows = await q<Stats>(`
    SELECT (SELECT COUNT(DISTINCT install_id) FROM events
              WHERE ts > now() - interval '7 days')::int AS active_users,
           (SELECT COUNT(DISTINCT install_id || ':' || ${RUN_KEY}) FROM events WHERE ${RUN_EVENT})::int AS total_runs,
           (SELECT COALESCE(SUM(cost_usd), 0) FROM events WHERE ${RUN_EVENT}) AS total_cost,
           (SELECT COALESCE(SUM(cost_usd), 0) FROM events
              WHERE ${RUN_EVENT} AND ts > now() - interval '7 days') AS cost_7d,
           (SELECT COALESCE(SUM(cost_usd), 0) FROM events
              WHERE ${RUN_EVENT} AND (ts AT TIME ZONE 'UTC')::date = (now() AT TIME ZONE 'UTC')::date) AS cost_today
  `);
  return rows[0];
}

/** One row per run, folded from all of that run's events. */
export interface RunRow {
  run_key: string;
  install_id: string;
  user_label: string;
  title: string;
  /** ok / error once finalized; running / unfinished while it has no completion (see RUNNING_WINDOW) */
  status: RunStatus;
  /** the last pipeline step reported: step1..step4, or finalize */
  step: string;
  word_count: number | null;
  /** summed over the run's events; null only if no event carried a price */
  cost_usd: number | null;
  duration_s: number | null;
  prompt_tokens: number | null;
  completion_tokens: number | null;
  city: string;
  region: string;
  country: string;
  /** time of the run's latest event */
  ts: string;
}

// `$where` is a fixed SQL fragment from this file (never user input); values go through parameters.
function runRowsSql(where: string, limitParam: string): string {
  return `
    SELECT ${RUN_KEY} AS run_key,
           install_id,
           COALESCE((ARRAY_AGG(user_label ORDER BY ts DESC) FILTER (WHERE user_label <> ''))[1], '') AS user_label,
           COALESCE((ARRAY_AGG(title ORDER BY ts DESC) FILTER (WHERE title <> ''))[1], '') AS title,
           CASE WHEN BOOL_OR(type = 'run_completed')
                  THEN COALESCE((ARRAY_AGG(status ORDER BY ts DESC) FILTER (WHERE type = 'run_completed'))[1], 'ok')
                WHEN MAX(ts) > now() - ${RUNNING_WINDOW} THEN 'running'
                ELSE 'unfinished' END AS status,
           COALESCE((ARRAY_AGG(step ORDER BY ts DESC))[1], '') AS step,
           MAX(word_count) AS word_count,
           SUM(cost_usd)::float8 AS cost_usd,
           MAX(duration_s) AS duration_s,
           SUM(prompt_tokens)::float8 AS prompt_tokens,
           SUM(completion_tokens)::float8 AS completion_tokens,
           (ARRAY_AGG(city ORDER BY ts DESC))[1] AS city,
           (ARRAY_AGG(region ORDER BY ts DESC))[1] AS region,
           (ARRAY_AGG(country ORDER BY ts DESC))[1] AS country,
           MAX(ts) AS ts
      FROM events
     WHERE ${RUN_EVENT}${where}
     GROUP BY 1, install_id
     ORDER BY MAX(ts) DESC
     LIMIT ${limitParam}`;
}

export async function recentRuns(limit = 30): Promise<RunRow[]> {
  if (demo.demoEnabled()) return demo.demoRecentRuns(limit);
  await ensureSchema();
  return q<RunRow>(runRowsSql("", "$1"), [limit]);
}

export interface DailyCost {
  day: string;
  cost: number;
  runs: number;
}

export async function dailyCost(days = 30): Promise<DailyCost[]> {
  if (demo.demoEnabled()) return demo.demoDailyCost(days);
  await ensureSchema();
  return q<DailyCost>(
    // per-event amounts, so each day shows what was actually spent that day (a run spanning midnight splits)
    `SELECT (ts AT TIME ZONE 'UTC')::date::text AS day,
            COALESCE(SUM(cost_usd), 0) AS cost,
            COUNT(DISTINCT install_id || ':' || ${RUN_KEY})::int AS runs
       FROM events
      WHERE ${RUN_EVENT} AND ts > now() - ($1 || ' days')::interval
      GROUP BY 1
      ORDER BY 1`,
    [String(days)],
  );
}

export async function userRuns(installId: string, limit = 100): Promise<RunRow[]> {
  if (demo.demoEnabled()) return demo.demoUserRuns(installId).slice(0, limit);
  await ensureSchema();
  return q<RunRow>(runRowsSql(" AND install_id = $1", "$2"), [installId, limit]);
}

export interface LocationRow {
  city: string;
  region: string;
  country: string;
  visits: number;
  last_seen: string;
}

export async function userLocations(installId: string, limit = 50): Promise<LocationRow[]> {
  if (demo.demoEnabled()) return demo.demoUserLocations(installId);
  await ensureSchema();
  return q<LocationRow>(
    `SELECT city, region, country, COUNT(*)::int AS visits, MAX(ts) AS last_seen
       FROM events
      WHERE install_id = $1
      GROUP BY city, region, country
      ORDER BY MAX(ts) DESC
      LIMIT $2`,
    [installId, limit],
  );
}

export async function userLabel(installId: string): Promise<string> {
  if (demo.demoEnabled()) return demo.demoUserLabel(installId);
  await ensureSchema();
  const rows = await q<{ label: string }>(
    `SELECT user_label AS label FROM events
      WHERE install_id = $1 AND user_label <> ''
      ORDER BY ts DESC LIMIT 1`,
    [installId],
  );
  return rows[0]?.label ?? "";
}

export interface InstallNames {
  current: string;
  previous: string;
}

export async function userNames(installId: string): Promise<InstallNames> {
  if (demo.demoEnabled()) return demo.demoUserNames(installId);
  await ensureSchema();
  const rows = await q<{ current_name: string; previous_name: string }>(
    `SELECT current_name, previous_name FROM installs WHERE install_id = $1`,
    [installId],
  );
  return { current: rows[0]?.current_name ?? "", previous: rows[0]?.previous_name ?? "" };
}
