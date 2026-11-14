// Shapes and helpers for the Health view: GET /api/checks (app.py: checks_report).

export type CheckStatus = "down" | "grace" | "unknown" | "new" | "paused" | "up";
export type Check = {
  name: string;
  slug: string;
  status: CheckStatus;
  last_ping: string | null;
  next_ping: string | null;
  grace_seconds: number | null;
  tags: string;
};
export type ChecksResponse = {
  configured: boolean;
  checks: Check[];
  counts: Partial<Record<CheckStatus, number>>;
  error?: string;
};

/** Dot colour token and plain-English label for a check's state. */
export const CHECK_STYLE: Record<CheckStatus, { dot: string; label: string }> = {
  down: { dot: "bg-destructive", label: "down" },
  grace: { dot: "bg-warn", label: "late" },
  unknown: { dot: "bg-idle", label: "unknown" },
  new: { dot: "bg-idle", label: "never pinged" },
  paused: { dot: "bg-idle", label: "paused" },
  up: { dot: "bg-ok", label: "up" },
};

/** One line for the card: "2 down · 1 late · 6 up", or "All 9 up". */
export function checksHeadline(data: ChecksResponse): string {
  const total = data.checks.length;
  if (!total) return "No checks yet";
  const up = data.counts.up ?? 0;
  if (up === total) return `All ${total} up`;
  const parts: string[] = [];
  for (const status of ["down", "grace", "unknown", "new", "paused", "up"] as const) {
    const n = data.counts[status];
    if (n) parts.push(`${n} ${CHECK_STYLE[status].label}`);
  }
  return parts.join(" · ");
}

/** Anything the owner should act on: a check that is down or late. */
export function checksNeedAttention(data: ChecksResponse | null): boolean {
  return !!data && ((data.counts.down ?? 0) > 0 || (data.counts.grace ?? 0) > 0);
}
