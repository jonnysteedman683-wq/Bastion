import { describe, expect, it } from "vitest";
import type { Approval, OperationTask } from "./ledger";
import type { SpendResponse } from "./spend";
import { todayIsClear, todaySummary } from "./today";

const NOW = new Date("2026-11-14T15:00:00Z");
const task = (status: string, ts: string, finished?: string): OperationTask => ({
  task_id: `${status}-${ts}`, title: "t", status, agent: "Sebastian", updated_ts: ts, finished_ts: finished,
});
const approval = (requested: string): Approval =>
  ({ task_id: requested, title: "a", requested_by: "o", agent: "q", mode: "m", run_url: "", requested_ts: requested, updated_ts: requested, detail: "", approve_command: null, reject_command: null }) as Approval;
const spend = (cost: number, cap: number | null, status: SpendResponse["today"]["cap_status"]) =>
  ({ today: { cost_usd: cost, cap_usd: cap, cap_status: status, input_tokens: 0, output_tokens: 0, runs: 0 } }) as SpendResponse;

describe("todaySummary", () => {
  it("counts only tasks that finished today (UTC)", () => {
    const tasks = [
      task("done", "2026-11-14T09:00:00Z"),
      task("failed", "2026-11-14T10:00:00Z"),
      task("done", "2026-11-13T23:59:00Z"),
      task("open", "2026-11-14T11:00:00Z"),
      task("done", "2026-11-14T12:00:00Z", "2026-11-13T22:00:00Z"),
    ];
    const t = todaySummary(tasks, null, [], NOW);
    expect(t.finished).toBe(2);
    expect(t.failed).toBe(1);
    expect(t.spendUsd).toBeNull();
  });

  it("takes spend and cap from /api/spend and overdue approvals from their age", () => {
    const t = todaySummary([], spend(1.25, 5, "ok"), [approval("2026-11-14T14:00:00Z"), approval("2026-11-12T09:00:00Z")], NOW);
    expect(t).toMatchObject({ spendUsd: 1.25, capUsd: 5, capStatus: "ok", waiting: 2, overdue: 1 });
  });
});

describe("todayIsClear", () => {
  const base = todaySummary([], spend(1, 5, "ok"), [], NOW);
  it("is clear with no failures, no overspend and nothing overdue", () => {
    expect(todayIsClear(base)).toBe(true);
    expect(todayIsClear({ ...base, failed: 1 })).toBe(false);
    expect(todayIsClear({ ...base, capStatus: "over" })).toBe(false);
    expect(todayIsClear({ ...base, overdue: 1 })).toBe(false);
  });
});
