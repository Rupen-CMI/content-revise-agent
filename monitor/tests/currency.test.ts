import { describe, expect, it } from "vitest";
import { axisCost, scaleCosts } from "@/lib/chart";
import { fmtMoney, groupIndian, parseCurrency, toDisplay, USD_MONEY } from "@/lib/currency";
import { fmtCost } from "@/lib/format";

const INR = { currency: "INR", rate: 95.18 } as const;

describe("parseCurrency", () => {
  it("only INR switches currency; anything else (missing, junk, injection) is USD", () => {
    expect(parseCurrency("INR")).toBe("INR");
    for (const v of [undefined, null, "", "USD", "inr", "EUR", "INR; Path=/", "<script>"]) {
      expect(parseCurrency(v as string | undefined), String(v)).toBe("USD");
    }
  });
});

describe("groupIndian", () => {
  it("groups the last three digits, then pairs (lakh / crore)", () => {
    expect(groupIndian("5")).toBe("5");
    expect(groupIndian("999")).toBe("999");
    expect(groupIndian("1000")).toBe("1,000");
    expect(groupIndian("12345")).toBe("12,345");
    expect(groupIndian("123456")).toBe("1,23,456");
    expect(groupIndian("1234567")).toBe("12,34,567");
    expect(groupIndian("123456789")).toBe("12,34,56,789");
  });
});

describe("fmtMoney", () => {
  it("USD keeps the dashboard's existing format exactly", () => {
    expect(fmtMoney(0, "USD")).toBe("$0.0000");
    expect(fmtMoney(0.0136, "USD")).toBe("$0.0136");
    expect(fmtMoney(9.99991, "USD")).toBe("$9.9999");
    expect(fmtMoney(12.345, "USD")).toBe("$12.35");
    expect(fmtMoney("0.5", "USD")).toBe("$0.5000");
    expect(fmtMoney(0.0136)).toBe("$0.0136"); // USD is the default
  });

  it("INR: 3 decimals below a rupee, else 2 with Indian grouping", () => {
    expect(fmtMoney(0, "INR")).toBe("₹0.00");
    expect(fmtMoney(0.0123, "INR")).toBe("₹0.012");
    expect(fmtMoney(1.2944, "INR")).toBe("₹1.29");
    expect(fmtMoney(999.5, "INR")).toBe("₹999.50");
    expect(fmtMoney(99999.5, "INR")).toBe("₹99,999.50");
    expect(fmtMoney(1234567.891, "INR")).toBe("₹12,34,567.89");
  });

  it("shows a dash for missing or non-numeric values in either currency", () => {
    for (const c of ["USD", "INR"] as const) {
      for (const v of [null, undefined, "", "abc", Number.NaN, Number.POSITIVE_INFINITY]) {
        expect(fmtMoney(v as number, c), `${c} ${String(v)}`).toBe("—");
      }
    }
  });
});

describe("fmtCost (USD in, display currency out)", () => {
  it("is unchanged without a currency, so existing call sites and numbers are untouched", () => {
    expect(fmtCost(0.0136)).toBe("$0.0136");
    expect(fmtCost("0.0136")).toBe("$0.0136");
    expect(fmtCost(null)).toBe("—");
    expect(fmtCost(0.0136, USD_MONEY)).toBe("$0.0136");
  });

  it("converts at the given rate", () => {
    expect(fmtCost(0.0136, INR)).toBe("₹1.29"); // 0.0136 USD x 95.18
    expect(fmtCost("0.0136", INR)).toBe("₹1.29"); // pg returns numerics as strings
    expect(fmtCost(10, INR)).toBe("₹951.80");
    expect(fmtCost(0, INR)).toBe("₹0.00");
    expect(fmtCost(null, INR)).toBe("—");
    expect(fmtCost("", INR)).toBe("—");
  });

  it("toDisplay refuses garbage instead of returning NaN", () => {
    expect(toDisplay("x", INR)).toBeNull();
    expect(toDisplay(2, INR)).toBeCloseTo(190.36, 6);
  });
});

describe("chart helpers", () => {
  const points = [
    { day: "2026-10-26", cost: 0, runs: 0 },
    { day: "2026-10-27", cost: 0.02, runs: 2 },
  ];

  it("scaleCosts converts the plotted values and leaves everything else alone", () => {
    const out = scaleCosts(points, 100);
    expect(out.map((p) => p.cost)).toEqual([0, 2]);
    expect(out.map((p) => p.runs)).toEqual([0, 2]);
    expect(points[1].cost).toBe(0.02); // the input is not mutated
    expect(scaleCosts(points, 1)).toBe(points);
  });

  it("axisCost uses the right symbol and keeps its compact thresholds", () => {
    expect(axisCost(0)).toBe("$0");
    expect(axisCost(0.05)).toBe("$0.05");
    expect(axisCost(1.25, "USD")).toBe("$1.3");
    expect(axisCost(0, "INR")).toBe("₹0");
    expect(axisCost(0.5, "INR")).toBe("₹0.50");
    expect(axisCost(4.567, "INR")).toBe("₹4.6");
    expect(axisCost(120.4, "INR")).toBe("₹120");
  });
});
