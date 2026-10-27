import { describe, expect, it } from "vitest";
import {
  createSessionToken,
  renewSessionToken,
  safeEqualStr,
  secretsMatch,
  SESSION_IDLE_S,
  SESSION_MAX_S,
  SESSION_RENEW_AFTER_S,
  verifySessionToken,
} from "@/lib/session";

describe("safeEqualStr", () => {
  it("matches equal strings and rejects different ones", () => {
    expect(safeEqualStr("abc", "abc")).toBe(true);
    expect(safeEqualStr("abc", "abd")).toBe(false);
    expect(safeEqualStr("abc", "abcd")).toBe(false);
  });

  it("never matches an empty input (unset secret cannot 'match')", () => {
    expect(safeEqualStr("", "")).toBe(false);
    expect(safeEqualStr("x", "")).toBe(false);
    expect(safeEqualStr("", "x")).toBe(false);
  });
});

describe("secretsMatch", () => {
  it("compares digest digests, independent of length leakage", async () => {
    await expect(secretsMatch("correct-key", "correct-key")).resolves.toBe(true);
    await expect(secretsMatch("wrong-key", "correct-key")).resolves.toBe(false);
    await expect(secretsMatch("correct-key-but-longer", "correct-key")).resolves.toBe(false);
  });

  it("fails closed on empty secrets", async () => {
    await expect(secretsMatch("", "")).resolves.toBe(false);
    await expect(secretsMatch("x", "")).resolves.toBe(false);
  });
});

describe("session token", () => {
  const SECRET = "test-session-secret";

  it("round-trips: create then verify", async () => {
    const token = await createSessionToken(SECRET);
    await expect(verifySessionToken(token, SECRET)).resolves.toBe(true);
  });

  it("rejects a wrong secret", async () => {
    const token = await createSessionToken(SECRET);
    await expect(verifySessionToken(token, "other-secret")).resolves.toBe(false);
  });

  it("rejects a tampered payload (cannot push the activity time forward)", async () => {
    const now = 1_800_000_000;
    const token = await createSessionToken(SECRET, now);
    const [issued, last, sig] = token.split(".");
    const forged = `${issued}.${Number(last) + 100}.${sig}`;
    await expect(verifySessionToken(forged, SECRET, now + 200)).resolves.toBe(false);
  });

  it("rejects the old two-part token format and non-hex signatures", async () => {
    await expect(verifySessionToken(`${Math.floor(Date.now() / 1000) + 600}.${"a".repeat(64)}`, SECRET)).resolves.toBe(false);
    await expect(verifySessionToken("1800000000.1800000000.deadbeef", SECRET, 1_800_000_000)).resolves.toBe(false);
  });
});

describe("session idle + absolute timeouts", () => {
  const SECRET = "test-session-secret";
  const T0 = 1_800_000_000;

  it("is valid just under an hour of inactivity and dead at exactly an hour", async () => {
    const token = await createSessionToken(SECRET, T0);
    await expect(verifySessionToken(token, SECRET, T0 + SESSION_IDLE_S - 1)).resolves.toBe(true);
    await expect(verifySessionToken(token, SECRET, T0 + SESSION_IDLE_S)).resolves.toBe(false);
    // the very scenario reported: signed in yesterday, back today
    await expect(verifySessionToken(token, SECRET, T0 + 24 * 3600)).resolves.toBe(false);
  });

  it("does not renew a token that was refreshed less than a minute ago", async () => {
    const token = await createSessionToken(SECRET, T0);
    await expect(renewSessionToken(token, SECRET, T0 + SESSION_RENEW_AFTER_S - 1)).resolves.toBeNull();
  });

  it("renewal restarts the idle clock but keeps the original sign-in time", async () => {
    const token = await createSessionToken(SECRET, T0);
    const r1 = await renewSessionToken(token, SECRET, T0 + 50 * 60);
    expect(r1).not.toBeNull();
    expect(r1!.value.split(".")[0]).toBe(String(T0));
    expect(r1!.maxAge).toBe(SESSION_IDLE_S);
    // the original would have died at T0+3600; the renewed one lives until 50 min + 1 h
    await expect(verifySessionToken(token, SECRET, T0 + 70 * 60)).resolves.toBe(false);
    await expect(verifySessionToken(r1!.value, SECRET, T0 + 70 * 60)).resolves.toBe(true);
  });

  it("an expired or forged token is never renewed", async () => {
    const token = await createSessionToken(SECRET, T0);
    await expect(renewSessionToken(token, SECRET, T0 + SESSION_IDLE_S + 5)).resolves.toBeNull();
    await expect(renewSessionToken(token, "other-secret", T0 + 120)).resolves.toBeNull();
    await expect(renewSessionToken(undefined, SECRET, T0 + 120)).resolves.toBeNull();
  });

  it("keeps renewing while active, but the absolute limit still ends the session", async () => {
    let token = await createSessionToken(SECRET, T0);
    // one request every 30 minutes for 11.5 hours stays signed in...
    for (let t = T0 + 1800; t <= T0 + SESSION_MAX_S - 1800; t += 1800) {
      const r = await renewSessionToken(token, SECRET, t);
      expect(r, `renewal at +${(t - T0) / 60} min`).not.toBeNull();
      token = r!.value;
    }
    // ...the cookie's lifetime is clamped so it cannot outlast the absolute limit...
    const last = await renewSessionToken(token, SECRET, T0 + SESSION_MAX_S - 600);
    expect(last!.maxAge).toBe(600);
    // ...and at 12 h it is refused however recently it was used
    await expect(verifySessionToken(last!.value, SECRET, T0 + SESSION_MAX_S)).resolves.toBe(false);
    await expect(renewSessionToken(last!.value, SECRET, T0 + SESSION_MAX_S)).resolves.toBeNull();
  });

  it("rejects tokens from the future (clock tampering) but tolerates a minute of skew", async () => {
    const token = await createSessionToken(SECRET, T0 + 30);
    await expect(verifySessionToken(token, SECRET, T0)).resolves.toBe(true);
    const far = await createSessionToken(SECRET, T0 + 3600);
    await expect(verifySessionToken(far, SECRET, T0)).resolves.toBe(false);
  });
});

describe("session token (garbage input)", () => {
  const SECRET = "test-session-secret";

  it("rejects garbage tokens and missing secret", async () => {
    await expect(verifySessionToken(undefined, SECRET)).resolves.toBe(false);
    await expect(verifySessionToken("", SECRET)).resolves.toBe(false);
    await expect(verifySessionToken("no-dot", SECRET)).resolves.toBe(false);
    const token = await createSessionToken(SECRET);
    await expect(verifySessionToken(token, "")).resolves.toBe(false);
    await expect(verifySessionToken(token, undefined)).resolves.toBe(false);
  });
});
