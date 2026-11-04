import { Activity, Bot, LayoutDashboard, ScrollText, ShieldCheck, TriangleAlert } from "lucide-react";
import { useState } from "react";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Separator } from "@/components/ui/separator";
import { Skeleton } from "@/components/ui/skeleton";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { useFeed, useNow, useRoster } from "@/hooks";
import { timeAgo, type Agent, type LedgerEvent } from "@/lib/ledger";
import { cn } from "@/lib/utils";

type View = "overview" | "roster" | "ledger";

const NAV: { id: View; label: string; icon: typeof LayoutDashboard }[] = [
  { id: "overview", label: "Overview", icon: LayoutDashboard },
  { id: "roster", label: "Roster", icon: Bot },
  { id: "ledger", label: "Ledger", icon: ScrollText },
];

export default function App() {
  const [view, setView] = useState<View>("overview");
  const feed = useFeed();
  const roster = useRoster();
  const now = useNow();
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

        {view === "overview" && (
          <>
            <Stats agents={roster.data?.agents} events={feed.events} now={now} />
            <div className="grid gap-6 lg:grid-cols-5">
              <RosterCard agents={roster.data?.agents} now={now} compact className="min-w-0 lg:col-span-2" />
              <FeedCard events={feed.events} now={now} limit={12} className="min-w-0 lg:col-span-3" />
            </div>
          </>
        )}
        {view === "roster" && <RosterCard agents={roster.data?.agents} now={now} />}
        {view === "ledger" && <FeedCard events={feed.events} now={now} />}
      </main>
    </div>
  );
}

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

function Stats({ agents, events, now }: { agents?: Agent[]; events: LedgerEvent[]; now: Date }) {
  const dayAgo = now.getTime() - 86_400_000;
  const today = events.filter((e) => new Date(e.ts).getTime() >= dayAgo);
  const lastDeploy = events.find((e) => e.kind === "deploy.record");
  const items = [
    { label: "Agents active", value: agents ? `${agents.filter((a) => a.status === "active").length} / ${agents.length}` : null },
    { label: "Events, last 24h", value: String(today.length) },
    { label: "Task requests, last 24h", value: String(today.filter((e) => e.kind === "task.request").length) },
    { label: "Last deploy", value: lastDeploy ? timeAgo(lastDeploy.ts, now) : "none yet" },
  ];
  return (
    <div className="grid grid-cols-2 gap-4 lg:grid-cols-4">
      {items.map((it) => (
        <Card key={it.label} className="gap-2 py-4">
          <CardHeader className="px-4">
            <CardDescription>{it.label}</CardDescription>
          </CardHeader>
          <CardContent className="px-4 text-2xl font-semibold tabular-nums">
            {it.value ?? <Skeleton className="h-8 w-16" />}
          </CardContent>
        </Card>
      ))}
    </div>
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
