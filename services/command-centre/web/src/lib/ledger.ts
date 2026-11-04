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
