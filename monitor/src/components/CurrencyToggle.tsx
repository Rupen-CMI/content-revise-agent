"use client";

import { useRouter } from "next/navigation";
import { useTransition } from "react";
import { CURRENCY_COOKIE, type Currency } from "@/lib/currency";

/** USD / INR switch. The choice is a plain preference cookie; the server reads it and renders the page in
 * that currency, so the numbers are right on first paint and there is no client-side conversion to keep
 * in sync. `rateLabel` is shown on hover so the rate being used is never hidden. */
export function CurrencyToggle({
  currency,
  inrAvailable,
  rateLabel,
}: {
  currency: Currency;
  inrAvailable: boolean;
  rateLabel: string;
}) {
  const router = useRouter();
  const [pending, startTransition] = useTransition();

  const pick = (next: Currency) => {
    if (next === currency) return;
    document.cookie = `${CURRENCY_COOKIE}=${next}; Path=/; Max-Age=31536000; SameSite=Lax; Secure`;
    startTransition(() => router.refresh());
  };

  return (
    <div className="currency-toggle" role="group" aria-label="Display currency" aria-busy={pending} title={rateLabel}>
      <button type="button" aria-pressed={currency === "USD"} onClick={() => pick("USD")}>
        $ USD
      </button>
      <button
        type="button"
        aria-pressed={currency === "INR"}
        disabled={!inrAvailable}
        onClick={() => pick("INR")}
      >
        ₹ INR
      </button>
    </div>
  );
}
