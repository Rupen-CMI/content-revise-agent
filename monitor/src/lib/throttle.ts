import { createHash } from "node:crypto";
import { getPool, q } from "./db";
import { demoEnabled } from "./demo";

// Login brute-force throttle. Serverless instances share no memory, so the counters live in Postgres.
// Fails OPEN on a database error: the dashboard is useless without the database anyway, and locking
// the owner out because the throttle table is unreachable would be worse than a missed throttle.

const WINDOW = "15 minutes";
export const MAX_FAILURES_PER_IP = 5;
export const MAX_FAILURES_GLOBAL = 40; // across all IPs: slows a distributed guess

const TABLE_SQL = `
CREATE TABLE IF NOT EXISTS login_failures (
    id      BIGSERIAL PRIMARY KEY,
    ip_hash TEXT NOT NULL,
    ts      TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS login_failures_ts_idx ON login_failures (ts DESC);
CREATE INDEX IF NOT EXISTS login_failures_ip_ts_idx ON login_failures (ip_hash, ts DESC);
`;

let _table: Promise<void> | null = null;
function ensureTable(): Promise<void> {
  if (!_table) {
    _table = getPool()
      .query(TABLE_SQL)
      .then(() => undefined)
      .catch((e) => {
        _table = null;
        throw e;
      });
  }
  return _table;
}

/** Vercel overwrites x-forwarded-for at the edge, so its first entry is the real client address. */
export function clientIp(headers: Headers): string {
  return (headers.get("x-forwarded-for") ?? "").split(",")[0].trim() || headers.get("x-real-ip") || "unknown";
}

/** Salted hash: the raw address is never stored. */
export function ipKey(ip: string, salt: string): string {
  return createHash("sha256").update(`${salt}|${ip}`).digest("hex").slice(0, 32);
}

export async function isLockedOut(ipHash: string): Promise<boolean> {
  if (demoEnabled()) return false;
  try {
    await ensureTable();
    const rows = await q<{ mine: number; total: number }>(
      `SELECT COUNT(*) FILTER (WHERE ip_hash = $1)::int AS mine, COUNT(*)::int AS total
         FROM login_failures WHERE ts > now() - interval '${WINDOW}'`,
      [ipHash],
    );
    return (rows[0]?.mine ?? 0) >= MAX_FAILURES_PER_IP || (rows[0]?.total ?? 0) >= MAX_FAILURES_GLOBAL;
  } catch (e) {
    console.error("[login] throttle check failed (allowing):", e);
    return false;
  }
}

export async function recordFailure(ipHash: string): Promise<void> {
  if (demoEnabled()) return;
  try {
    await ensureTable();
    await getPool().query(`INSERT INTO login_failures (ip_hash) VALUES ($1)`, [ipHash]);
    await getPool().query(`DELETE FROM login_failures WHERE ts < now() - interval '1 day'`);
  } catch (e) {
    console.error("[login] could not record failure:", e);
  }
}

export async function clearFailures(ipHash: string): Promise<void> {
  if (demoEnabled()) return;
  try {
    await getPool().query(`DELETE FROM login_failures WHERE ip_hash = $1`, [ipHash]);
  } catch {
    /* best effort */
  }
}
