// Shapes and formatting for GET /api/spend (app.py: summarise_spend).

export type CapStatus = "none" | "ok" | "near" | "over";
export type SpendWindow = { cost_usd: number; input_tokens: number; output_tokens: number; runs: number };
export type SpendDay = SpendWindow & { day: string };
export type AgentSpend = {
  agent: string;
  today_usd: number;
  last_7d_usd: number;
  last_30d_usd: number;
  last_7d_tokens: number;
  last_7d_runs: number;
  cap_usd: number | null;
  cap_status: CapStatus;
};
export type SpendResponse = {
  source: "demo" | "ledger";
  generated_at: string;
  currency: "USD";
  reporting: boolean;
  caps: { daily_usd: number | null; default_agent_daily_usd: number | null; agent_daily_usd: Record<string, number> };
  today: SpendWindow & { cap_usd: number | null; cap_status: CapStatus };
  last_7d: SpendWindow;
  last_30d: SpendWindow;
  daily: SpendDay[];
  agents: AgentSpend[];
};

const usd = new Intl.NumberFormat("en-US", { style: "currency", currency: "USD" });

/** Dollars with cents; small but real spend keeps 4 decimals so it never reads as $0.00. */
export function formatUsd(value: number): string {
  if (value > 0 && value < 0.0001) return "<$0.0001";
  if (value > 0 && value < 0.01) return "$" + value.toFixed(4);
  return usd.format(value);
}

export function formatTokens(value: number): string {
  if (value >= 1_000_000) return String(+(value / 1_000_000).toFixed(2)) + "M";
  if (value >= 1_000) return String(+(value / 1_000).toFixed(1)) + "k";
  return String(value);
}

/** Share of a cap used, clamped to 0..1, or null when there is no cap. */
export function capShare(spent: number, cap: number | null): number | null {
  if (!cap || cap <= 0) return null;
  return Math.min(1, Math.max(0, spent / cap));
}

export function shortDay(day: string): string {
  return new Date(day + "T00:00:00Z").toLocaleDateString("en-GB", { day: "numeric", month: "short", timeZone: "UTC" });
}
