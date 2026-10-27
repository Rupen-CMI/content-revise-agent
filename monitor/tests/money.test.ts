import { beforeEach, describe, expect, it, vi } from "vitest";

const jar = vi.hoisted(() => ({ value: undefined as string | undefined }));
vi.mock("next/headers", () => ({
  cookies: async () => ({ get: (name: string) => (name === "cmi-currency" && jar.value !== undefined ? { name, value: jar.value } : undefined) }),
}));
const rate = vi.hoisted(() => ({ getUsdInrRate: vi.fn() }));
vi.mock("@/lib/rate", async (orig) => ({ ...(await orig<typeof import("@/lib/rate")>()), getUsdInrRate: rate.getUsdInrRate }));

import { CurrencyNote } from "@/components/CurrencyNote";
import { getMoney } from "@/lib/money";

const LIVE = { rate: 95.18, asOf: "2026-10-26", source: "ecb" as const };

beforeEach(() => {
  jar.value = undefined;
  rate.getUsdInrRate.mockReset().mockResolvedValue(LIVE);
});

describe("getMoney", () => {
  it("defaults to USD, but still reports the rate so the toggle can offer INR", async () => {
    const ctx = await getMoney();
    expect(ctx.money).toEqual({ currency: "USD", rate: 1 });
    expect(ctx.requested).toBe("USD");
    expect(ctx.info).toEqual(LIVE);
  });

  it("INR cookie + a rate: converts at that rate", async () => {
    jar.value = "INR";
    const ctx = await getMoney();
    expect(ctx.money).toEqual({ currency: "INR", rate: 95.18 });
  });

  it("INR cookie but no rate available: falls back to USD and remembers INR was requested", async () => {
    jar.value = "INR";
    rate.getUsdInrRate.mockResolvedValue(null);
    const ctx = await getMoney();
    expect(ctx.money.currency).toBe("USD");
    expect(ctx.requested).toBe("INR");
    expect(ctx.info).toBeNull();
  });

  it("a junk cookie is treated as USD", async () => {
    jar.value = "BTC";
    expect((await getMoney()).money.currency).toBe("USD");
  });
});

describe("CurrencyNote", () => {
  const text = (el: unknown) => JSON.stringify(el);

  it("says nothing in USD mode", async () => {
    expect(CurrencyNote({ ctx: await getMoney() })).toBeNull();
  });

  it("states the rate and that rupees are an estimate", async () => {
    jar.value = "INR";
    const out = text(CurrencyNote({ ctx: await getMoney() }));
    expect(out).toContain("95.18");
    expect(out).toContain("ECB reference rate");
    expect(out).toContain("estimate");
  });

  it("explains the fallback when the rate is unavailable", async () => {
    jar.value = "INR";
    rate.getUsdInrRate.mockResolvedValue(null);
    expect(text(CurrencyNote({ ctx: await getMoney() }))).toContain("unavailable");
  });
});
