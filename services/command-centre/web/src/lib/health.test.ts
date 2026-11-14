import { describe, expect, it } from "vitest";
import { checksHeadline, checksNeedAttention, type Check, type ChecksResponse } from "./health";

const check = (slug: string, status: Check["status"]): Check => ({
  name: slug, slug, status, last_ping: null, next_ping: null, grace_seconds: 60, tags: "",
});
const response = (checks: Check[]): ChecksResponse => ({
  configured: true,
  checks,
  counts: checks.reduce<ChecksResponse["counts"]>((all, c) => ({ ...all, [c.status]: (all[c.status] ?? 0) + 1 }), {}),
});

describe("checksHeadline", () => {
  it("says all up when every check is up", () => {
    expect(checksHeadline(response([check("a", "up"), check("b", "up")]))).toBe("All 2 up");
  });
  it("lists problems worst first", () => {
    expect(checksHeadline(response([check("a", "up"), check("b", "grace"), check("c", "down")]))).toBe("1 down · 1 late · 1 up");
  });
  it("handles no checks", () => {
    expect(checksHeadline(response([]))).toBe("No checks yet");
  });
});

describe("checksNeedAttention", () => {
  it("flags down or late checks only", () => {
    expect(checksNeedAttention(response([check("a", "down")]))).toBe(true);
    expect(checksNeedAttention(response([check("a", "grace")]))).toBe(true);
    expect(checksNeedAttention(response([check("a", "up"), check("b", "paused")]))).toBe(false);
    expect(checksNeedAttention(null)).toBe(false);
  });
});
