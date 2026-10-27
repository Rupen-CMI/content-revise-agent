import { CurrencyNote } from "@/components/CurrencyNote";
import { Header } from "@/components/Header";
import { requireAdminPage } from "@/lib/auth";
import { publicError } from "@/lib/errors";
import { getMoney } from "@/lib/money";
import { budgetsEnforced } from "@/lib/proxy";
import { listTokens, type TokenListRow } from "@/lib/tokens";
import { fmtCost, fmtTime } from "@/lib/format";

export const dynamic = "force-dynamic";

export default async function TokensPage() {
  await requireAdminPage();
  const enforced = budgetsEnforced();
  const ctx = await getMoney();
  const { money } = ctx;
  let tokens: TokenListRow[] = [];
  let error: string | null = null;
  try {
    tokens = await listTokens();
  } catch (e) {
    error = publicError(e);
  }

  return (
    <main className="container">
      <Header active="tokens" />

      <div className="page-head">
        <h1>LLM tokens</h1>
        <p className="sub">
          One token per distribution, so the DeepSeek key never leaves the server. Create or revoke with{" "}
          <code>npm run token -- create &quot;Name&quot; 5</code> and{" "}
          <code>npm run token -- revoke &lt;id&gt;</code>.
        </p>
        <CurrencyNote ctx={ctx} />
      </div>

      {error ? (
        <div className="setup-error">
          <strong>Could not load tokens.</strong>
          <p>{error}</p>
        </div>
      ) : (
        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <th>User</th>
                <th>Status</th>
                <th className="num" title={enforced ? undefined : "Stored but not enforced: calls are never blocked, spend is only tracked"}>
                  {enforced ? "Daily budget" : "Daily budget (off)"}
                </th>
                <th className="num">Today</th>
                <th className="num">30 days</th>
                <th className="num">All time</th>
                <th className="num">Calls</th>
                <th>Last used</th>
                <th>Id</th>
              </tr>
            </thead>
            <tbody>
              {tokens.map((t) => {
                // "budget reached" only means something while budgets block calls (they don't by default)
                const over = enforced && !t.revoked_at && Number(t.spend_today) >= t.daily_budget_usd;
                return (
                  <tr key={t.id}>
                    <td>{t.label}</td>
                    <td>
                      <span className={`badge ${t.revoked_at || over ? "error" : "ok"}`}>
                        {t.revoked_at ? "revoked" : over ? "budget reached" : "active"}
                      </span>
                    </td>
                    <td className="num dim">{fmtCost(t.daily_budget_usd, money)}</td>
                    <td className="num">{fmtCost(t.spend_today, money)}</td>
                    <td className="num">{fmtCost(t.spend_30d, money)}</td>
                    <td className="num">{fmtCost(t.spend_total, money)}</td>
                    <td className="num dim">{t.calls}</td>
                    <td className="dim">{fmtTime(t.last_used_at)}</td>
                    <td className="mono" title={t.id}>{t.id.slice(0, 8)}</td>
                  </tr>
                );
              })}
              {tokens.length === 0 && (
                <tr>
                  <td colSpan={9} className="muted">No tokens yet. Create one with the CLI.</td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
      )}
    </main>
  );
}
