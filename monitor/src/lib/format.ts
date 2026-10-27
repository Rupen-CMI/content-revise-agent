import { fmtMoney, toDisplay, USD_MONEY, type Money } from "./currency";

/** A cost STORED in USD, shown in the visitor's currency (USD when `money` is omitted). */
export function fmtCost(usd: number | string | null | undefined, money: Money = USD_MONEY): string {
  return fmtMoney(toDisplay(usd, money), money.currency);
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

/** "step2" -> "Step 2", "finalize" -> "Finalize"; anything unknown is shown as sent. */
export function fmtStep(step: string | null | undefined): string {
  const s = (step ?? "").trim();
  const m = /^step(\d)$/.exec(s);
  if (m) return `Step ${m[1]}`;
  return s ? s.charAt(0).toUpperCase() + s.slice(1) : "";
}

/** Badge class + text for a run. A run that never finalized says how far it got, e.g. "unfinished · Step 2". */
export function fmtRunStatus(status: string | null | undefined, step: string | null | undefined): { cls: string; text: string } {
  if (status === "error") return { cls: "error", text: "error" };
  if (status === "running" || status === "unfinished") {
    const at = fmtStep(step);
    return { cls: status, text: at ? `${status} · ${at}` : status };
  }
  return { cls: "ok", text: status ?? "ok" };
}

export function fmtLocation(city: string, region: string, country: string): string {
  const parts = [city, country].filter((p) => p && p.trim() !== "");
  return parts.length ? parts.join(", ") : "—";
}
