// Session + constant-time compare helpers.
// Uses only Web Crypto (works in Node for routes/pages AND in the Edge runtime
// for middleware), so middleware.ts can import this file directly.

// "__Host-" makes the browser itself refuse the cookie unless it is Secure, Path=/ and has no Domain,
// so it cannot be planted from a sibling subdomain.
//
// Sessions are stateless (HMAC), so the only way to revoke one early is rotating SESSION_SECRET. Two
// clocks keep the window short:
//   idle      1 h since the last real activity (every authenticated request renews it, see middleware.ts
//             and components/IdleGuard.tsx) -> a forgotten tab or a stolen-later cookie dies on its own
//   absolute  12 h since sign-in, however busy, so a continuously-renewed cookie cannot live forever
export const SESSION_COOKIE = "__Host-admin_session";
export const SESSION_IDLE_S = 3600;
export const SESSION_MAX_S = 12 * 3600;
/** Re-signing on every request would turn every page view into a Set-Cookie; once a minute is plenty. */
export const SESSION_RENEW_AFTER_S = 60;
const CLOCK_SKEW_S = 60;

export function sessionCookieOptions(maxAge: number) {
  return { httpOnly: true, sameSite: "lax" as const, path: "/", secure: true, maxAge };
}

const encoder = new TextEncoder();

/** Constant-time string comparison (no early exit on mismatch). Also returns
 * false for empty inputs so an unset secret/header can never "match". */
export function safeEqualStr(a: string, b: string): boolean {
  if (!a || !b) return false;
  const max = Math.max(a.length, b.length);
  let diff = a.length === b.length ? 0 : 1; // length difference alone must fail...
  for (let i = 0; i < max; i++) {
    // ...but the loop still runs over the full length (a.length is fixed for digests anyway)
    diff |= (a.charCodeAt(i) || 0) ^ (b.charCodeAt(i) || 0);
  }
  return diff === 0;
}

function hex(bytes: Uint8Array): string {
  let out = "";
  for (const b of bytes) out += b.toString(16).padStart(2, "0");
  return out;
}

export async function sha256Hex(input: string): Promise<string> {
  const digest = await crypto.subtle.digest("SHA-256", encoder.encode(input));
  return hex(new Uint8Array(digest));
}

/** Constant-time equality of two secrets: compare SHA-256 digests so even the
 * length of the inputs is not leaked through the comparison itself. */
export async function secretsMatch(provided: string, expected: string): Promise<boolean> {
  if (!provided || !expected) return false;
  const [a, b] = await Promise.all([sha256Hex(provided), sha256Hex(expected)]);
  return safeEqualStr(a, b);
}

// ---------------------------------------------------------------- session token

function hmacHex(payload: string, secret: string): Promise<string> {
  return crypto.subtle
    .importKey("raw", encoder.encode(secret), { name: "HMAC", hash: "SHA-256" }, false, ["sign"])
    .then((key) => crypto.subtle.sign("HMAC", key, encoder.encode(payload)))
    .then((sig) => hex(new Uint8Array(sig)));
}

const nowS = () => Math.floor(Date.now() / 1000);

/** `"<signed-in-epoch-s>.<last-activity-epoch-s>.<hex hmac of those two with SESSION_SECRET>"` */
export async function createSessionToken(secret: string, now: number = nowS()): Promise<string> {
  const payload = `${now}.${now}`;
  return `${payload}.${await hmacHex(payload, secret)}`;
}

interface SessionClaims {
  issued: number;
  last: number;
}

/** The signed claims when the token is genuine and neither clock has run out, otherwise null. */
async function readSession(
  token: string | undefined | null,
  secret: string | undefined,
  now: number,
): Promise<SessionClaims | null> {
  if (!token || !secret) return null;
  const m = /^(\d{1,12})\.(\d{1,12})\.([0-9a-f]{64})$/.exec(token);
  if (!m) return null; // also rejects the old two-part token: everyone signs in again once
  const issued = Number(m[1]);
  const last = Number(m[2]);
  if (last < issued || last > now + CLOCK_SKEW_S || issued > now + CLOCK_SKEW_S) return null;
  if (now - last >= SESSION_IDLE_S || now - issued >= SESSION_MAX_S) return null;
  const expected = await hmacHex(`${m[1]}.${m[2]}`, secret);
  return safeEqualStr(m[3], expected) ? { issued, last } : null;
}

export async function verifySessionToken(
  token: string | undefined | null,
  secret: string | undefined,
  now: number = nowS(),
): Promise<boolean> {
  return (await readSession(token, secret, now)) !== null;
}

/** A fresh cookie value (same sign-in time, activity = now) when `token` is valid and its last
 * activity is older than SESSION_RENEW_AFTER_S; null when there is nothing to renew. `maxAge` never
 * outlives the absolute limit. */
export async function renewSessionToken(
  token: string | undefined | null,
  secret: string | undefined,
  now: number = nowS(),
): Promise<{ value: string; maxAge: number } | null> {
  const claims = await readSession(token, secret, now);
  if (!claims || !secret || now - claims.last < SESSION_RENEW_AFTER_S) return null;
  const payload = `${claims.issued}.${now}`;
  return {
    value: `${payload}.${await hmacHex(payload, secret)}`,
    maxAge: Math.min(SESSION_IDLE_S, SESSION_MAX_S - (now - claims.issued)),
  };
}
