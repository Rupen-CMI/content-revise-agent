import { describe, expect, it } from "vitest";
import { fmtRunStatus, fmtStep } from "@/lib/format";

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
