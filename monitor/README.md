# CMI Usage Monitor

Dashboard for the CMI Blog Revamp Agent: which end-user regenerated which
documents, when, from where (city-level, derived by Vercel), and what each run
cost in DeepSeek tokens/USD. The agent (backend/telemetry.py in the main repo)
POSTs events to this app's `/api/ingest`; there is no inbound auth beyond a
shared ingest key, and no document body text is ever sent or stored — only
titles and metadata.

- Next.js (App Router, TypeScript), server components only — no client-side data fetching.
- Postgres on [Neon](https://neon.tech) (serverless driver `@neondatabase/serverless`; Vercel has no persistent disk).
- Plain SQL, no ORM. Schema: `db/schema.sql`, applied automatically (idempotent) on first ingest/dashboard load.
- Auth: single shared `ADMIN_PASSWORD`; session is an httpOnly cookie signed with HMAC-SHA256 (`SESSION_SECRET`).
- Unit tests: `npm test` (vitest) covers ingest validation, location-header decoding, secret comparison and session tokens.

## Deploy

### 1. Create the Neon database

1. Sign up at <https://neon.tech> (free tier is fine).
2. Create a project; copy the **pooled** connection string, e.g.
   `postgresql://USER:PASSWORD@ep-xxxx-pooler.REGION.aws.neon.tech/neondb?sslmode=require`
   (use the pooler hostname — Vercel functions are serverless and would exhaust
   direct connections).

### 2. Generate the secrets

Any long random strings, e.g. in PowerShell:

```
-join ((48..57)+(97..122) | Get-Random -Count 40 | % {[char]$_})
```

You need three: `INGEST_KEY` (the agent authenticates with this),
`ADMIN_PASSWORD` (dashboard login), `SESSION_SECRET` (cookie signing).

### 3. Deploy to Vercel

```bash
cd monitor
npm install
npx vercel link        # or: vercel project add cmi-monitor
npx vercel env add DATABASE_URL production      # paste the Neon pooled string
npx vercel env add INGEST_KEY production
npx vercel env add ADMIN_PASSWORD production
npx vercel env add SESSION_SECRET production
npx vercel --prod
```

(or import the `monitor/` folder as a Vercel project in the web UI and set the
env vars there — same result).

### 4. Create the schema (optional)

The app auto-initializes the schema on first use (`CREATE TABLE IF NOT
EXISTS`). To run it explicitly instead:

```bash
psql "$DATABASE_URL" -f db/schema.sql
# or open the Neon SQL editor and paste the contents of db/schema.sql
```

### 5. Log in

Open the deployed URL → you are redirected to `/login` → enter
`ADMIN_PASSWORD`. Everything except `/login` and `/api/ingest` is protected by
middleware.

## Point the agent at it

In the repo root `.env` (the values get baked into each distribution by
`packaging/build_package.py`):

```
MONITOR_URL=https://<your-project>.vercel.app
MONITOR_INGEST_KEY=<same value as INGEST_KEY on Vercel>
```

Then build with a label per customer:

```
python packaging/build_package.py --user "Acme Realty"
```

The end-user agent then POSTs these events to `<MONITOR_URL>/api/ingest` (per-install token, or the legacy
`X-Ingest-Key`):

| Event | When | Carries |
|---|---|---|
| `app_started` | each launch | install, name, location |
| `name_updated` | the user typed or changed their display name | the name |
| `run_progress` | after **every** pipeline step 1-4, pass **or fail** | run id, step, and the cost/tokens **that step added** |
| `run_completed` | each `/finalize` (ok or error) | run id, status, word count, and the cost/tokens it added |

Cost and token fields are per-event amounts, never running totals, and every event of a run shares its
`run_id`. The dashboard sums them, so a run abandoned after step 2 still shows up (as `unfinished · Step 2`,
or `running · Step 3` while its last event is under 30 minutes old) with exactly what it cost, and
"Regenerate draft" adds to the same run instead of creating a second one. Events are idempotent on a
client-generated `event_id`, and failed sends are queued in a local outbox file and retried when
connectivity returns.

Without `MONITOR_URL` in `.env` the build bakes no monitor keys and telemetry
is a silent no-op, and that includes running the agent from the project folder: **a dev run is only tracked
if its `.env` sets `MONITOR_URL` plus `MONITOR_PROXY_TOKEN`** (mint one with
`npm run token -- create "<your name> (dev)" 5`). Dev runs still call DeepSeek directly with your own key.

## What is stored (and what is not)

Stored per event: install UUID, baked user label, app version, timestamp,
city/region/country (from Vercel geolocation headers), and for runs: run id,
document title, word count, ok/error, duration, USD cost, prompt/completion/cached tokens.

Never stored: the request IP, any document body text, prompts, LLM output text, or API keys.

## Local development

```bash
npm install
cp .env.example .env   # fill in real values
npm run dev            # http://localhost:3000 (location headers absent locally — fine)
npm test               # vitest unit tests
npm run build          # production build check
npm run lint           # eslint
```

## Note about git

The repo root `.gitignore` has a global `*.json` rule; `monitor/.gitignore`
re-includes this app's manifest files (`package.json`, `package-lock.json`,
`tsconfig.json`) so they can be committed normally. If `git add monitor/`
still skips them, add with `git add -f monitor/package.json` or add a
negation to the root `.gitignore`.

## LLM proxy (keeps the DeepSeek key off users' PCs)

`POST /api/v1/chat/completions` is an OpenAI-compatible pass-through to DeepSeek. The agent points its
OpenAI client at `<your-vercel-url>/api/v1` and uses a **per-install token** (`cmi_...`) as the API key.
The real `DEEPSEEK_API_KEY` exists only in the Vercel project's env.

What the proxy enforces on every call:

- **Auth** — `Authorization: Bearer cmi_...`; only the token's SHA-256 is stored; revoked tokens are refused instantly.
- **Request whitelist** — only `model, messages, temperature, top_p, max_tokens, stop, presence/frequency_penalty,
  response_format`. No tools, no streaming. `model` must be in `ALLOWED_MODELS` (default `deepseek-chat`);
  `max_tokens` is clamped to `PROXY_MAX_TOKENS_CAP` (default 20000).
- **Budgets** — per-token daily budget in USD (UTC day) → `429 daily_budget_exceeded`; optional
  `GLOBAL_DAILY_BUDGET_USD` across all tokens. **Currently switched off:** nothing is blocked, every call
  is still priced and recorded, so spend is tracked without limits. The logic, the stored per-token budget
  and `GLOBAL_DAILY_BUDGET_USD` are all kept; set `ENFORCE_BUDGETS=1` in Vercel to turn blocking back on.
  (The calls-per-minute limit is separate and always on.)
- **Accounting** — every call is recorded (`llm_calls`): tokens, cost (same pricing table as `backend/llm.py`),
  duration, status, city/region/country. This is the authoritative cost figure and cannot be skipped or faked
  by the client.
- **Limits** — `maxDuration = 120 s` (real calls peak ~48 s; Vercel allows 300 s on Hobby, 800 s on Pro) and a
  1 MB prompt cap (real prompts peak ~110 KB; Vercel's body cap is 4.5 MB).

### Set it up

1. Add `DEEPSEEK_API_KEY` to the Vercel project env (the proxy answers `500 not_configured` without it) and redeploy.
2. Issue a token per distribution (the plaintext is shown **once**):

   ```bash
   cd monitor
   vercel env pull .env.local        # gives the CLI your DATABASE_URL
   npm run token -- create "Priya" 5 # label, daily budget in USD
   npm run token -- list
   npm run token -- revoke <id>
   ```

   The same operations exist as `GET/POST/DELETE /api/tokens` (dashboard session required), and the
   **LLM tokens** page (`/tokens`) shows spend per token.
3. Build that user's copy with their token, passed explicitly (it is never read from the repo `.env`, whose
   `MONITOR_PROXY_TOKEN` is only your own dev token and is refused by the build):

   ```powershell
   $env:MONITOR_PROXY_TOKEN = "cmi_..."          # or: --proxy-token cmi_...
   python packaging/build_package.py --user "Priya"
   ```

### Quick check

```bash
curl -s "$MONITOR_URL/api/v1/chat/completions" \
  -H "Authorization: Bearer cmi_..." -H "Content-Type: application/json" \
  -d '{"model":"deepseek-chat","messages":[{"role":"user","content":"Say ok"}],"max_tokens":5}'
```


## Security model

| Surface | Who can use it | How |
|---|---|---|
| Dashboard pages, `/api/tokens` | the owner | `ADMIN_PASSWORD` login -> signed `__Host-` session cookie (HttpOnly, Secure, SameSite=Lax). Ends after **1 hour of inactivity** (sliding) or **12 hours** since sign-in, whichever comes first. Checked in middleware **and again** inside every page and admin route. Mutations also refuse cross-site requests (CSRF). |
| `POST /api/ingest` | an install | its own token (`Authorization: Bearer cmi_...`); the user label comes from the token, so one install cannot report as another. `INGEST_KEY` is only a legacy fallback for `--direct` builds and may be left unset. |
| `POST /api/v1/chat/completions` | an install | its own token; whitelisted params only, model allowlist, `max_tokens` cap, 400k-char prompt cap, per-token daily USD budget, per-token calls/minute limit, optional global daily cap. |

What is deliberately **not** possible:

- The DeepSeek key never reaches a user's PC, and DeepSeek's error text (e.g. "Insufficient Balance") is
  replaced by generic errors before it is sent to the agent. The real error is in the Vercel function log.
- Prompts and document text are never stored; only titles, token counts, costs and city-level location.
  The raw IP address is never stored (login throttling keeps only a salted hash for 24 h).
- Login is throttled: 5 failures per IP (and 40 overall) per 15 minutes locks login for that window.
- Idle timeout: every real request renews the session cookie (at most once a minute; `<Link>` prefetches
  and sign-out do not count). Activity that makes no request (reading, scrolling, moving the mouse) is
  reported by `IdleGuard` through `POST /api/session/ping` at most every 2 minutes, and an open tab locks
  itself and signs out once the hour is up. Activity is shared between tabs through `localStorage`.
- Responses to API routes are `Cache-Control: no-store`; the create-token response contains a live secret.
- Security headers on every response: CSP (no external scripts, `connect-src 'self'`, no framing), HSTS,
  `nosniff`, `Referrer-Policy: no-referrer`, `X-Frame-Options: DENY`, `noindex`. No `X-Powered-By`.
- Raw database errors are never shown on pages in production (details go to the server log).
- The demo sample data (`npm run demo`) is hard-disabled in production builds.

Things to do on the Vercel side (not code):

- Use a long random `ADMIN_PASSWORD` (16+ characters). Rotating `SESSION_SECRET` signs everyone out.
- Add a **Vercel Firewall rate-limit rule** for `/api/v1/*` and `/login`; it stops floods before they reach
  the database (the in-app limits only apply after a database lookup).
- Revoke a token the moment a PC is lost or a person leaves: `npm run token -- revoke <id>`.
- Keep `DEEPSEEK_API_KEY` only in Vercel env, with a spend cap set in the DeepSeek console as a last resort.

Known limits: a stolen session cookie stays valid until its idle/absolute limit passes (stateless
sessions: rotate `SESSION_SECRET` to kill all of them at once); the per-token
budget is checked before a call, so concurrent calls can overshoot it by a few cents; usage events are
reported by the agent, so only the proxy's `llm_calls` figures are authoritative for cost.
