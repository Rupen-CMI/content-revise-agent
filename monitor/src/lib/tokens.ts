import { createHash, randomBytes } from "node:crypto";
import { getPool, q } from "./db";
import { demoEnabled, demoTokens } from "./demo";
import { ensureProxySchema } from "./proxy-schema";

const TOKEN_PREFIX = "cmi_";

/** 256 random bits, URL-safe. Shown once at creation; only its hash is stored. */
export function generateToken(): string {
  return TOKEN_PREFIX + randomBytes(32).toString("base64url");
}

/** The token is high-entropy random, so a plain SHA-256 is sufficient (no slow KDF needed). */
export function hashToken(token: string): string {
  return createHash("sha256").update(token).digest("hex");
}

export interface TokenRow {
  id: string;
  label: string;
  daily_budget_usd: number;
}

/** What the proxy needs per call, fetched in ONE round trip: identity, today's spend and the call rate. */
export interface TokenUsage extends TokenRow {
  spend_today: number;
  calls_last_minute: number;
}

const TODAY_UTC = `(date_trunc('day', now() AT TIME ZONE 'UTC') AT TIME ZONE 'UTC')`;

function plausibleToken(token: string): boolean {
  return token.startsWith(TOKEN_PREFIX) && token.length <= 200;
}

/** Identity only (used by /api/ingest). */
export async function lookupToken(token: string): Promise<TokenRow | null> {
  if (!plausibleToken(token)) return null;
  await ensureProxySchema();
  const rows = await q<TokenRow>(
    `SELECT id, label, daily_budget_usd FROM install_tokens
      WHERE token_hash = $1 AND revoked_at IS NULL`,
    [hashToken(token)],
  );
  return rows[0] ?? null;
}

/** Identity + spend + rate for the proxy, in a single query. */
export async function findActiveToken(token: string): Promise<TokenUsage | null> {
  if (!plausibleToken(token)) return null;
  await ensureProxySchema();
  const rows = await q<TokenUsage>(
    `SELECT t.id, t.label, t.daily_budget_usd,
            COALESCE((SELECT SUM(c.cost_usd) FROM llm_calls c
                       WHERE c.token_id = t.id AND c.ts >= ${TODAY_UTC}), 0) AS spend_today,
            (SELECT COUNT(*) FROM llm_calls c
              WHERE c.token_id = t.id AND c.ts > now() - interval '1 minute')::int AS calls_last_minute
       FROM install_tokens t
      WHERE t.token_hash = $1 AND t.revoked_at IS NULL`,
    [hashToken(token)],
  );
  const r = rows[0];
  return r ? { ...r, spend_today: Number(r.spend_today), calls_last_minute: Number(r.calls_last_minute) } : null;
}

export async function spendTodayAll(): Promise<number> {
  const rows = await q<{ s: number }>(`SELECT COALESCE(SUM(cost_usd), 0) AS s FROM llm_calls WHERE ts >= ${TODAY_UTC}`);
  return Number(rows[0]?.s ?? 0);
}

export interface CallRecord {
  tokenId: string;
  installId: string;
  model: string;
  promptTokens: number;
  completionTokens: number;
  cachedTokens: number;
  costUsd: number;
  durationMs: number;
  httpStatus: number;
  city: string;
  region: string;
  country: string;
}

export async function recordCall(c: CallRecord): Promise<void> {
  // one round trip: insert the call and bump last_used_at together
  await getPool().query(
    `WITH ins AS (
       INSERT INTO llm_calls
         (token_id, install_id, model, prompt_tokens, completion_tokens, cached_tokens,
          cost_usd, duration_ms, http_status, city, region, country)
       VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12)
       RETURNING token_id)
     UPDATE install_tokens SET last_used_at = now() WHERE id = (SELECT token_id FROM ins)`,
    [c.tokenId, c.installId, c.model, c.promptTokens, c.completionTokens, c.cachedTokens,
     c.costUsd, c.durationMs, c.httpStatus, c.city, c.region, c.country],
  );
}

// ---------------------------------------------------------------- admin (session-protected API + CLI)

export async function createToken(label: string, dailyBudgetUsd: number): Promise<{ id: string; token: string }> {
  await ensureProxySchema();
  const token = generateToken();
  const rows = await q<{ id: string }>(
    `INSERT INTO install_tokens (token_hash, label, daily_budget_usd) VALUES ($1,$2,$3) RETURNING id`,
    [hashToken(token), label.trim().slice(0, 120), dailyBudgetUsd],
  );
  return { id: rows[0].id, token };
}

export async function revokeToken(id: string): Promise<boolean> {
  await ensureProxySchema();
  const res = await getPool().query(
    `UPDATE install_tokens SET revoked_at = now() WHERE id = $1 AND revoked_at IS NULL`,
    [id],
  );
  return (res.rowCount ?? 0) > 0;
}

export interface TokenListRow {
  id: string;
  label: string;
  daily_budget_usd: number;
  created_at: string;
  revoked_at: string | null;
  last_used_at: string | null;
  calls: number;
  spend_today: number;
  spend_30d: number;
  spend_total: number;
}

export async function listTokens(): Promise<TokenListRow[]> {
  if (demoEnabled()) return demoTokens();
  await ensureProxySchema();
  return q<TokenListRow>(`
    SELECT t.id, t.label, t.daily_budget_usd, t.created_at, t.revoked_at, t.last_used_at,
           COUNT(c.id)::int AS calls,
           COALESCE(SUM(c.cost_usd) FILTER (WHERE c.ts >= ${TODAY_UTC}), 0) AS spend_today,
           COALESCE(SUM(c.cost_usd) FILTER (WHERE c.ts > now() - interval '30 days'), 0) AS spend_30d,
           COALESCE(SUM(c.cost_usd), 0) AS spend_total
      FROM install_tokens t
      LEFT JOIN llm_calls c ON c.token_id = t.id
     GROUP BY t.id
     ORDER BY t.created_at DESC
  `);
}
