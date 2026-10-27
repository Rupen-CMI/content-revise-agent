"use client";

import { useActionState, useEffect, useState } from "react";
import { BrandMark } from "@/components/BrandMark";
import { ThemeToggle } from "@/components/ThemeToggle";
import { loginAction, LoginState } from "./actions";

export default function LoginPage() {
  const [state, formAction, pending] = useActionState<LoginState, FormData>(
    loginAction,
    {},
  );
  // set by middleware / IdleGuard when the session ended on its own; read after mount so the
  // server-rendered and first client render match
  const [expired, setExpired] = useState(false);
  useEffect(() => {
    setExpired(new URLSearchParams(window.location.search).get("reason") === "expired");
  }, []);

  return (
    <main className="login">
      <div className="login-theme">
        <ThemeToggle />
      </div>
      <div className="login-card">
        <div className="brand">
          <BrandMark />
          <span>CMI Blog Agent</span>
        </div>
        <h1>Usage dashboard</h1>
        <p className="muted">Owner access. Enter the admin password.</p>
        {expired ? (
          <p className="muted" role="status">
            You were signed out after an hour of inactivity. Sign in again to continue.
          </p>
        ) : null}
        <form action={formAction}>
          <input
            type="password"
            name="password"
            placeholder="Password"
            autoFocus
            autoComplete="current-password"
          />
          <button type="submit" disabled={pending}>
            {pending ? "Signing in…" : "Sign in"}
          </button>
          {state.error ? <p className="error-text">{state.error}</p> : null}
        </form>
      </div>
    </main>
  );
}
