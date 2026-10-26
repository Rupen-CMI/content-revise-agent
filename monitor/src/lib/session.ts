// Session + constant-time compare helpers.
// Uses only Web Crypto (works in Node for routes/pages AND in the Edge runtime
// for middleware), so middleware.ts can import this file directly.

// "__Host-" makes the browser itself refuse the cookie unless it is Secure, Path=/ and has no Domain,
// so it cannot be planted from a sibling subdomain. 7 days: sessions are stateless (HMAC), so the only
// way to revoke one early is rotating SESSION_SECRET; keep the window short.
export const SESSION_COOKIE = "__Host-admin_session";
export const SESSION_TTL_S = 7 * 24 * 3600;

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

/** `"<expiry-epoch-seconds>.<hex hmac of that payload with SESSION_SECRET>"` */
export async function createSessionToken(secret: string): Promise<string> {
  const expiry = Math.floor(Date.now() / 1000) + SESSION_TTL_S;
  return `${expiry}.${await hmacHex(String(expiry), secret)}`;
}

export async function verifySessionToken(
  token: string | undefined | null,
  secret: string | undefined,
): Promise<boolean> {
  if (!token || !secret) return false;
  const dot = token.lastIndexOf(".");
  if (dot < 1 || dot === token.length - 1) return false;
  const payload = token.slice(0, dot);
  const expiry = Number.parseInt(payload, 10);
  if (!Number.isFinite(expiry) || expiry * 1000 <= Date.now()) return false;
  const expectedSig = await hmacHex(payload, secret);
  return safeEqualStr(token.slice(dot + 1), expectedSig);
}
