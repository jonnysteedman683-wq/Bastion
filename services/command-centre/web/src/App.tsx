import { Activity, Bot, LayoutDashboard, ScrollText, ShieldCheck, TriangleAlert } from "lucide-react";
import { useState } from "react";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Separator } from "@/components/ui/separator";
import { Skeleton } from "@/components/ui/skeleton";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { useFeed, useNow, useOperations, useRoster } from "@/hooks";
import { isSafeGithubUrl, timeAgo, type Agent, type LedgerEvent, type OperationsResponse } from "@/lib/ledger";
import { cn } from "@/lib/utils";

type View = "operations" | "roster" | "ledger";

const NAV: { id: View; label: string; icon: typeof LayoutDashboard }[] = [
  { id: "operations", label: "Operations", icon: LayoutDashboard },
  { id: "roster", label: "Roster", icon: Bot },
  { id: "ledger", label: "Ledger", icon: ScrollText },
];

export default function App() {
  const [view, setView] = useState<View>("operations");
  const feed = useFeed();
  const roster = useRoster();
  const now = useNow();
  const operations = useOperations();
  const source = feed.source ?? roster.data?.source ?? null;

  return (
    <div className="flex min-h-svh flex-col md:flex-row">
      <aside className="flex shrink-0 flex-row items-center gap-1 border-b bg-card px-3 py-2 md:w-56 md:flex-col md:items-stretch md:border-r md:border-b-0 md:p-3">
        <div className="mr-auto flex items-center gap-2 px-2 py-1 md:mr-0 md:mb-4">
          <ShieldCheck className="size-5" aria-hidden />
          <span className="font-semibold tracking-tight">Bastion</span>
        </div>
        <nav aria-label="Main" className="flex gap-1 md:flex-col">
          {NAV.map(({ id, label, icon: Icon }) => (
            <button
              key={id}
              type="button"
              onClick={() => setView(id)}
              aria-current={view === id ? "page" : undefined}
              className={cn(
                "flex items-center gap-2 rounded-md px-2 py-1.5 text-sm text-muted-foreground transition-colors hover:bg-accent hover:text-accent-foreground",
                view === id && "bg-accent font-medium text-accent-foreground",
              )}
            >
              <Icon className="size-4" aria-hidden />
              <span className="hidden sm:inline">{label}</span>
            </button>
          ))}
        </nav>
      </aside>

      <main className="min-w-0 flex-1 space-y-6 p-4 md:p-8">
        <header className="flex flex-wrap items-center gap-3">
          <h1 className="text-2xl font-semibold tracking-tight">{NAV.find((n) => n.id === view)?.label}</h1>
          <SourceBadge source={source} />
        </header>
        {(feed.error || roster.error) && (
          <div role="alert" className="flex items-center gap-2 rounded-md border border-destructive/40 p-3 text-sm text-destructive">
            <TriangleAlert className="size-4" aria-hidden /> Can't reach the command centre API: {feed.error ?? roster.error}
          </div>
        )}

        {view === "operations" && <Operations data={operations.data} now={now} />}
        {view === "roster" && <RosterCard agents={roster.data?.agents} now={now} />}
        {view === "ledger" && <FeedCard events={feed.events} now={now} />}
      </main>
    </div>
  );
}

function Operations({ data, now }: { data: OperationsResponse | null; now: Date }) {
  if (!data) return <Skeleton className="h-96 w-full" />;
  const counts = data.tasks.reduce<Record<string, number>>((all, task) => ({ ...all, [task.status]: (all[task.status] ?? 0) + 1 }), {});
  return <div className="space-y-6">
    <div className="grid grid-cols-2 gap-4 lg:grid-cols-4">
      <Metric label="Needs attention" value={data.attention.length} />
      <Metric label="Completed" value={counts.done ?? 0} />
      <Metric label="In progress" value={(counts.open ?? 0) + (counts.claimed ?? 0)} />
      <Metric label="Open PRs" value={data.pr_report?.items.length ?? 0} />
    </div>
    <div className="grid gap-6 lg:grid-cols-2">
      <Card><CardHeader><CardTitle>Attention now</CardTitle><CardDescription>Blocked work and decisions that need you.</CardDescription></CardHeader><CardContent>{data.attention.length ? <ul className="space-y-3">{data.attention.map((item, i) => <li key={i}><Badge variant="outline" className="mr-2 border-red-500/40 text-red-700">{item.kind}</Badge><span className="font-medium">{item.title}</span><p className="mt-1 text-sm text-muted-foreground">{item.detail}</p></li>)}</ul> : <p className="text-sm text-muted-foreground">Nothing needs attention.</p>}</CardContent></Card>
      <Card><CardHeader><CardTitle>Goal progress</CardTitle><CardDescription>Tracked work by lifecycle status.</CardDescription></CardHeader><CardContent><div className="flex flex-wrap gap-2">{Object.entries(counts).map(([status, count]) => <Badge key={status} variant="secondary">{status}: {count}</Badge>)}</div><ul className="mt-4 space-y-2">{data.tasks.slice(0, 8).map(task => <li key={task.task_id} className="flex justify-between gap-3 text-sm"><span>{task.title}</span><span className="text-muted-foreground">{task.status}</span></li>)}</ul></CardContent></Card>
      <Card><CardHeader><CardTitle>Pending merge requests</CardTitle><CardDescription>{data.pr_report ? `Scanned ${timeAgo(data.pr_report.scanned_ts, now)}` : "No PR report received yet."}</CardDescription></CardHeader><CardContent>{data.pr_report ? <ul className="space-y-3">{data.pr_report.items.map(pr => <li key={pr.pr} className="text-sm"><Badge variant="outline" className="mr-2">{pr.bucket}</Badge>{isSafeGithubUrl(pr.url) ? <a className="font-medium underline" href={pr.url}>{pr.pr}</a> : <span className="font-medium">{pr.pr}</span>}<span className="ml-2 text-muted-foreground">{pr.note}</span></li>)}</ul> : <p className="text-sm text-muted-foreground">The PR steward has not reported yet.</p>}</CardContent></Card>
      <FeedCard events={data.completions} now={now} limit={8} />
    </div>
  </div>;
}

function Metric({ label, value }: { label: string; value: number }) { return <Card className="gap-2 py-4"><CardHeader className="px-4"><CardDescription>{label}</CardDescription></CardHeader><CardContent className="px-4 text-2xl font-semibold tabular-nums">{value}</CardContent></Card>; }

function SourceBadge({ source }: { source: "demo" | "ledger" | null }) {
  if (!source) return null;
  return source === "ledger" ? (
    <Badge variant="outline" className="gap-1.5">
      <span className="size-2 rounded-full bg-ok" aria-hidden /> Live ledger
    </Badge>
  ) : (
    <Badge variant="secondary" title="No ledger connected yet. These events are generated for the demo.">
      Demo data
    </Badge>
  );
}

const STATUS_DOT: Record<Agent["status"], string> = { active: "bg-ok", idle: "bg-warn", silent: "bg-idle" };

function RosterCard({
  agents,
  now,
  compact,
  className,
}: {
  agents?: Agent[];
  now: Date;
  compact?: boolean;
  className?: string;
}) {
  return (
    <Card className={className}>
      <CardHeader>
        <CardTitle className="flex items-center gap-2">
          <Bot className="size-4" aria-hidden /> Roster
        </CardTitle>
        <CardDescription>Every agent, and when it last wrote to the ledger.</CardDescription>
      </CardHeader>
      <CardContent className="overflow-x-auto">
        {!agents ? (
          <Skeleton className="h-40 w-full" />
        ) : (
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>Agent</TableHead>
                {!compact && <TableHead>Runs on</TableHead>}
                <TableHead className="text-right">Last seen</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {agents.map((a) => (
                <TableRow key={a.name}>
                  <TableCell>
                    <div className="flex items-center gap-2">
                      <span className={cn("size-2 shrink-0 rounded-full", STATUS_DOT[a.status])} title={a.status} aria-label={a.status} />
                      <div>
                        <div className="font-medium">{a.name}</div>
                        <div className="text-xs text-muted-foreground">{a.role}</div>
                      </div>
                    </div>
                  </TableCell>
                  {!compact && <TableCell className="text-muted-foreground">{a.runs_on}</TableCell>}
                  <TableCell className="text-right tabular-nums text-muted-foreground">{timeAgo(a.last_seen, now)}</TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        )}
      </CardContent>
    </Card>
  );
}

const KIND_STYLE: Record<string, string> = {
  "task.request": "border-sky-500/40 text-sky-700 dark:text-sky-300",
  "task.result": "border-emerald-500/40 text-emerald-700 dark:text-emerald-300",
  "deploy.record": "border-violet-500/40 text-violet-700 dark:text-violet-300",
  "memory.write": "border-amber-500/40 text-amber-700 dark:text-amber-300",
  "task.claim": "border-sky-500/40 text-sky-700 dark:text-sky-300",
  "task.failed": "border-red-500/40 text-red-700 dark:text-red-300",
  escalate: "border-red-500/40 text-red-700 dark:text-red-300",
  "approval.request": "border-fuchsia-500/40 text-fuchsia-700 dark:text-fuchsia-300",
  "approval.decision": "border-fuchsia-500/40 text-fuchsia-700 dark:text-fuchsia-300",
  heartbeat: "text-muted-foreground",
};

function FeedCard({ events, now, limit, className }: { events: LedgerEvent[]; now: Date; limit?: number; className?: string }) {
  const shown = limit ? events.slice(0, limit) : events;
  return (
    <Card className={className}>
      <CardHeader>
        <CardTitle className="flex items-center gap-2">
          <Activity className="size-4" aria-hidden /> Live feed
        </CardTitle>
        <CardDescription>Newest first. Updates every 5 seconds.</CardDescription>
      </CardHeader>
      <CardContent>
        {shown.length === 0 ? (
          <Skeleton className="h-40 w-full" />
        ) : (
          <ol aria-live="polite" className="space-y-0">
            {shown.map((e, i) => (
              <li key={e.id}>
                {i > 0 && <Separator />}
                <div className="flex flex-wrap items-baseline gap-x-3 gap-y-1 py-2.5">
                  <Badge variant="outline" className={cn("font-mono text-[11px]", KIND_STYLE[e.kind])}>
                    {e.kind}
                  </Badge>
                  <span className="text-sm font-medium">{e.agent}</span>
                  <span className="min-w-0 flex-1 basis-60 text-sm text-muted-foreground">{e.summary}</span>
                  <time dateTime={e.ts} title={e.ts} className="text-xs tabular-nums text-muted-foreground">
                    {timeAgo(e.ts, now)}
                  </time>
                </div>
              </li>
            ))}
          </ol>
        )}
      </CardContent>
    </Card>
  );
}
