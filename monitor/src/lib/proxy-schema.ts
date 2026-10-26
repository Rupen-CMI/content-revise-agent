import { getPool } from "./db";

// Embedded (not read from disk) so it is always bundled on Vercel. Keep in sync with db/proxy.sql.
export const PROXY_SCHEMA_SQL = `
CREATE TABLE IF NOT EXISTS install_tokens (
    id                UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    token_hash        TEXT NOT NULL UNIQUE,
    label             TEXT NOT NULL,
    daily_budget_usd  DOUBLE PRECISION NOT NULL DEFAULT 5,
    created_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    revoked_at        TIMESTAMPTZ,
    last_used_at      TIMESTAMPTZ
);
CREATE TABLE IF NOT EXISTS llm_calls (
    id                BIGSERIAL PRIMARY KEY,
    token_id          UUID NOT NULL REFERENCES install_tokens(id),
    ts                TIMESTAMPTZ NOT NULL DEFAULT now(),
    install_id        TEXT NOT NULL DEFAULT '',
    model             TEXT NOT NULL DEFAULT '',
    prompt_tokens     BIGINT NOT NULL DEFAULT 0,
    completion_tokens BIGINT NOT NULL DEFAULT 0,
    cached_tokens     BIGINT NOT NULL DEFAULT 0,
    cost_usd          DOUBLE PRECISION NOT NULL DEFAULT 0,
    duration_ms       INTEGER NOT NULL DEFAULT 0,
    http_status       INTEGER NOT NULL DEFAULT 0,
    city              TEXT NOT NULL DEFAULT '',
    region            TEXT NOT NULL DEFAULT '',
    country           TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS llm_calls_token_ts_idx ON llm_calls (token_id, ts DESC);
CREATE INDEX IF NOT EXISTS llm_calls_ts_idx       ON llm_calls (ts DESC);
`;

let _schema: Promise<void> | null = null;

/** Idempotent auto-init, once per serverless instance (same pattern as db.ts ensureSchema). */
export function ensureProxySchema(): Promise<void> {
  if (!_schema) {
    _schema = getPool()
      .query(PROXY_SCHEMA_SQL)
      .then(() => undefined)
      .catch((e) => {
        _schema = null;
        throw e;
      });
  }
  return _schema;
}
