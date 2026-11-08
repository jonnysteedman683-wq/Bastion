import { describe, expect, it } from "vitest";
import { cursorOf, isOverdue, isSafeGithubUrl, mergeEvents, timeAgo, waitingSince, type Approval, type LedgerEvent } from "./ledger";

const ev = (id: number): LedgerEvent => ({ id, ts: "2026-11-04T00:00:00Z", agent: "a", kind: "k", summary: "s" });

describe("mergeEvents", () => {
  it("keeps newest first and drops duplicates", () => {
    expect(mergeEvents([ev(2), ev(1)], [ev(3), ev(2)]).map((e) => e.id)).toEqual([3, 2, 1]);
  });
  it("caps the feed", () => {
    expect(mergeEvents([], [ev(1), ev(2), ev(3)], 2).map((e) => e.id)).toEqual([3, 2]);
  });
});

describe("cursorOf", () => {
  it("is 0 for an empty feed, else the highest id", () => {
    expect(cursorOf([])).toBe(0);
    expect(cursorOf([ev(4), ev(9), ev(2)])).toBe(9);
  });
});

describe("timeAgo", () => {
  const now = new Date("2026-11-04T12:00:00Z");
  it("formats each range", () => {
    expect(timeAgo(null, now)).toBe("never");
    expect(timeAgo("2026-11-04T11:59:50Z", now)).toBe("10s ago");
    expect(timeAgo("2026-11-04T11:55:00Z", now)).toBe("5m ago");
    expect(timeAgo("2026-11-04T09:00:00Z", now)).toBe("3h ago");
    expect(timeAgo("2026-11-02T12:00:00Z", now)).toBe("2d ago");
  });
});

describe("isSafeGithubUrl", () => {
  it("only allows HTTPS GitHub links", () => {
    expect(isSafeGithubUrl("https://github.com/org/repo/pull/1")).toBe(true);
    expect(isSafeGithubUrl("http://github.com/org/repo")).toBe(false);
    expect(isSafeGithubUrl("https://example.com")).toBe(false);
  });
});

const approval = (requested_ts: string, updated_ts = "2026-11-04T11:00:00Z"): Approval => ({
  task_id: "t-1", title: "Deploy", requested_by: "Lestinxor", agent: "Sebastian", mode: "", run_url: "",
  requested_ts, updated_ts, detail: "", approve_command: "linx approve t-1", reject_command: null,
});

describe("approvals", () => {
  const now = new Date("2026-11-04T12:00:00Z");
  it("waits since the request, else since the last update", () => {
    expect(waitingSince(approval("2026-11-04T09:00:00Z"))).toBe("2026-11-04T09:00:00Z");
    expect(waitingSince(approval(""))).toBe("2026-11-04T11:00:00Z");
    expect(waitingSince(approval("", ""))).toBeNull();
  });
  it("is overdue after 24 hours waiting", () => {
    expect(isOverdue(approval("2026-11-03T12:00:01Z"), now)).toBe(false);
    expect(isOverdue(approval("2026-11-03T11:59:59Z"), now)).toBe(true);
    expect(isOverdue(approval("", ""), now)).toBe(false);
  });
});
