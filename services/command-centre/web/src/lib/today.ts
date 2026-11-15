// The Operations "Today" strip: one line that says whether today is going well, from data the page
// already loads (/api/operations, /api/spend, /api/approvals). Today is the UTC day, like the ledger.

import { isOverdue, type Approval, type OperationTask } from "./ledger";
import type { CapStatus, SpendResponse } from "./spend";

export type Today = {
  finished: number; // tasks that finished today, done or failed
  failed: number;
  spendUsd: number | null; // null until spend has loaded
  capUsd: number | null;
  capStatus: CapStatus;
  waiting: number; // approvals waiting on the owner
  overdue: number; // of those, waiting over a day
};

const FINISHED = new Set(["done", "failed"]);

function utcDay(iso: string | undefined): string | null {
  if (!iso) return null;
  const t = new Date(iso);
  return Number.isNaN(t.getTime()) ? null : t.toISOString().slice(0, 10);
}

export function todaySummary(
  tasks: OperationTask[] | undefined,
  spend: SpendResponse | null,
  approvals: Approval[] | undefined,
  now: Date,
): Today {
  const today = now.toISOString().slice(0, 10);
  const finishedToday = (tasks ?? []).filter(
    (t) => FINISHED.has(t.status) && utcDay(t.finished_ts || t.updated_ts) === today,
  );
  const waiting = approvals ?? [];
  return {
    finished: finishedToday.length,
    failed: finishedToday.filter((t) => t.status === "failed").length,
    spendUsd: spend ? spend.today.cost_usd : null,
    capUsd: spend ? spend.today.cap_usd : null,
    capStatus: spend ? spend.today.cap_status : "none",
    waiting: waiting.length,
    overdue: waiting.filter((a) => isOverdue(a, now)).length,
  };
}

/** "all clear" when nothing failed, spend is under its cap and nothing waits over a day. */
export function todayIsClear(t: Today): boolean {
  return t.failed === 0 && t.capStatus !== "over" && t.overdue === 0;
}
