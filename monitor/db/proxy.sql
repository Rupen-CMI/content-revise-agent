-- LLM proxy tables. Applied automatically on first proxy use (src/lib/proxy-schema.ts) and
-- idempotent, so it is also safe to run by hand or via `npm run token -- init`.

CREATE TABLE IF NOT EXISTS install_tokens (
    id                UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    token_hash        TEXT NOT NULL UNIQUE,        -- sha256 hex of the token; the token itself is never stored
    label             TEXT NOT NULL,               -- who it was issued to (matches --user at build time)
    daily_budget_usd  DOUBLE PRECISION NOT NULL DEFAULT 5,
    created_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    revoked_at        TIMESTAMPTZ,
    last_used_at      TIMESTAMPTZ
);

CREATE TABLE IF NOT EXISTS llm_calls (
    id                BIGSERIAL PRIMARY KEY,
    token_id          UUID NOT NULL REFERENCES install_tokens(id),
    ts                TIMESTAMPTZ NOT NULL DEFAULT now(),
    install_id        TEXT NOT NULL DEFAULT '',    -- optional X-Install-Id header, ties calls to telemetry events
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
