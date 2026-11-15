// Shapes and helpers for the Failures view: GET /api/failures (app.py: failures_report).

export type FailureGroup = {
  agent: string;
  signature: string;
  count: number;
  recurring: boolean;
  first_ts: string;
  last_ts: string;
  example: { title: string; reason: string };
  run_url: string;
  trace_url: string;
};
export type AgentFailures = { agent: string; last_24h: number; last_7d: number; rate: number | null };
export type FailuresResponse = {
  source: string;
  generated_at: string;
  window_days: number;
  totals: { last_24h: number; last_7d: number; recurring: number };
  agents: AgentFailures[];
  groups: FailureGroup[];
};

/** A failure today, or the same fault three times this week, is worth a look. */
export function failuresNeedAttention(data: FailuresResponse | null): boolean {
  return !!data && (data.totals.last_24h > 0 || data.totals.recurring > 0);
}

/** One line for the card: "2 today · 5 this week · 1 recurring". */
export function failuresHeadline(data: FailuresResponse): string {
  const { last_24h, last_7d, recurring } = data.totals;
  if (last_7d === 0) return `No failures in the last ${data.window_days} days.`;
  const parts = [`${last_24h} today`, `${last_7d} this week`];
  if (recurring > 0) parts.push(`${recurring} recurring`);
  return parts.join(" · ");
}

/** Share of finished tasks that failed, or a dash when the agent finished none. */
export function rateLabel(rate: number | null): string {
  return rate === null ? "–" : `${Math.round(rate * 100)}%`;
}
