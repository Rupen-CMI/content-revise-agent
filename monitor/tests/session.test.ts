import { describe, expect, it } from "vitest";
import {
  createSessionToken,
  safeEqualStr,
  secretsMatch,
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

  it("rejects a tampered payload", async () => {
    const token = await createSessionToken(SECRET);
    const [payload, sig] = token.split(".");
    const forged = `${Number(payload) + 100}.${sig}`;
    await expect(verifySessionToken(forged, SECRET)).resolves.toBe(false);
  });

  it("rejects an expired token", async () => {
    const expired = `${Math.floor(Date.now() / 1000) - 10}.deadbeef`;
    await expect(verifySessionToken(expired, SECRET)).resolves.toBe(false);
  });

  it("rejects garbage tokens and missing secret", async () => {
    await expect(verifySessionToken(undefined, SECRET)).resolves.toBe(false);
    await expect(verifySessionToken("", SECRET)).resolves.toBe(false);
    await expect(verifySessionToken("no-dot", SECRET)).resolves.toBe(false);
    const token = await createSessionToken(SECRET);
    await expect(verifySessionToken(token, "")).resolves.toBe(false);
    await expect(verifySessionToken(token, undefined)).resolves.toBe(false);
  });
});
