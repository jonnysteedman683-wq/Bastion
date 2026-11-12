// Shapes returned by the command centre's own API (app.py). The browser never talks to Supabase.

export type LedgerEvent = {
  id: number;
  ts: string; // ISO 8601, UTC
  agent: string;
  kind: string; // e.g. task.request, task.result, deploy.record, memory.write
  summary: string;
};

export type FeedResponse = { source: "demo" | "ledger"; events: LedgerEvent[] };

export type Agent = {
  name: string;
  role: string;
  runs_on: string;
  last_seen: string | null;
  status: "active" | "idle" | "silent";
};

export type RosterResponse = { source: "demo" | "ledger"; agents: Agent[] };

export type OperationTask = {
  task_id: string;
  title: string;
  status: string;
  agent: string;
  updated_ts: string;
  detail?: string;
  run_url?: string;
  trace_url?: string;
};
export type AttentionItem = { kind: string; title: string; detail: string };
export type OperationsResponse = {
  source: "demo" | "ledger";
  generated_at: string;
  attention: AttentionItem[];
  tasks: OperationTask[];
  pr_report: { scanned_ts: string; summary: string; items: { pr: string; url: string; bucket: string; flags: string[]; note: string }[] } | null;
  completions: LedgerEvent[];
  control: Control;
};

/** LinX's kill switch, as read from the owner's last linx/control/halt memory write. */
export type Control = { halted: boolean; reason: string | null; since: string | null };

/** The one LinX command that flips the kill switch from its current state. Run on the owner's PC. */
export function killSwitchCommand(control: Control): { action: string; command: string } {
  return control.halted
    ? { action: "Resume the fleet", command: 'linx resume --reason "<why>"' }
    : { action: "Halt the fleet", command: 'linx halt --reason "<why>"' };
}

/** A task waiting for the owner. The commands are null when the task id isn't safe to paste into a shell. */
export type Approval = {
  task_id: string;
  title: string;
  requested_by: string;
  agent: string;
  mode: string;
  run_url: string;
  requested_ts: string;
  updated_ts: string;
  detail: string;
  approve_command: string | null;
  reject_command: string | null;
};
export type ApprovalsResponse = { source: "demo" | "ledger"; approvals: Approval[] };

const OVERDUE_MS = 24 * 60 * 60 * 1000;

/** When the owner was first asked: the request time, else the last update. */
export function waitingSince(item: Approval): string | null {
  return item.requested_ts || item.updated_ts || null;
}

/** Waiting on the owner for more than a day. */
export function isOverdue(item: Approval, now: Date = new Date()): boolean {
  const since = waitingSince(item);
  return since !== null && now.getTime() - new Date(since).getTime() > OVERDUE_MS;
}

export function isSafeGithubUrl(value: string | null): boolean {
  try {
    const url = new URL(value ?? "");
    return url.protocol === "https:" && url.hostname === "github.com";
  } catch {
    return false;
  }
}

const TRACE_URL = /^https:\/\/[a-z0-9]([a-z0-9.-]{0,251}[a-z0-9])?\/project\/[A-Za-z0-9_-]{1,100}\/traces\/[A-Za-z0-9_.:-]{1,200}$/;

/** An https Langfuse trace page: no userinfo, port, query or fragment. The API has already checked the host. */
export function isSafeTraceUrl(value: string | null | undefined): boolean {
  return typeof value === "string" && value.length <= 500 && TRACE_URL.test(value);
}

/** Merge newly fetched events into the feed: newest first, no duplicates, at most `cap` kept. */
export function mergeEvents(current: LedgerEvent[], incoming: LedgerEvent[], cap = 200): LedgerEvent[] {
  const byId = new Map<number, LedgerEvent>();
  for (const e of current) byId.set(e.id, e);
  for (const e of incoming) byId.set(e.id, e);
  return [...byId.values()].sort((a, b) => b.id - a.id).slice(0, cap);
}

/** Highest event id seen, so the next poll asks only for newer events. */
export function cursorOf(events: LedgerEvent[]): number {
  return events.reduce((max, e) => (e.id > max ? e.id : max), 0);
}

/** "12s ago", "5m ago", "3h ago", "2d ago". */
export function timeAgo(iso: string | null, now: Date = new Date()): string {
  if (!iso) return "never";
  const s = Math.max(0, Math.round((now.getTime() - new Date(iso).getTime()) / 1000));
  if (s < 60) return `${s}s ago`;
  if (s < 3600) return `${Math.floor(s / 60)}m ago`;
  if (s < 86400) return `${Math.floor(s / 3600)}h ago`;
  return `${Math.floor(s / 86400)}d ago`;
}

export async function getJSON<T>(path: string, signal?: AbortSignal): Promise<T> {
  const res = await fetch(path, { signal, headers: { Accept: "application/json" } });
  if (!res.ok) throw new Error(`${path} returned ${res.status}`);
  return (await res.json()) as T;
}
