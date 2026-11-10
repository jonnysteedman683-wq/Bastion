import { Activity, Bot, Check, ClipboardCopy, Coins, Inbox, LayoutDashboard, Map, OctagonPause, Power, ScrollText, ShieldCheck, TriangleAlert } from "lucide-react";
import { FormEvent, Suspense, lazy, useEffect, useMemo, useState } from "react";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Separator } from "@/components/ui/separator";
import { Skeleton } from "@/components/ui/skeleton";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { useApprovals, useFeed, useNow, useOperations, useRoster, useSpend } from "@/hooks";

import { isOverdue, isSafeGithubUrl, killSwitchCommand, timeAgo, waitingSince, type Agent, type Approval, type Control, type LedgerEvent, type OperationsResponse } from "@/lib/ledger";
import { cn } from "@/lib/utils";
import { requestMagicLink, signOut } from "@/lib/auth";

const SpendView = lazy(() => import("@/components/spend-view").then((m) => ({ default: m.SpendView })));

export type View = "operations" | "approvals" | "spend" | "roster" | "ledger" | "map";

const NAV: { id: View; label: string; icon: typeof LayoutDashboard }[] = [
  { id: "operations", label: "Operations", icon: LayoutDashboard },
  { id: "map", label: "Command map", icon: Map },
  { id: "approvals", label: "Approvals", icon: Inbox },
  { id: "spend", label: "Spend", icon: Coins },
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
  const spend = useSpend();
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
        {(feed.error || roster.error || operations.error || approvals.error || spend.error) && (
          <div role="alert" className="flex items-center gap-2 rounded-md border border-destructive/40 p-3 text-sm text-destructive">
            <TriangleAlert className="size-4" aria-hidden /> Can't reach the command centre API: {feed.error ?? roster.error ?? operations.error ?? approvals.error ?? spend.error}
          </div>
        )}

        {operations.data?.control.halted && <HaltBanner control={operations.data.control} now={now} />}

        {view === "operations" && <Operations data={operations.data} now={now} />}
        {view === "map" && <CommandMap agents={roster.data?.agents} events={feed.events} now={now} />}
        {view === "approvals" && <Approvals items={approvals.data?.approvals} now={now} />}
        {view === "spend" && (
          <Suspense fallback={<Skeleton className="h-96 w-full" />}>
            <SpendView data={spend.data} />
          </Suspense>
        )}
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

  return (
    <main className="mx-auto flex min-h-svh max-w-md items-center p-6">
      <Card className="w-full">
        <CardHeader>
          <CardTitle>Sign in to Bastion</CardTitle>
          <CardDescription>Enter your approved email to receive a secure sign-in link.</CardDescription>
        </CardHeader>
        <CardContent>
          <form onSubmit={submit} className="space-y-4">
            <input
              type="email"
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              className="w-full rounded-md border bg-background px-3 py-2 text-sm"
              placeholder="you@example.com"
              autoComplete="email"
              required
            />
            <button
              type="submit"
              disabled={state === "sending"}
              className="w-full rounded-md bg-primary px-3 py-2 text-sm font-medium text-primary-foreground disabled:opacity-60"
            >
              {state === "sending" ? "Sending link..." : state === "sent" ? "Link sent" : "Send sign-in link"}
            </button>
            {state === "error" && <p className="text-sm text-destructive">We could not send the link. Try again.</p>}
          </form>
        </CardContent>
      </Card>
    </main>
  );
}

function Operations({ data, now }: { data: OperationsResponse | null; now: Date }) {
  if (!data) return <Skeleton className="h-96 w-full" />;
  const counts = data.tasks.reduce<Record<string, number>>((all, task) => ({ ...all, [task.status]: (all[task.status] ?? 0) + 1 }), {});
  const staleReport = data.pr_report && now.getTime() - new Date(data.pr_report.scanned_ts).getTime() > 26 * 60 * 60 * 1000;

  return (
    <div className="space-y-6">
      <KillSwitch control={data.control} now={now} />
      <div className="grid grid-cols-2 gap-4 lg:grid-cols-4">
        <Metric label="Needs attention" value={data.attention.length} />
        <Metric label="Completed" value={counts.done ?? 0} />
        <Metric label="In progress" value={(counts.open ?? 0) + (counts.claimed ?? 0)} />
        <Metric label="Open PRs" value={data.pr_report?.items.length ?? 0} />
      </div>
      <div className="grid gap-6 lg:grid-cols-2">
        <Card>
          <CardHeader>
            <CardTitle>Attention now</CardTitle>
            <CardDescription>Blocked work and decisions that need you.</CardDescription>
          </CardHeader>
          <CardContent>
            {data.attention.length ? (
              <ul className="space-y-3 text-sm">
                {data.attention.map((item) => (
                  <li key={`${item.kind}-${item.title}`} className="rounded-md border bg-muted/30 p-3">
                    <div className="font-medium">{item.title}</div>
                    <div className="mt-1 text-muted-foreground">{item.detail}</div>
                  </li>
                ))}
              </ul>
            ) : (
              <p className="text-sm text-muted-foreground">No open blockers.</p>
            )}
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle>Goal progress</CardTitle>
            <CardDescription>Tracked work by lifecycle status.</CardDescription>
          </CardHeader>
          <CardContent>
            <div className="flex flex-wrap gap-2">
              {Object.entries(counts).map(([status, count]) => (
                <Badge key={status} variant="secondary">
                  {status}: {count}
                </Badge>
              ))}
            </div>
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle>Pending merge requests</CardTitle>
            <CardDescription>
              {data.pr_report ? `${staleReport ? "Report is stale — " : ""}Scanned ${timeAgo(data.pr_report.scanned_ts, now)}` : "No steward report available."}
            </CardDescription>
          </CardHeader>
          <CardContent>
            {data.pr_report ? (
              <ul className="space-y-2 text-sm">
                {data.pr_report.items.slice(0, 3).map((item) => (
                  <li key={item.pr} className="rounded-md border p-2">
                    <div className="flex items-center justify-between gap-3">
                      <span className="font-medium">{item.pr}</span>
                      <Badge variant="outline">{item.bucket}</Badge>
                    </div>
                    <div className="mt-1 text-muted-foreground">{item.note}</div>
                  </li>
                ))}
              </ul>
            ) : (
              <p className="text-sm text-muted-foreground">No PR data available yet.</p>
            )}
          </CardContent>
        </Card>

        <FeedCard events={data.completions} now={now} limit={8} />
      </div>
    </div>
  );
}

function Approvals({ items, now }: { items?: Approval[]; now: Date }) {
  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center gap-2">
          <Inbox className="size-4" aria-hidden /> Waiting for you
        </CardTitle>
        <CardDescription>
          Oldest first. Decisions are signed by your owner key, which lives only on your PC: copy a command and run it there. Bastion never holds the key.
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
                      <span className="ml-auto text-xs tabular-nums text-muted-foreground">asked {timeAgo(waitingSince(item), now)}</span>
                    </div>
                    <p className="text-sm text-muted-foreground">
                      {[item.requested_by && `from ${item.requested_by}`, item.agent && `for ${item.agent}`, item.mode && `mode ${item.mode}`]
                        .filter(Boolean)
                        .join(" · ")}
                      {item.detail && <> — {item.detail}</>}
                      {isSafeGithubUrl(item.run_url) && <> · <a className="underline" href={item.run_url}>run</a></>}
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

function HaltBanner({ control, now }: { control: Control; now: Date }) {
  return (
    <div role="alert" className="flex flex-wrap items-center gap-x-3 gap-y-1 rounded-md border border-red-500/50 bg-red-500/10 p-3 text-sm text-red-800 dark:text-red-200">
      <OctagonPause className="size-5 shrink-0" aria-hidden />
      <span className="font-semibold">Fleet halted</span>
      {control.since && <span>since {timeAgo(control.since, now)}</span>}
      <span className="min-w-0 basis-full sm:basis-auto">{control.reason || "No reason given."}</span>
    </div>
  );
}

function KillSwitch({ control, now }: { control: Control; now: Date }) {
  const next = killSwitchCommand(control);
  return (
    <Card className={cn(control.halted && "border-red-500/50")}>
      <CardHeader>
        <CardTitle className="flex items-center gap-2">
          <Power className="size-4" aria-hidden /> Kill switch
          <Badge variant="outline" className={cn("ml-1", control.halted ? "border-red-500/40 text-red-700 dark:text-red-300" : "border-emerald-500/40 text-emerald-700 dark:text-emerald-300")}>
            {control.halted ? "halted" : "running"}
          </Badge>
        </CardTitle>
        <CardDescription>
          {control.halted ? `Nightly work and the memory loop are stopped${control.since ? ` (${timeAgo(control.since, now)})` : ""}.` : "Halting stops nightly work and pauses the memory loop."} Only your owner key can flip it: copy the command and run it on your PC.
        </CardDescription>
      </CardHeader>
      <CardContent>
        <CopyCommand label={next.action} command={next.command} />
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

function Metric({ label, value }: { label: string; value: number }) {
  return (
    <Card className="gap-2 py-4">
      <CardHeader className="px-4">
        <CardDescription>{label}</CardDescription>
      </CardHeader>
      <CardContent className="px-4 pb-4 text-2xl font-semibold tracking-tight">{value}</CardContent>
    </Card>
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

const STATUS_DOT: Record<Agent["status"], string> = { active: "bg-ok", idle: "bg-warn", silent: "bg-idle" };

function CommandMap({ agents, events, now }: { agents?: Agent[]; events?: LedgerEvent[]; now: Date }) {
  const roster = agents ?? [];
  const [statusFilter, setStatusFilter] = useState<"all" | Agent["status"]>("all");
  const [roleFilter, setRoleFilter] = useState<"all" | string>("all");
  const [selected, setSelected] = useState<string | null>(null);

  const roles = useMemo(() => Array.from(new Set(roster.map((agent) => agent.role))), [roster]);
  const filteredAgents = useMemo(
    () =>
      roster.filter((agent) => {
        const matchesStatus = statusFilter === "all" || agent.status === statusFilter;
        const matchesRole = roleFilter === "all" || agent.role === roleFilter;
        return matchesStatus && matchesRole;
      }),
    [roleFilter, roster, statusFilter],
  );

  useEffect(() => {
    if (!filteredAgents.length) {
      setSelected(null);
      return;
    }
    if (!selected || !filteredAgents.some((agent) => agent.name === selected)) {
      setSelected(filteredAgents[0].name);
    }
  }, [filteredAgents, selected]);

  const positions = [
    { x: 14, y: 26 },
    { x: 36, y: 14 },
    { x: 63, y: 20 },
    { x: 80, y: 42 },
    { x: 62, y: 70 },
    { x: 28, y: 72 },
  ];

  const nodes = filteredAgents.slice(0, 6).map((agent, index) => ({ ...agent, x: positions[index]?.x ?? 50, y: positions[index]?.y ?? 50 }));
  type LinkState = "accepted" | "pending" | "active" | "declined";
  const links = [
    { from: nodes[0]?.name, to: nodes[1]?.name, state: "accepted" as LinkState },
    { from: nodes[1]?.name, to: nodes[2]?.name, state: "pending" as LinkState },
    { from: nodes[2]?.name, to: nodes[3]?.name, state: "accepted" as LinkState },
    { from: nodes[3]?.name, to: nodes[4]?.name, state: "active" as LinkState },
    { from: nodes[4]?.name, to: nodes[5]?.name, state: "pending" as LinkState },
    { from: nodes[5]?.name, to: nodes[0]?.name, state: "active" as LinkState },
  ].filter((link) => link.from && link.to);

  const selectedNode = nodes.find((node) => node.name === selected) ?? nodes[0] ?? null;
  const recentSelectedEvents = selectedNode
    ? (events ?? []).filter((event) => event.agent === selectedNode.name).slice(0, 3)
    : [];

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-center gap-2">
        <span className="text-sm font-medium text-muted-foreground">Status:</span>
        {(["all", "active", "idle", "silent"] as const).map((option) => (
          <button
            type="button"
            key={option}
            onClick={() => setStatusFilter(option)}
            className={cn(
              "rounded-full border px-2.5 py-1 text-xs transition-colors",
              statusFilter === option ? "border-primary bg-primary text-primary-foreground" : "bg-muted text-muted-foreground hover:bg-accent",
            )}
          >
            {option === "all" ? "All" : option}
          </button>
        ))}
        <span className="ml-2 text-sm font-medium text-muted-foreground">Role:</span>
        {["all", ...roles].map((option) => (
          <button
            type="button"
            key={option}
            onClick={() => setRoleFilter(option)}
            className={cn(
              "rounded-full border px-2.5 py-1 text-xs transition-colors",
              roleFilter === option ? "border-primary bg-primary text-primary-foreground" : "bg-muted text-muted-foreground hover:bg-accent",
            )}
          >
            {option === "all" ? "All roles" : option}
          </button>
        ))}
      </div>

      <div className="grid gap-6 xl:grid-cols-[minmax(0,2fr)_340px]">
        <Card>
          <CardHeader>
            <CardTitle className="flex items-center gap-2">
              <Map className="size-4" aria-hidden /> Command map
            </CardTitle>
            <CardDescription>Mission topology for the active fleet.</CardDescription>
          </CardHeader>
          <CardContent>
            {!agents ? (
              <Skeleton className="h-[520px] w-full" />
            ) : (
              <div className="map-surface relative h-[520px] overflow-hidden rounded-xl border bg-[radial-gradient(circle_at_center,_rgba(110,150,255,0.20),_transparent_55%)]">
                <svg viewBox="0 0 100 100" className="absolute inset-0 h-full w-full" preserveAspectRatio="xMidYMid meet">
                  {links.map((link, index) => {
                    const from = nodes.find((n) => n.name === link.from);
                    const to = nodes.find((n) => n.name === link.to);
                    if (!from || !to) return null;
                    const isPending = link.state === "pending";
                    const color = link.state === "accepted" ? "#22c55e" : link.state === "pending" ? "#fbbf24" : link.state === "declined" ? "#ef4444" : "#60a5fa";
                    return (
                      <path
                        key={`${link.from}-${link.to}-${index}`}
                        d={`M ${from.x} ${from.y} Q ${(from.x + to.x) / 2} ${Math.min(from.y, to.y) - 18} ${to.x} ${to.y}`}
                        stroke={color}
                        strokeWidth={isPending ? 1.5 : 2}
                        strokeDasharray={isPending ? "2,2" : undefined}
                        fill="none"
                        className="map-line"
                      />
                    );
                  })}
                </svg>

                <div className="absolute left-1/2 top-1/2 flex -translate-x-1/2 -translate-y-1/2 flex-col items-center gap-2 rounded-full border border-primary/30 bg-background/80 px-4 py-3 shadow-sm backdrop-blur-sm">
                  <div className="text-[10px] uppercase tracking-[0.2em] text-muted-foreground">Goal</div>
                  <div className="text-center text-sm font-medium">Bastion control centre</div>
                  <div className="flex items-center gap-2 text-xs text-muted-foreground">
                    <span className="size-2 rounded-full bg-ok" aria-hidden /> 72% on track
                  </div>
                </div>

                {nodes.length === 0 ? (
                  <div className="absolute inset-0 flex items-center justify-center text-sm text-muted-foreground">No agents match the current filters.</div>
                ) : (
                  nodes.map((node) => (
                    <button
                      key={node.name}
                      type="button"
                      onClick={() => setSelected(node.name)}
                      className={cn(
                        "map-node absolute w-28 -translate-x-1/2 -translate-y-1/2 rounded-xl text-left",
                        selected === node.name && "ring-2 ring-primary/60",
                      )}
                      style={{ left: `${node.x}%`, top: `${node.y}%` }}
                    >
                      <div className="rounded-xl border border-border bg-card/90 p-2 shadow-sm backdrop-blur-sm">
                        <div className="flex items-center justify-between gap-2 text-[11px] text-muted-foreground">
                          <span className="truncate">{node.role}</span>
                          <span className={cn("size-2.5 rounded-full", STATUS_DOT[node.status])} aria-label={node.status} title={node.status} />
                        </div>
                        <div className="mt-1 truncate text-sm font-medium">{node.name}</div>
                        <div className="mt-2 flex items-center justify-between text-[10px] text-muted-foreground">
                          <span>last seen</span>
                          <span>{timeAgo(node.last_seen, now)}</span>
                        </div>
                      </div>
                    </button>
                  ))
                )}
              </div>
            )}
          </CardContent>
        </Card>

        <div className="space-y-4">
          <Card>
            <CardHeader>
              <CardTitle className="text-base">Map legend</CardTitle>
            </CardHeader>
            <CardContent className="space-y-3 text-sm">
              <div className="flex items-center gap-2"><span className="h-0.5 w-8 rounded-full bg-emerald-500" /> Solid: active coordination</div>
              <div className="flex items-center gap-2"><span className="h-0.5 w-8 rounded-full bg-amber-400" style={{ strokeDasharray: "4 4" }} /> Dotted: assistance pending</div>
              <div className="flex items-center gap-2"><span className="h-0.5 w-8 rounded-full bg-red-500" /> Red: declined / blocked</div>
              <div className="flex items-center gap-2"><span className="h-0.5 w-8 rounded-full bg-sky-500" /> Blue: owned task dependency</div>
            </CardContent>
          </Card>

          {selectedNode && (
            <Card>
              <CardHeader>
                <CardTitle className="text-base">Selected node</CardTitle>
              </CardHeader>
              <CardContent className="space-y-3 text-sm">
                <div className="flex items-center justify-between gap-2">
                  <span className="font-medium">{selectedNode.name}</span>
                  <Badge variant="secondary">{selectedNode.role}</Badge>
                </div>
                <div className="flex items-center justify-between text-muted-foreground">
                  <span>Status</span>
                  <span className="flex items-center gap-2">
                    <span className={cn("size-2 rounded-full", STATUS_DOT[selectedNode.status])} aria-hidden />
                    {selectedNode.status}
                  </span>
                </div>
                <div className="flex items-center justify-between text-muted-foreground">
                  <span>Runs on</span>
                  <span>{selectedNode.runs_on}</span>
                </div>
                <div className="flex items-center justify-between text-muted-foreground">
                  <span>Last seen</span>
                  <span>{timeAgo(selectedNode.last_seen, now)}</span>
                </div>
                {recentSelectedEvents.length > 0 && (
                  <div className="space-y-2 pt-2">
                    <div className="text-xs uppercase tracking-[0.2em] text-muted-foreground">Recent activity</div>
                    {recentSelectedEvents.map((event) => (
                      <div key={event.id} className="rounded-md border p-2">
                        <div className="flex items-center justify-between gap-2">
                          <span className="font-medium">{event.kind}</span>
                          <time className="text-[10px] text-muted-foreground">{timeAgo(event.ts, now)}</time>
                        </div>
                        <div className="mt-1 text-muted-foreground">{event.summary}</div>
                      </div>
                    ))}
                  </div>
                )}
              </CardContent>
            </Card>
          )}

          <Card>
            <CardHeader>
              <CardTitle className="text-base">Recent mission events</CardTitle>
            </CardHeader>
            <CardContent>
              {!(events ?? []).length ? (
                <p className="text-sm text-muted-foreground">No recent events.</p>
              ) : (
                <ul className="space-y-3 text-sm">
                  {(events ?? []).slice(0, 4).map((event) => (
                    <li key={event.id} className="rounded-md border p-2">
                      <div className="flex items-center justify-between gap-2">
                        <span className="font-medium">{event.agent}</span>
                        <Badge variant="outline" className={cn("font-mono text-[10px]", KIND_STYLE[event.kind] ?? "border-muted text-muted-foreground")}>{event.kind}</Badge>
                      </div>
                      <div className="mt-1 text-muted-foreground">{event.summary}</div>
                      <time className="mt-1 block text-[11px] text-muted-foreground" dateTime={event.ts}>{timeAgo(event.ts, now)}</time>
                    </li>
                  ))}
                </ul>
              )}
            </CardContent>
          </Card>
        </div>
      </div>
    </div>
  );
}

function RosterCard({ agents, now, compact, className }: { agents?: Agent[]; now: Date; compact?: boolean; className?: string }) {
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
