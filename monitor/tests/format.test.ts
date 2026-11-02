import { describe, expect, it } from "vitest";
import { fmtRunStatus, fmtStep, fmtTime } from "@/lib/format";

describe("fmtStep", () => {
  it("labels the pipeline steps", () => {
    expect(fmtStep("step1")).toBe("Step 1");
    expect(fmtStep("step4")).toBe("Step 4");
    expect(fmtStep("finalize")).toBe("Finalize");
    expect(fmtStep("")).toBe("");
    expect(fmtStep(null)).toBe("");
  });
});

describe("fmtRunStatus", () => {
  it("finished runs keep the plain ok / error badges", () => {
    expect(fmtRunStatus("ok", "finalize")).toEqual({ cls: "ok", text: "ok" });
    expect(fmtRunStatus("error", "finalize")).toEqual({ cls: "error", text: "error" });
  });

  it("a run that never finalized says how far it got", () => {
    expect(fmtRunStatus("unfinished", "step2")).toEqual({ cls: "unfinished", text: "unfinished · Step 2" });
    expect(fmtRunStatus("running", "step3")).toEqual({ cls: "running", text: "running · Step 3" });
  });

  it("copes with a missing step", () => {
    expect(fmtRunStatus("unfinished", "")).toEqual({ cls: "unfinished", text: "unfinished" });
  });
});

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
