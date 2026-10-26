import { describe, expect, it } from "vitest";
import { axisCost, fillDays, shortDay } from "@/lib/chart";

const NOW = new Date("2026-10-26T15:30:00Z");

describe("fillDays", () => {
  it("returns exactly N consecutive UTC days ending today, zero-filled", () => {
    const out = fillDays([{ day: "2026-10-24", cost: 0.5, runs: 2 }], 7, NOW);
    expect(out.map((d) => d.day)).toEqual([
      "2026-10-20", "2026-10-21", "2026-10-22", "2026-10-23", "2026-10-24", "2026-10-25", "2026-10-26",
    ]);
    expect(out[4]).toEqual({ day: "2026-10-24", cost: 0.5, runs: 2 });
    expect(out.filter((d) => d.cost === 0 && d.runs === 0)).toHaveLength(6);
  });

  it("crosses month and year boundaries and ignores days outside the window", () => {
    const out = fillDays([{ day: "2025-01-01", cost: 9, runs: 9 }], 3, new Date("2026-01-01T00:10:00Z"));
    expect(out.map((d) => d.day)).toEqual(["2025-12-30", "2025-12-31", "2026-01-01"]);
    expect(out.every((d) => d.cost === 0)).toBe(true);
  });

  it("coerces numeric strings (Postgres returns SUM as text for some drivers)", () => {
    const out = fillDays([{ day: "2026-10-26", cost: "0.25" as unknown as number, runs: "3" as unknown as number }], 1, NOW);
    expect(out[0]).toEqual({ day: "2026-10-26", cost: 0.25, runs: 3 });
  });
});

describe("formatters", () => {
  it("formats days locale-independently", () => {
    expect(shortDay("2026-10-03")).toBe("Oct 3");
    expect(shortDay("2026-01-31")).toBe("Jan 31");
  });
  it("formats axis costs compactly", () => {
    expect(axisCost(0)).toBe("$0");
    expect(axisCost(0.0532)).toBe("$0.05");
    expect(axisCost(1.234)).toBe("$1.2");
    expect(axisCost(12.4)).toBe("$12");
  });
});
