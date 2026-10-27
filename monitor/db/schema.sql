-- CMI usage monitor schema. Applied automatically by the app on first use
-- (src/lib/db.ts ensureSchema) and idempotent, so it is safe to also run by hand:
--   psql "$DATABASE_URL" -f monitor/db/schema.sql
-- or from any SQL console against the Neon database.

CREATE TABLE IF NOT EXISTS events (
    event_id            TEXT PRIMARY KEY,          -- client UUID: retries are deduped
    type                TEXT NOT NULL CHECK (type IN ('run_completed', 'app_started')),
    install_id          TEXT NOT NULL,
    user_label          TEXT NOT NULL DEFAULT '',  -- baked per distribution (--user at build time)
    app_version         TEXT NOT NULL DEFAULT '',
    ts                  TIMESTAMPTZ NOT NULL,
    -- location, derived by Vercel from the request (city-level); the raw IP is never stored
    city                TEXT NOT NULL DEFAULT '',
    region              TEXT NOT NULL DEFAULT '',
    country             TEXT NOT NULL DEFAULT '',
    -- run_completed only; NULL/'' for app_started
    run_id              TEXT NOT NULL DEFAULT '',
    title               TEXT NOT NULL DEFAULT '',  -- document title only, never body text
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

-- One row per install: the name the user typed into the agent. When it changes, the old current_name
-- moves to previous_name (see upsertInstallName in src/lib/events.ts).
CREATE TABLE IF NOT EXISTS installs (
    install_id       TEXT PRIMARY KEY,
    current_name     TEXT NOT NULL DEFAULT '',
    previous_name    TEXT NOT NULL DEFAULT '',
    name_updated_at  TIMESTAMPTZ               -- ts of the event that set current_name (ordering guard)
);
