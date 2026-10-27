"use client";

import { useEffect } from "react";

// Browser half of the idle timeout. The server already ends a session an hour after its last request
// (middleware.ts); this covers what the server cannot see:
//   * activity that makes no request (reading, scrolling, hovering the chart) keeps the session alive
//     by pinging /api/session/ping, at most once every PING_EVERY_MS
//   * a tab left open with data on screen locks itself at the deadline instead of waiting for the next
//     click
// Last-activity is shared across tabs through localStorage, so working in one tab keeps the others open.

const ACTIVITY_KEY = "cmi-last-activity";
const PING_EVERY_MS = 2 * 60 * 1000;
const WRITE_EVERY_MS = 5 * 1000;
const CHECK_EVERY_MS = 15 * 1000;
const EVENTS = ["pointerdown", "pointermove", "keydown", "scroll", "wheel", "touchstart"] as const;

/** Shared across tabs; null when storage is unavailable (the caller then trusts its own timestamp). */
function sharedLastActivity(): number | null {
  try {
    const v = Number(localStorage.getItem(ACTIVITY_KEY));
    return v > 0 ? v : null;
  } catch {
    return null;
  }
}

export function IdleGuard({ idleSeconds }: { idleSeconds: number }) {
  useEffect(() => {
    const idleMs = idleSeconds * 1000;
    let written = 0;
    let pinged = Date.now(); // the page load itself was a request, so the server just renewed the cookie
    let locking = false;

    const remember = (now: number) => {
      written = now;
      try {
        localStorage.setItem(ACTIVITY_KEY, String(now));
      } catch {
        /* storage blocked: this tab still tracks its own activity through `written` */
      }
    };
    remember(Date.now());

    const onActivity = () => {
      const now = Date.now();
      if (now - written >= WRITE_EVERY_MS) remember(now);
      if (now - pinged >= PING_EVERY_MS) {
        pinged = now;
        void fetch("/api/session/ping", { method: "POST", credentials: "same-origin", keepalive: true }).catch(
          () => {},
        );
      }
    };

    const lock = async () => {
      if (locking) return;
      locking = true;
      try {
        await fetch("/logout", { method: "POST", credentials: "same-origin", keepalive: true });
      } catch {
        /* offline: the cookie still expires on its own */
      }
      window.location.replace("/login?reason=expired");
    };

    const check = () => {
      // a sleeping laptop or a throttled background tab lets timers drift, so compare wall-clock times
      if (Date.now() - (sharedLastActivity() ?? written) >= idleMs) void lock();
    };

    for (const e of EVENTS) window.addEventListener(e, onActivity, { passive: true });
    document.addEventListener("visibilitychange", check);
    const timer = window.setInterval(check, CHECK_EVERY_MS);
    return () => {
      for (const e of EVENTS) window.removeEventListener(e, onActivity);
      document.removeEventListener("visibilitychange", check);
      window.clearInterval(timer);
    };
  }, [idleSeconds]);

  return null;
}
