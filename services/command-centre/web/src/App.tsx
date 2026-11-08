import { Activity, Bot, Check, ClipboardCopy, Inbox, LayoutDashboard, ScrollText, ShieldCheck, TriangleAlert } from "lucide-react";
import { FormEvent, useState } from "react";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Separator } from "@/components/ui/separator";
import { Skeleton } from "@/components/ui/skeleton";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { useApprovals, useFeed, useNow, useOperations, useRoster } from "@/hooks";
import { isOverdue, isSafeGithubUrl, timeAgo, waitingSince, type Agent, type Approval, type LedgerEvent, type OperationsResponse } from "@/lib/ledger";
import { cn } from "@/lib/utils";
import { requestMagicLink, signOut } from "@/lib/auth";

export type View = "operations" | "approvals" | "roster" | "ledger";

const NAV: { id: View; label: string; icon: typeof LayoutDashboard }[] = [
  { id: "operations", label: "Operations", icon: LayoutDashboard },
  { id: "approvals", label: "Approvals", icon: Inbox },
  { id: "roster", label: "Roster", icon: Bot },
  { id: "ledger", label: "Ledger", icon: ScrollText },
];

export default function App() {
  if (window.location.pathname === "/login") return <Login />;
  const [view, setView] = useState<View>("operations");
  const feed = useFeed();
  const roster = useRoster();
  const now = useNow();
  const operations = useOperations();
  const approvals = useApprovals();
  const pendingCount = approvals.data?.approvals.length ?? 0;
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
              {id === "approvals" && pendingCount > 0 && (
                <span className="ml-auto rounded-full bg-primary px-1.5 text-xs tabular-nums text-primary-foreground" aria-label={`${pendingCount} pending`}>
                  {pendingCount}
                </span>
              )}
            </button>
          ))}
        </nav>
      </aside>

      <main className="min-w-0 flex-1 space-y-6 p-4 md:p-8">
        <header className="flex flex-wrap items-center gap-3">
          <h1 className="text-2xl font-semibold tracking-tight">{NAV.find((n) => n.id === view)?.label}</h1>
          <SourceBadge source={source} />
          <button className="ml-auto text-sm text-muted-foreground underline" onClick={() => void signOut().then(() => window.location.assign("/login"))}>Sign out</button>
        </header>
        {(feed.error || roster.error || operations.error || approvals.error) && (
          <div role="alert" className="flex items-center gap-2 rounded-md border border-destructive/40 p-3 text-sm text-destructive">
            <TriangleAlert className="size-4" aria-hidden /> Can't reach the command centre API: {feed.error ?? roster.error ?? operations.error ?? approvals.error}
          </div>
        )}

        {view === "operations" && <Operations data={operations.data} now={now} />}
        {view === "approvals" && <Approvals items={approvals.data?.approvals} now={now} />}
        {view === "roster" && <RosterCard agents={roster.data?.agents} now={now} />}
        {view === "ledger" && <FeedCard events={feed.events} now={now} />}
      </main>
    </div>
  );
}

function Login() {
  const [email, setEmail] = useState("");
  const [state, setState] = useState<"idle" | "sending" | "sent" | "error">("idle");

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setState("sending");
    try {
      await requestMagicLink(email);
      setState("sent");
    } catch {
      setState("error");
    }
  }

  return <main className="mx-auto flex min-h-svh max-w-md items-center p-6"><Card className="w-full"><CardHeader><CardTitle>Sign in to Bastion</CardTitle><CardDescription>Enter your approved email address and we’ll send a sign-in link.</CardDescription></CardHeader><CardContent><form className="space-y-4" onSubmit={submit}><label className="block text-sm font-medium" htmlFor="email">Email</label><input id="email" className="w-full rounded-md border bg-background px-3 py-2" type="email" value={email} onChange={(event) => setEmail(event.target.value)} required autoComplete="email" /><button className="w-full rounded-md bg-primary px-3 py-2 text-primary-foreground disabled:opacity-60" disabled={state === "sending"}>{state === "sending" ? "Sending…" : "Email me a sign-in link"}</button>{state === "sent" && <p className="text-sm text-muted-foreground">If the account is eligible, a sign-in link has been sent.</p>}{state === "error" && <p className="text-sm text-destructive">We couldn’t send that link. Please try again.</p>}</form></CardContent></Card></main>;
}

function Operations({ data, now }: { data: OperationsResponse | null; now: Date }) {
  if (!data) return <Skeleton className="h-96 w-full" />;
  const counts = data.tasks.reduce<Record<string, number>>((all, task) => ({ ...all, [task.status]: (all[task.status] ?? 0) + 1 }), {});
  const staleReport = data.pr_report && now.getTime() - new Date(data.pr_report.scanned_ts).getTime() > 26 * 60 * 60 * 1000;
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
      <Card><CardHeader><CardTitle>Pending merge requests</CardTitle><CardDescription>{data.pr_report ? `${staleReport ? "Report is stale — " : ""}Scanned ${timeAgo(data.pr_report.scanned_ts, now)}` : "No PR report received yet."}</CardDescription></CardHeader><CardContent>{data.pr_report ? <ul className="space-y-3">{data.pr_report.items.map(pr => <li key={pr.pr} className="text-sm"><Badge variant="outline" className="mr-2">{pr.bucket}</Badge>{isSafeGithubUrl(pr.url) ? <a className="font-medium underline" href={pr.url}>{pr.pr}</a> : <span className="font-medium">{pr.pr}</span>}<span className="ml-2 text-muted-foreground">{pr.note}</span></li>)}</ul> : <p className="text-sm text-muted-foreground">The PR steward has not reported yet.</p>}</CardContent></Card>
      <FeedCard events={data.completions} now={now} limit={8} />
    </div>
  </div>;
}

function Approvals({ items, now }: { items?: Approval[]; now: Date }) {
  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center gap-2">
          <Inbox className="size-4" aria-hidden /> Waiting for you
        </CardTitle>
        <CardDescription>
          Oldest first. Decisions are signed by your owner key, which lives only on your PC: copy a command and run it
          there. Bastion never holds the key.
        </CardDescription>
      </CardHeader>
      <CardContent>
        {!items ? (
          <Skeleton className="h-40 w-full" />
        ) : items.length === 0 ? (
          <p className="text-sm text-muted-foreground">Nothing is waiting for your approval.</p>
        ) : (
          <ul className="space-y-0">
            {items.map((item, i) => {
              const overdue = isOverdue(item, now);
              return (
                <li key={item.task_id}>
                  {i > 0 && <Separator />}
                  <div className="space-y-2 py-3">
                    <div className="flex flex-wrap items-baseline gap-x-3 gap-y-1">
                      <span className="font-medium">{item.title || item.task_id}</span>
                      <code className="font-mono text-xs text-muted-foreground">{item.task_id}</code>
                      {overdue && (
                        <Badge variant="outline" className="border-red-500/40 text-red-700 dark:text-red-300">
                          waiting over a day
                        </Badge>
                      )}
                      <span className="ml-auto text-xs tabular-nums text-muted-foreground">
                        asked {timeAgo(waitingSince(item), now)}
                      </span>
                    </div>
                    <p className="text-sm text-muted-foreground">
                      {[item.requested_by && `from ${item.requested_by}`, item.agent && `for ${item.agent}`, item.mode && `mode ${item.mode}`]
                        .filter(Boolean)
                        .join(" · ")}
                      {item.detail && <> — {item.detail}</>}
                      {isSafeGithubUrl(item.run_url) && (
                        <> · <a className="underline" href={item.run_url}>run</a></>
                      )}
                    </p>
                    {item.approve_command && item.reject_command ? (
                      <div className="flex flex-wrap gap-2">
                        <CopyCommand label="Approve" command={item.approve_command} />
                        <CopyCommand label="Reject" command={item.reject_command} />
                      </div>
                    ) : (
                      <p className="text-sm text-destructive">
                        This task id has unusual characters, so no command is offered. Decide it from <code>linx pending</code>.
                      </p>
                    )}
                  </div>
                </li>
              );
            })}
          </ul>
        )}
      </CardContent>
    </Card>
  );
}

function CopyCommand({ label, command }: { label: string; command: string }) {
  const [copied, setCopied] = useState(false);
  async function copy() {
    try {
      await navigator.clipboard.writeText(command);
      setCopied(true);
      window.setTimeout(() => setCopied(false), 2000);
    } catch {
      setCopied(false);
    }
  }
  return (
    <button
      type="button"
      onClick={() => void copy()}
      title={command}
      className="inline-flex items-center gap-2 rounded-md border px-2.5 py-1 text-sm hover:bg-accent"
    >
      {copied ? <Check className="size-4" aria-hidden /> : <ClipboardCopy className="size-4" aria-hidden />}
      <span>{copied ? "Copied" : label}</span>
      <code className="hidden font-mono text-xs text-muted-foreground md:inline">{command}</code>
    </button>
  );
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
