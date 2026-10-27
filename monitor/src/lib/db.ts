import { Pool } from "@neondatabase/serverless";

// Schema is embedded (not read from disk at runtime) so it is always bundled on Vercel.
// Keep in sync with monitor/db/schema.sql.
export const SCHEMA_SQL = `
CREATE TABLE IF NOT EXISTS events (
    event_id            TEXT PRIMARY KEY,
    type                TEXT NOT NULL CHECK (type IN ('run_completed', 'app_started')),
    install_id          TEXT NOT NULL,
    user_label          TEXT NOT NULL DEFAULT '',
    app_version         TEXT NOT NULL DEFAULT '',
    ts                  TIMESTAMPTZ NOT NULL,
    city                TEXT NOT NULL DEFAULT '',
    region              TEXT NOT NULL DEFAULT '',
    country             TEXT NOT NULL DEFAULT '',
    run_id              TEXT NOT NULL DEFAULT '',
    title               TEXT NOT NULL DEFAULT '',
    word_count          INTEGER,
    status              TEXT CHECK (status IS NULL OR status IN ('ok', 'error')),
    duration_s          DOUBLE PRECISION,
    cost_usd            DOUBLE PRECISION,
    prompt_tokens       BIGINT,
    completion_tokens   BIGINT,
    cached_tokens       BIGINT,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS events_install_ts_idx ON events (install_id, ts DESC);
CREATE INDEX IF NOT EXISTS events_type_ts_idx    ON events (type, ts DESC);
CREATE TABLE IF NOT EXISTS installs (
    install_id       TEXT PRIMARY KEY,
    current_name     TEXT NOT NULL DEFAULT '',
    previous_name    TEXT NOT NULL DEFAULT '',
    name_updated_at  TIMESTAMPTZ
);
`;

let _pool: Pool | null = null;

export function getPool(): Pool {
  if (!_pool) {
    const url = process.env.DATABASE_URL;
    if (!url) throw new Error("DATABASE_URL is not set");
    _pool = new Pool({ connectionString: url });
  }
  return _pool;
}

/** Parameterized query returning rows. */
export async function q<T = Record<string, unknown>>(
  text: string,
  params?: unknown[],
): Promise<T[]> {
  await ensureSchema(); // idempotent + memoised; a fresh deploy must not fail before the first event arrives
  const res = await getPool().query(text, params as never[]);
  return res.rows as T[];
}

let _schema: Promise<void> | null = null;

/** Idempotent auto-init: applies the schema once per serverless instance. */
export function ensureSchema(): Promise<void> {
  if (!_schema) {
    _schema = getPool()
      .query(SCHEMA_SQL)
      .then(() => undefined)
      .catch((e) => {
        _schema = null; // allow a retry on the next request
        throw e;
      });
  }
  return _schema;
}
