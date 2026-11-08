import { describe, expect, it } from "vitest";
import { capShare, formatTokens, formatUsd, shortDay } from "./spend";

describe("formatUsd", () => {
  it("keeps cents, shows sub-cent spend, and never hides a nonzero amount", () => {
    expect(formatUsd(0)).toBe("$0.00");
    expect(formatUsd(1.5)).toBe("$1.50");
    expect(formatUsd(1234.567)).toBe("$1,234.57");
    expect(formatUsd(0.0042)).toBe("$0.0042");
    expect(formatUsd(0.00001)).toBe("<$0.0001");
  });
});

describe("formatTokens", () => {
  it("abbreviates thousands and millions", () => {
    expect(formatTokens(0)).toBe("0");
    expect(formatTokens(950)).toBe("950");
    expect(formatTokens(12_400)).toBe("12.4k");
    expect(formatTokens(3_250_000)).toBe("3.25M");
  });
});

describe("capShare", () => {
  it("is spend over cap, clamped to 0..1, or null without a cap", () => {
    expect(capShare(1, 4)).toBe(0.25);
    expect(capShare(9, 4)).toBe(1);
    expect(capShare(1, null)).toBeNull();
    expect(capShare(1, 0)).toBeNull();
  });
});

describe("shortDay", () => {
  it("formats an ISO day as day and month, in UTC", () => {
    expect(shortDay("2026-11-04")).toBe("4 Nov");
  });
});
