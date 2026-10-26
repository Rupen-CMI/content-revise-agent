// Admin CLI for the LLM proxy's per-install tokens (talks straight to Neon; no dashboard login needed).
//   npm run token -- create "Priya" 5     issue a token with a $5/day budget (printed ONCE)
//   npm run token -- list                 tokens + spend
//   npm run token -- revoke <id>          cut a token off immediately
//   npm run token -- init                 just create the tables (events, install_tokens, llm_calls)
// Needs DATABASE_URL: the npm script loads monitor/.env and .env.local (e.g. from `vercel env pull`).
import { createHash, randomBytes } from "node:crypto";
import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { Pool } from "@neondatabase/serverless";

const url = process.env.DATABASE_URL;
if (!url) {
  console.error("DATABASE_URL is not set (run `vercel env pull .env.local` or export it).");
  process.exit(1);
}
const pool = new Pool({ connectionString: url });
const [cmd, ...args] = process.argv.slice(2);

const dbDir = join(dirname(fileURLToPath(import.meta.url)), "..", "db");
const schema = ["schema.sql", "proxy.sql"].map((f) => readFileSync(join(dbDir, f), "utf8")).join("\n");
const hash = (t) => createHash("sha256").update(t).digest("hex");

try {
  await pool.query(schema);
  if (cmd === "init") {
    console.log("tables ready (events + install_tokens + llm_calls)");
  } else if (cmd === "create") {
    const label = (args[0] ?? "").trim();
    const budget = args[1] === undefined ? 5 : Number(args[1]);
    if (!label || !Number.isFinite(budget) || budget <= 0) {
      console.error('usage: npm run token -- create "<label>" [daily_budget_usd]');
      process.exit(1);
    }
    const token = "cmi_" + randomBytes(32).toString("base64url");
    const { rows } = await pool.query(
      "INSERT INTO install_tokens (token_hash, label, daily_budget_usd) VALUES ($1,$2,$3) RETURNING id",
      [hash(token), label.slice(0, 120), budget],
    );
    console.log(`id:     ${rows[0].id}\nlabel:  ${label}\nbudget: $${budget}/day (UTC)\ntoken:  ${token}\n`);
    console.log("Store the token now; it cannot be shown again. Put it in the repo .env as MONITOR_PROXY_TOKEN for this build.");
  } else if (cmd === "list") {
    const { rows } = await pool.query(`
      SELECT t.id, t.label, t.daily_budget_usd, t.revoked_at IS NOT NULL AS revoked,
             COUNT(c.id)::int AS calls,
             ROUND(COALESCE(SUM(c.cost_usd),0)::numeric, 4) AS total_usd
        FROM install_tokens t LEFT JOIN llm_calls c ON c.token_id = t.id
       GROUP BY t.id ORDER BY t.created_at DESC`);
    console.table(rows);
  } else if (cmd === "revoke") {
    const id = args[0] ?? "";
    const res = await pool.query("UPDATE install_tokens SET revoked_at = now() WHERE id = $1 AND revoked_at IS NULL", [id]);
    console.log(res.rowCount ? `revoked ${id}` : "no active token with that id");
  } else {
    console.error("commands: init | create <label> [budget] | list | revoke <id>");
    process.exit(1);
  }
} finally {
  await pool.end();
}
