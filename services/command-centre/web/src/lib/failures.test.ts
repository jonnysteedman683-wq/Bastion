import { describe, expect, it } from "vitest";
import { failuresHeadline, failuresNeedAttention, rateLabel, type FailuresResponse } from "./failures";

const report = (last24: number, last7: number, recurring: number): FailuresResponse => ({
  source: "ledger", generated_at: "2026-11-15T00:00:00Z", window_days: 7,
  totals: { last_24h: last24, last_7d: last7, recurring }, agents: [], groups: [],
});

describe("failuresNeedAttention", () => {
  it("is quiet with no data or a clean week", () => {
    expect(failuresNeedAttention(null)).toBe(false);
    expect(failuresNeedAttention(report(0, 0, 0))).toBe(false);
  });
  it("flags a failure today or a recurring fault", () => {
    expect(failuresNeedAttention(report(1, 1, 0))).toBe(true);
    expect(failuresNeedAttention(report(0, 4, 1))).toBe(true);
    expect(failuresNeedAttention(report(0, 2, 0))).toBe(false);
  });
});

describe("failuresHeadline", () => {
  it("says so when the week is clean", () => {
    expect(failuresHeadline(report(0, 0, 0))).toBe("No failures in the last 7 days.");
  });
  it("counts today, the week and recurring faults", () => {
    expect(failuresHeadline(report(2, 5, 1))).toBe("2 today · 5 this week · 1 recurring");
    expect(failuresHeadline(report(0, 1, 0))).toBe("0 today · 1 this week");
  });
});

describe("rateLabel", () => {
  it("shows a percentage, or a dash when nothing finished", () => {
    expect(rateLabel(null)).toBe("–");
    expect(rateLabel(0.5)).toBe("50%");
    expect(rateLabel(0.125)).toBe("13%");
  });
});
