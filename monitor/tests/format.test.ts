import { describe, expect, it } from "vitest";
import { fmtTime } from "@/lib/format";

describe("fmtTime", () => {
  it("shows times in India Standard Time (UTC+5:30)", () => {
    expect(fmtTime("2026-11-02T13:49:00Z")).toBe("2026-11-02 19:19 IST");
  });

  it("rolls over to the next day when the offset crosses midnight", () => {
    expect(fmtTime("2026-11-02T19:00:00Z")).toBe("2026-11-03 00:30 IST");
  });

  it("shows a dash for a missing or invalid time", () => {
    expect(fmtTime(null)).toBe("—");
    expect(fmtTime("not a date")).toBe("—");
  });
});
