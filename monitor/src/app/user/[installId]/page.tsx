import Link from "next/link";
import { Header } from "@/components/Header";
import { requireAdminPage } from "@/lib/auth";
import { publicError } from "@/lib/errors";
import {
  userLabel,
  userLocations,
  userNames,
  userRuns,
  type InstallNames,
  type LocationRow,
  type RunRow,
} from "@/lib/events";
import { fmtCost, fmtDuration, fmtLocation, fmtTime, fmtTokens } from "@/lib/format";

export const dynamic = "force-dynamic";

export default async function UserPage({
  params,
}: {
  params: Promise<{ installId: string }>;
}) {
  await requireAdminPage();
  const { installId } = await params;

  let runs: RunRow[] = [];
  let locations: LocationRow[] = [];
  let label = "";
  let names: InstallNames = { current: "", previous: "" };
  let totalCost = 0;
  let totalTokens = 0;
  let error: string | null = null;
  try {
    [runs, locations, label, names] = await Promise.all([
      userRuns(installId),
      userLocations(installId),
      userLabel(installId),
      userNames(installId),
    ]);
    totalCost = runs.reduce((sum, r) => sum + (r.cost_usd ?? 0), 0);
    totalTokens = runs.reduce(
      (sum, r) => sum + (r.prompt_tokens ?? 0) + (r.completion_tokens ?? 0),
      0,
    );
  } catch (e) {
    error = publicError(e);
  }

  return (
    <main className="container">
      <Header active="usage" />

      {error ? (
        <div className="setup-error">
          <strong>Could not load this user.</strong>
          <p>{error}</p>
        </div>
      ) : (
        <>
          <div className="page-head">
            <Link href="/" className="back">← All users</Link>
            <h1>
              {names.current || label || "(unlabeled)"}{" "}
              <span className="mono">{installId.slice(0, 8)}</span>
            </h1>
            <p className="sub">
              {names.previous ? <>Previously “{names.previous}” · </> : null}
              {label && names.current && label !== names.current ? <>Issued to {label} · </> : null}
              {runs.length} run{runs.length === 1 ? "" : "s"} · {fmtCost(totalCost)} total ·{" "}
              {fmtTokens(totalTokens)} tokens
            </p>
          </div>

          <section className="section">
            <h2>Run history</h2>
            <div className="table-wrap">
              <table>
                <thead>
                  <tr>
                    <th>Title</th>
                    <th>Status</th>
                    <th className="num">Words</th>
                    <th className="num">Cost</th>
                    <th className="num">Time</th>
                    <th>Location</th>
                    <th>When</th>
                  </tr>
                </thead>
                <tbody>
                  {runs.map((r) => (
                    <tr key={r.event_id}>
                      <td className="wrap">{r.title || "(untitled)"}</td>
                      <td>
                        <span className={`badge ${r.status === "error" ? "error" : "ok"}`}>
                          {r.status ?? "?"}
                        </span>
                      </td>
                      <td className="num dim">{r.word_count ?? "—"}</td>
                      <td className="num">{fmtCost(r.cost_usd)}</td>
                      <td className="num dim">{fmtDuration(r.duration_s)}</td>
                      <td className="dim">{fmtLocation(r.city, r.region, r.country)}</td>
                      <td className="dim">{fmtTime(r.ts)}</td>
                    </tr>
                  ))}
                  {runs.length === 0 && (
                    <tr>
                      <td colSpan={7} className="muted">No completed runs for this install.</td>
                    </tr>
                  )}
                </tbody>
              </table>
            </div>
          </section>

          <section className="section">
            <h2>Locations</h2>
            <div className="table-wrap">
              <table>
                <thead>
                  <tr>
                    <th>Location</th>
                    <th className="num">Events</th>
                    <th>Last seen</th>
                  </tr>
                </thead>
                <tbody>
                  {locations.map((l) => (
                    <tr key={`${l.city}-${l.region}-${l.country}`}>
                      <td>{fmtLocation(l.city, l.region, l.country)}</td>
                      <td className="num">{l.visits}</td>
                      <td className="dim">{fmtTime(l.last_seen)}</td>
                    </tr>
                  ))}
                  {locations.length === 0 && (
                    <tr>
                      <td colSpan={3} className="muted">
                        No location data. Events only get a city when they arrive through Vercel.
                      </td>
                    </tr>
                  )}
                </tbody>
              </table>
            </div>
          </section>
        </>
      )}
    </main>
  );
}
