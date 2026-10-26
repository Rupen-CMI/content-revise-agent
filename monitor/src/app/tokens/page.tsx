import { Header } from "@/components/Header";
import { requireAdminPage } from "@/lib/auth";
import { publicError } from "@/lib/errors";
import { listTokens, type TokenListRow } from "@/lib/tokens";
import { fmtCost, fmtTime } from "@/lib/format";

export const dynamic = "force-dynamic";

export default async function TokensPage() {
  await requireAdminPage();
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
                <th className="num">Daily budget</th>
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
                const over = !t.revoked_at && Number(t.spend_today) >= t.daily_budget_usd;
                return (
                  <tr key={t.id}>
                    <td>{t.label}</td>
                    <td>
                      <span className={`badge ${t.revoked_at || over ? "error" : "ok"}`}>
                        {t.revoked_at ? "revoked" : over ? "budget reached" : "active"}
                      </span>
                    </td>
                    <td className="num dim">{fmtCost(t.daily_budget_usd)}</td>
                    <td className="num">{fmtCost(t.spend_today)}</td>
                    <td className="num">{fmtCost(t.spend_30d)}</td>
                    <td className="num">{fmtCost(t.spend_total)}</td>
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
