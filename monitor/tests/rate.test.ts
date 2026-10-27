import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { _resetRateForTests, describeRate, getUsdInrRate } from "@/lib/rate";

const ECB = { amount: 1, base: "USD", date: "2026-10-26", rates: { INR: 95.18 } };
const OPEN_ER = { result: "success", base_code: "USD", time_last_update_unix: 1793059351, rates: { INR: 95.269051 } };

const ok = (body: unknown) => new Response(JSON.stringify(body), { status: 200, headers: { "content-type": "application/json" } });

/** Route each provider URL to its own canned response. */
function routes(map: { ecb?: () => Response | Promise<Response>; open?: () => Response | Promise<Response> }) {
  return vi.spyOn(globalThis, "fetch").mockImplementation(async (input: RequestInfo | URL) => {
    const url = String(input);
    if (url.includes("frankfurter")) return map.ecb ? map.ecb() : new Response("nope", { status: 500 });
    if (url.includes("open.er-api")) return map.open ? map.open() : new Response("nope", { status: 500 });
    throw new Error(`unexpected fetch ${url}`);
  });
}

beforeEach(() => {
  vi.restoreAllMocks();
  vi.unstubAllEnvs();
  vi.spyOn(console, "error").mockImplementation(() => {});
  delete process.env.USD_INR_RATE;
  _resetRateForTests();
});
afterEach(() => vi.unstubAllEnvs());

describe("getUsdInrRate", () => {
  it("uses the ECB rate when it answers", async () => {
    const f = routes({ ecb: () => ok(ECB) });
    expect(await getUsdInrRate()).toEqual({ rate: 95.18, asOf: "2026-10-26", source: "ecb" });
    expect(f).toHaveBeenCalledTimes(1); // the backup is not touched
  });

  it("falls back to the second provider when the first is down", async () => {
    routes({ open: () => ok(OPEN_ER) });
    expect(await getUsdInrRate()).toEqual({ rate: 95.269051, asOf: "2026-10-27", source: "open-er" });
  });

  it("falls back when the first provider times out / throws", async () => {
    routes({
      ecb: () => {
        throw new Error("The operation was aborted due to timeout");
      },
      open: () => ok(OPEN_ER),
    });
    expect((await getUsdInrRate())?.source).toBe("open-er");
  });

  it("a fixed USD_INR_RATE wins and no network call is made", async () => {
    vi.stubEnv("USD_INR_RATE", "90.5");
    const f = routes({ ecb: () => ok(ECB) });
    expect(await getUsdInrRate()).toEqual({ rate: 90.5, asOf: "fixed", source: "env" });
    expect(f).not.toHaveBeenCalled();
  });

  it("ignores a nonsensical USD_INR_RATE and uses the live rate", async () => {
    for (const bad of ["abc", "0", "-5", "9999999", ""]) {
      vi.stubEnv("USD_INR_RATE", bad);
      routes({ ecb: () => ok(ECB) });
      expect((await getUsdInrRate())?.source, bad).toBe("ecb");
    }
  });

  it("refuses an implausible rate from a provider rather than showing nonsense", async () => {
    routes({ ecb: () => ok({ ...ECB, rates: { INR: 0.0095 } }), open: () => ok({ ...OPEN_ER, rates: { INR: 5e6 } }) });
    expect(await getUsdInrRate()).toBeNull();
  });

  it("refuses malformed provider bodies (wrong shape, wrong types, not JSON)", async () => {
    routes({
      ecb: () => ok({ rates: { INR: "95.18" }, date: "2026-10-26" }),
      open: () => new Response("<html>captive portal</html>", { status: 200 }),
    });
    expect(await getUsdInrRate()).toBeNull();
  });

  it("returns null when every provider is down, then does not hammer them for a few minutes", async () => {
    const f = routes({});
    expect(await getUsdInrRate()).toBeNull();
    expect(f).toHaveBeenCalledTimes(2); // both providers tried once
    expect(await getUsdInrRate()).toBeNull();
    expect(f).toHaveBeenCalledTimes(2); // remembered failure: no new requests

    _resetRateForTests();
    routes({ ecb: () => ok(ECB) });
    expect((await getUsdInrRate())?.rate).toBe(95.18); // and it recovers once allowed to retry
  });

  it("never logs the provider URL or response (only the provider name and a short reason)", async () => {
    const err = vi.spyOn(console, "error").mockImplementation(() => {});
    routes({});
    await getUsdInrRate();
    const logged = JSON.stringify(err.mock.calls);
    expect(logged).not.toContain("http");
    expect(logged).toContain("ecb");
  });

  it("demo mode needs no network", async () => {
    vi.stubEnv("MONITOR_DEMO", "1");
    vi.stubEnv("NODE_ENV", "development");
    const f = routes({});
    expect(await getUsdInrRate()).toEqual({ rate: 88, asOf: "demo", source: "demo" });
    expect(f).not.toHaveBeenCalled();
  });
});

describe("describeRate", () => {
  it("says what the rate is and where it came from", () => {
    expect(describeRate({ rate: 95.18, asOf: "2026-10-26", source: "ecb" })).toBe("1 USD = ₹95.18 (ECB reference rate, 2026-10-26)");
    expect(describeRate({ rate: 90, asOf: "fixed", source: "env" })).toBe("1 USD = ₹90.00 (fixed rate (USD_INR_RATE))");
  });
});
