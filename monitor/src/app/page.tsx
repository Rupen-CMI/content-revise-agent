import Link from "next/link";
import { CostChart } from "@/components/CostChart";
import { CurrencyNote } from "@/components/CurrencyNote";
import { Header } from "@/components/Header";
import { requireAdminPage } from "@/lib/auth";
import { fillDays, scaleCosts } from "@/lib/chart";
import { getMoney } from "@/lib/money";
import { publicError } from "@/lib/errors";
import {
  dailyCost,
  dashboardStats,
  listInstalls,
  recentRuns,
  type DailyCost,
  type InstallRow,
  type RunRow,
  type Stats,
} from "@/lib/events";
import {
  fmtCost,
  fmtDuration,
  fmtLocation,
  fmtRunStatus,
  fmtTime,
  fmtTokens,
} from "@/lib/format";

export const dynamic = "force-dynamic";

function Stat({ label, value }: { label: string; value: string }) {
  return (
    <div className="stat">
      <div className="label">{label}</div>
      <div className="value">{value}</div>
    </div>
  );
}

export default async function DashboardPage() {
  await requireAdminPage();
  const ctx = await getMoney();
  const { money } = ctx;
  let installs: InstallRow[] = [];
  let stats: Stats | undefined;
  let runs: RunRow[] = [];
  let costs: DailyCost[] = [];
  let error: string | null = null;
  try {
    [installs, stats, runs, costs] = await Promise.all([
      listInstalls(),
      dashboardStats(),
      recentRuns(30),
      dailyCost(30),
    ]);
  } catch (e) {
    error = publicError(e);
  }

  return (
    <main className="container">
      <Header active="usage" />

      {error ? (
        <div className="setup-error">
          <strong>Dashboard is not set up yet.</strong>
          <p>{error}</p>
          <p className="muted">
            Set DATABASE_URL (a Neon Postgres connection string) and the other env
            vars in the Vercel project, then redeploy. See monitor/README.md.
          </p>
        </div>
      ) : (
        <>
          <div className="page-head">
            <h1>Usage</h1>
            <p className="sub">Who is using the agent, what they regenerated, and what it cost.</p>
            <CurrencyNote ctx={ctx} />
          </div>

          <div className="stats">
            <Stat label="Active users · 7d" value={String(stats?.active_users ?? 0)} />
            <Stat label="Total runs" value={String(stats?.total_runs ?? 0)} />
            <Stat label="Total cost" value={fmtCost(stats?.total_cost, money)} />
            <Stat label="Cost · last 7d" value={fmtCost(stats?.cost_7d, money)} />
          </div>

          <section className="section">
            <h2>Users</h2>
            <div className="table-wrap">
              <table>
                <thead>
                  <tr>
                    <th>User</th>
                    <th>Current name</th>
                    <th>Previous name</th>
                    <th>Last seen</th>
                    <th>Location</th>
                    <th className="num">Runs</th>
                    <th className="num">Cost</th>
                    <th className="num">Tokens</th>
                  </tr>
                </thead>
                <tbody>
                  {installs.map((row) => (
                    <tr key={row.install_id}>
                      <td>
                        <Link href={`/user/${encodeURIComponent(row.install_id)}`}>
                          {row.label || "(unlabeled)"}
                        </Link>{" "}
                        <span className="mono" title={row.install_id}>
                          {row.install_id.slice(0, 6)}
                        </span>
                      </td>
                      <td>{row.current_name || <span className="muted">not set yet</span>}</td>
                      <td className="dim">{row.previous_name || "—"}</td>
                      <td className="dim">{fmtTime(row.last_seen)}</td>
                      <td className="dim">{row.last_location || "—"}</td>
                      <td className="num">{row.runs}</td>
                      <td className="num">{fmtCost(row.total_cost, money)}</td>
                      <td className="num dim">{fmtTokens(row.total_tokens)}</td>
                    </tr>
                  ))}
                  {installs.length === 0 && (
                    <tr>
                      <td colSpan={8} className="muted">
                        No events yet. The agent reports here once it is built with MONITOR_URL set.
                      </td>
                    </tr>
                  )}
                </tbody>
              </table>
            </div>
          </section>

          <section className="section">
            <h2>Recent documents</h2>
            <div className="table-wrap">
              <table>
                <thead>
                  <tr>
                    <th>Title</th>
                    <th>User</th>
                    <th>Status</th>
                    <th className="num">Cost</th>
                    <th className="num">Time</th>
                    <th>Location</th>
                    <th>When</th>
                  </tr>
                </thead>
                <tbody>
                  {runs.map((r) => (
                    <tr key={`${r.install_id}:${r.run_key}`}>
                      <td className="wrap">
                        <Link href={`/user/${encodeURIComponent(r.install_id)}`}>
                          {r.title || "(untitled)"}
                        </Link>
                      </td>
                      <td>{r.user_label || "(unlabeled)"}</td>
                      <td>
                        <span className={`badge ${fmtRunStatus(r.status, r.step).cls}`}>
                          {fmtRunStatus(r.status, r.step).text}
                        </span>
                      </td>
                      <td className="num">{fmtCost(r.cost_usd, money)}</td>
                      <td className="num dim">{fmtDuration(r.duration_s)}</td>
                      <td className="dim">{fmtLocation(r.city, r.region, r.country)}</td>
                      <td className="dim">{fmtTime(r.ts)}</td>
                    </tr>
                  ))}
                  {runs.length === 0 && (
                    <tr>
                      <td colSpan={7} className="muted">Nothing yet.</td>
                    </tr>
                  )}
                </tbody>
              </table>
            </div>
          </section>

          <section className="section">
            <h2>Daily cost · last 30 days (UTC)</h2>
            <CostChart data={scaleCosts(fillDays(costs, 30), money.rate)} currency={money.currency} />
          </section>
        </>
      )}
    </main>
  );
}
