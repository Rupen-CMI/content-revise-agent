// Display currency. Everything is STORED and billed in USD (that is what DeepSeek charges and what the proxy
// records); INR is only a way of showing it, converted at read time with a cached exchange rate. This file
// is isomorphic (the chart is a client component), so it has no server-only imports.

export type Currency = "USD" | "INR";

/** Preference cookie. It is only a display choice (the value is whitelisted on read), so it is not httpOnly:
 * the toggle sets it from the browser and the server reads it to render the right currency directly. */
export const CURRENCY_COOKIE = "cmi-currency";

export function parseCurrency(value: string | undefined | null): Currency {
  return value === "INR" ? "INR" : "USD";
}

/** How to show a USD amount: in `currency`, at `rate` units of that currency per 1 USD (1 for USD). */
export interface Money {
  currency: Currency;
  rate: number;
}

export const USD_MONEY: Money = { currency: "USD", rate: 1 };

export const SYMBOL: Record<Currency, string> = { USD: "$", INR: "₹" };

/** Indian digit grouping: "1234567" -> "12,34,567". Hand-written (not Intl) so the server and the browser
 * always produce identical text, whatever ICU data each has. */
export function groupIndian(digits: string): string {
  if (digits.length <= 3) return digits;
  const head = digits.slice(0, -3).replace(/\B(?=(\d{2})+(?!\d))/g, ",");
  return `${head},${digits.slice(-3)}`;
}

/** Format `amount`, which is ALREADY in `currency`. USD keeps the dashboard's long-standing 4 decimals for
 * small values; INR needs fewer because a rupee is worth about a cent: 3 decimals below ₹1, else 2. */
export function fmtMoney(amount: number | string | null | undefined, currency: Currency = "USD"): string {
  if (amount == null || amount === "") return "—";
  const n = typeof amount === "string" ? Number(amount) : amount;
  if (!Number.isFinite(n)) return "—";
  if (currency === "USD") {
    if (n === 0) return "$0.0000";
    return n < 10 ? `$${n.toFixed(4)}` : `$${n.toFixed(2)}`;
  }
  if (n === 0) return "₹0.00";
  if (n < 1) return `₹${n.toFixed(3)}`;
  const [whole, frac] = n.toFixed(2).split(".");
  return `₹${groupIndian(whole)}.${frac}`;
}

/** Amount in USD -> the number to display/plot in `money`'s currency. */
export function toDisplay(usd: number | string | null | undefined, money: Money): number | null {
  if (usd == null || usd === "") return null;
  const n = typeof usd === "string" ? Number(usd) : usd;
  return Number.isFinite(n) ? n * money.rate : null;
}
