export function fmtCost(usd: number | string | null | undefined): string {
  if (usd == null || usd === "") return "—";
  const n = typeof usd === "string" ? Number(usd) : usd;
  if (!Number.isFinite(n)) return "—";
  if (n === 0) return "$0.0000";
  // 4 decimals for the small per-run / per-user values these costs actually are
  return n < 10 ? `$${n.toFixed(4)}` : `$${n.toFixed(2)}`;
}

export function fmtTokens(n: number | string | null | undefined): string {
  const v = typeof n === "string" ? Number(n) : n ?? 0;
  if (!Number.isFinite(v)) return "—";
  if (v >= 1e9) return `${(v / 1e9).toFixed(1)}B`;
  if (v >= 1e6) return `${(v / 1e6).toFixed(1)}M`;
  if (v >= 1e3) return `${(v / 1e3).toFixed(1)}k`;
  return String(v);
}

export function fmtDuration(s: number | null | undefined): string {
  if (s == null || !Number.isFinite(s)) return "—";
  if (s < 60) return `${Math.round(s)}s`;
  if (s < 3600) return `${Math.floor(s / 60)}m ${Math.round(s % 60)}s`;
  return `${Math.floor(s / 3600)}h ${Math.round((s % 3600) / 60)}m`;
}

export function fmtTime(iso: string | null | undefined): string {
  if (!iso) return "—";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "—";
  return `${d.toISOString().slice(0, 16).replace("T", " ")} UTC`;
}

export function fmtLocation(city: string, region: string, country: string): string {
  const parts = [city, country].filter((p) => p && p.trim() !== "");
  return parts.length ? parts.join(", ") : "—";
}
