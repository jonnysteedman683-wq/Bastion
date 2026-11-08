import { CircleAlert, CircleCheck, Coins, OctagonX } from "lucide-react";
import { Bar, BarChart, CartesianGrid, ReferenceLine, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import type { TooltipContentProps } from "recharts";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { capShare, formatTokens, formatUsd, shortDay, type CapStatus, type SpendDay, type SpendResponse } from "@/lib/spend";
import { cn } from "@/lib/utils";

const STATUS: Record<Exclude<CapStatus, "none">, { label: string; icon: typeof CircleCheck; text: string; bar: string }> = {
  ok: { label: "under cap", icon: CircleCheck, text: "text-muted-foreground", bar: "bg-ok" },
  near: { label: "near cap", icon: CircleAlert, text: "text-amber-700 dark:text-amber-300", bar: "bg-warn" },
  over: { label: "over cap", icon: OctagonX, text: "text-red-700 dark:text-red-300", bar: "bg-destructive" },
};

export function SpendView({ data }: { data: SpendResponse | null }) {
  if (!data) return <Skeleton className="h-96 w-full" />;
  return (
    <div className="space-y-6">
      {!data.reporting && (
        <Card>
          <CardHeader>
            <CardTitle>No agent reports usage yet</CardTitle>
            <CardDescription>
              Spend appears here once agents add a <code>usage</code> object to their ledger events, for example on{" "}
              <code>task.result</code>: <code>{'"usage": {"model": "…", "input_tokens": 1234, "output_tokens": 567, "cost_usd": 0.0123}'}</code>
            </CardDescription>
          </CardHeader>
        </Card>
      )}
      <div className="grid grid-cols-2 gap-4 lg:grid-cols-4">
        <TodayTile data={data} />
        <Tile label="Last 7 days" value={formatUsd(data.last_7d.cost_usd)} note={`${data.last_7d.runs} runs`} />
        <Tile label="Last 30 days" value={formatUsd(data.last_30d.cost_usd)} note={`${data.last_30d.runs} runs`} />
        <Tile
          label="Tokens, 7 days"
          value={formatTokens(data.last_7d.input_tokens + data.last_7d.output_tokens)}
          note={`${formatTokens(data.last_7d.input_tokens)} in · ${formatTokens(data.last_7d.output_tokens)} out`}
        />
      </div>
      <DailyChart daily={data.daily} cap={data.caps.daily_usd} />
      <AgentTable data={data} />
    </div>
  );
}

function Tile({ label, value, note, children }: { label: string; value: string; note?: string; children?: React.ReactNode }) {
  return (
    <Card className="gap-2 py-4">
      <CardHeader className="px-4">
        <CardDescription>{label}</CardDescription>
      </CardHeader>
      <CardContent className="space-y-2 px-4">
        <div className="text-2xl font-semibold tabular-nums">{value}</div>
        {note && <div className="text-xs text-muted-foreground">{note}</div>}
        {children}
      </CardContent>
    </Card>
  );
}

function TodayTile({ data }: { data: SpendResponse }) {
  const { today } = data;
  return (
    <Tile label="Today (UTC)" value={formatUsd(today.cost_usd)}>
      {today.cap_usd === null ? (
        <div className="text-xs text-muted-foreground">No fleet cap set</div>
      ) : (
        <CapMeter spent={today.cost_usd} cap={today.cap_usd} status={today.cap_status} />
      )}
    </Tile>
  );
}

function CapMeter({ spent, cap, status }: { spent: number; cap: number | null; status: CapStatus }) {
  const share = capShare(spent, cap);
  if (share === null || status === "none") return <span className="text-xs text-muted-foreground">no cap</span>;
  const s = STATUS[status];
  const Icon = s.icon;
  return (
    <div className="min-w-28 space-y-1">
      <div
        className="h-1.5 w-full overflow-hidden rounded-full bg-muted"
        role="meter"
        aria-valuemin={0}
        aria-valuemax={cap ?? 0}
        aria-valuenow={spent}
        aria-label={`${formatUsd(spent)} of ${formatUsd(cap ?? 0)} daily cap`}
      >
        <div className={cn("h-full rounded-full", s.bar)} style={{ width: `${Math.max(share * 100, 2)}%` }} />
      </div>
      <div className={cn("flex items-center gap-1 text-xs", s.text)}>
        <Icon className="size-3.5" aria-hidden /> {s.label} · {formatUsd(cap ?? 0)}/day
      </div>
    </div>
  );
}

function DailyTooltip({ active, payload }: TooltipContentProps) {
  const day = active ? (payload?.[0]?.payload as SpendDay | undefined) : undefined;
  if (!day) return null;
  return (
    <div className="rounded-md border bg-popover px-3 py-2 text-xs text-popover-foreground shadow-md">
      <div className="font-medium">{shortDay(day.day)}</div>
      <div className="tabular-nums">{formatUsd(day.cost_usd)}</div>
      <div className="text-muted-foreground tabular-nums">
        {formatTokens(day.input_tokens + day.output_tokens)} tokens · {day.runs} runs
      </div>
    </div>
  );
}

function DailyChart({ daily, cap }: { daily: SpendDay[]; cap: number | null }) {
  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center gap-2">
          <Coins className="size-4" aria-hidden /> Daily spend, last 30 days
        </CardTitle>
        <CardDescription>USD per UTC day, all agents.{cap !== null && ` Dashed line: fleet cap of ${formatUsd(cap)}/day.`}</CardDescription>
      </CardHeader>
      <CardContent className="space-y-3">
        <div className="h-56 w-full" role="img" aria-label="Bar chart of daily spend over the last 30 days; a table follows.">
          <ResponsiveContainer width="100%" height="100%">
            <BarChart data={daily} margin={{ top: 8, right: 8, bottom: 0, left: 0 }} barCategoryGap={2}>
              <CartesianGrid vertical={false} stroke="var(--border)" />
              <XAxis
                dataKey="day"
                tickFormatter={shortDay}
                tickLine={false}
                axisLine={false}
                interval="preserveStartEnd"
                minTickGap={24}
                tick={{ fill: "var(--muted-foreground)", fontSize: 11 }}
              />
              <YAxis
                width={56}
                tickFormatter={(v: number) => formatUsd(v)}
                tickLine={false}
                axisLine={false}
                tick={{ fill: "var(--muted-foreground)", fontSize: 11 }}
              />
              <Tooltip content={DailyTooltip} cursor={{ fill: "var(--muted)" }} />
              {cap !== null && <ReferenceLine y={cap} stroke="var(--muted-foreground)" strokeDasharray="4 4" ifOverflow="extendDomain" />}
              <Bar dataKey="cost_usd" fill="var(--chart-1)" radius={[4, 4, 0, 0]} maxBarSize={24} isAnimationActive={false} />
            </BarChart>
          </ResponsiveContainer>
        </div>
        <details className="text-sm">
          <summary className="cursor-pointer text-muted-foreground">Show as table</summary>
          <Table className="mt-2">
            <TableHeader>
              <TableRow>
                <TableHead>Day</TableHead>
                <TableHead className="text-right">Spend</TableHead>
                <TableHead className="text-right">Tokens</TableHead>
                <TableHead className="text-right">Runs</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {[...daily].reverse().map((d) => (
                <TableRow key={d.day}>
                  <TableCell>{shortDay(d.day)}</TableCell>
                  <TableCell className="text-right tabular-nums">{formatUsd(d.cost_usd)}</TableCell>
                  <TableCell className="text-right tabular-nums">{formatTokens(d.input_tokens + d.output_tokens)}</TableCell>
                  <TableCell className="text-right tabular-nums">{d.runs}</TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </details>
      </CardContent>
    </Card>
  );
}

function AgentTable({ data }: { data: SpendResponse }) {
  return (
    <Card>
      <CardHeader>
        <CardTitle>By agent</CardTitle>
        <CardDescription>Today against each agent's daily cap; highest 30-day spend first.</CardDescription>
      </CardHeader>
      <CardContent className="overflow-x-auto">
        {data.agents.length === 0 ? (
          <p className="text-sm text-muted-foreground">No usage reported in the last 30 days.</p>
        ) : (
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>Agent</TableHead>
                <TableHead className="text-right">Today</TableHead>
                <TableHead>Daily cap</TableHead>
                <TableHead className="text-right">7 days</TableHead>
                <TableHead className="text-right">30 days</TableHead>
                <TableHead className="text-right">Tokens, 7d</TableHead>
                <TableHead className="text-right">Runs, 7d</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {data.agents.map((a) => (
                <TableRow key={a.agent}>
                  <TableCell className="font-medium">{a.agent}</TableCell>
                  <TableCell className="text-right tabular-nums">{formatUsd(a.today_usd)}</TableCell>
                  <TableCell>
                    <CapMeter spent={a.today_usd} cap={a.cap_usd} status={a.cap_status} />
                  </TableCell>
                  <TableCell className="text-right tabular-nums">{formatUsd(a.last_7d_usd)}</TableCell>
                  <TableCell className="text-right tabular-nums">{formatUsd(a.last_30d_usd)}</TableCell>
                  <TableCell className="text-right tabular-nums">{formatTokens(a.last_7d_tokens)}</TableCell>
                  <TableCell className="text-right tabular-nums">{a.last_7d_runs}</TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        )}
      </CardContent>
    </Card>
  );
}
