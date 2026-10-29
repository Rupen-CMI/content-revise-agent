"use client";

import { useEffect, useState } from "react";

const POLL_MS = 5 * 60_000;

/** The USD -> INR rate in the header, with a pulsing green dot while the rate is fresh. The server renders
 * the first value; this then re-reads /api/rate every few minutes (and when the tab regains focus) so the
 * number never goes stale on a dashboard left open. The dot turns grey if a refresh fails. */
export function RateBadge({ rate: initial, label }: { rate: number; label: string }) {
  const [rate, setRate] = useState(initial);
  const [title, setTitle] = useState(label);
  const [live, setLive] = useState(true);

  useEffect(() => {
    let stopped = false;
    const refresh = async () => {
      try {
        const res = await fetch("/api/rate", { cache: "no-store" });
        if (!res.ok) throw new Error(String(res.status));
        const { info } = (await res.json()) as { info: { rate: number; asOf: string } | null };
        if (stopped) return;
        if (!info) throw new Error("no rate");
        setRate(info.rate);
        setTitle(`1 USD = ₹${info.rate.toFixed(2)} (as of ${info.asOf})`);
        setLive(true);
      } catch {
        if (!stopped) setLive(false);
      }
    };
    const timer = setInterval(refresh, POLL_MS);
    const onVisible = () => document.visibilityState === "visible" && void refresh();
    document.addEventListener("visibilitychange", onVisible);
    return () => {
      stopped = true;
      clearInterval(timer);
      document.removeEventListener("visibilitychange", onVisible);
    };
  }, []);

  return (
    <div className="rate-badge" title={title} data-live={live}>
      <span className="rate-dot" aria-hidden="true" />
      <span className="rate-text">
        <span className="rate-pair">USD/INR</span> ₹{rate.toFixed(2)}
      </span>
    </div>
  );
}
