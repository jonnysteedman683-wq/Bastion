import { Bug, ExternalLink, Repeat } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { failuresHeadline, rateLabel, type FailureGroup, type FailuresResponse } from "@/lib/failures";
import { isSafeGithubUrl, isSafeTraceUrl, timeAgo } from "@/lib/ledger";
import { cn } from "@/lib/utils";

type Props = { data: FailuresResponse | null; error: string | null; now: Date };

export function FailuresView({ data, error, now }: Props) {
  return (
    <div className="grid gap-6 lg:grid-cols-3">
      <Card className="lg:col-span-2">
        <CardHeader>
          <CardTitle className="flex items-center gap-2">
            <Bug className="size-4" aria-hidden /> Faults
          </CardTitle>
          <CardDescription>
            {data ? failuresHeadline(data) : "Failed runs, grouped by agent and error."} The same fault three times in a
            week is marked recurring: a candidate for a scar test.
          </CardDescription>
        </CardHeader>
        <CardContent>
          {error ? (
            <p className="text-sm text-destructive">Can't load failures: {error}</p>
          ) : !data ? (
            <Skeleton className="h-48 w-full" />
          ) : data.groups.length === 0 ? (
            <p className="text-sm text-muted-foreground">Nothing failed in the last {data.window_days} days.</p>
          ) : (
            <ul className="space-y-2">
              {data.groups.map((group) => (
                <FaultRow key={`${group.agent}|${group.signature}`} group={group} now={now} />
              ))}
            </ul>
          )}
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>By agent</CardTitle>
          <CardDescription>Failure rate counts finished tasks only.</CardDescription>
        </CardHeader>
        <CardContent>
          {!data ? (
            error ? null : <Skeleton className="h-32 w-full" />
          ) : data.agents.length === 0 ? (
            <p className="text-sm text-muted-foreground">Every agent is clean.</p>
          ) : (
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Agent</TableHead>
                  <TableHead className="text-right">24h</TableHead>
                  <TableHead className="text-right">7d</TableHead>
                  <TableHead className="text-right">Rate</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {data.agents.map((a) => (
                  <TableRow key={a.agent}>
                    <TableCell className="font-medium">{a.agent}</TableCell>
                    <TableCell className={cn("text-right tabular-nums", a.last_24h > 0 && "text-destructive")}>{a.last_24h}</TableCell>
                    <TableCell className="text-right tabular-nums">{a.last_7d}</TableCell>
                    <TableCell className="text-right tabular-nums">{rateLabel(a.rate)}</TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          )}
        </CardContent>
      </Card>
    </div>
  );
}

function FaultRow({ group, now }: { group: FailureGroup; now: Date }) {
  return (
    <li className={cn("space-y-1 rounded-md border p-3 text-sm", group.recurring && "border-destructive/50")}>
      <div className="flex flex-wrap items-center gap-2">
        <Badge variant="outline">{group.agent}</Badge>
        <code className="min-w-0 flex-1 truncate text-xs" title={group.signature}>{group.signature}</code>
        {group.recurring && (
          <Badge variant="destructive" className="gap-1">
            <Repeat className="size-3" aria-hidden /> recurring
          </Badge>
        )}
        <span className="tabular-nums font-medium" aria-label={`${group.count} times`}>×{group.count}</span>
      </div>
      <p className="break-words text-muted-foreground">
        {group.example.title && <span className="text-foreground">{group.example.title}: </span>}
        {group.example.reason || "No reason given."}
      </p>
      <div className="flex flex-wrap items-center gap-3 text-xs text-muted-foreground">
        <span title={group.last_ts}>last {timeAgo(group.last_ts, now)}</span>
        {group.count > 1 && <span title={group.first_ts}>first {timeAgo(group.first_ts, now)}</span>}
        {isSafeGithubUrl(group.run_url) && (
          <a className="inline-flex items-center gap-1 underline" href={group.run_url} target="_blank" rel="noreferrer noopener">
            run <ExternalLink className="size-3" aria-hidden />
          </a>
        )}
        {isSafeTraceUrl(group.trace_url) && (
          <a className="inline-flex items-center gap-1 underline" href={group.trace_url} target="_blank" rel="noreferrer noopener">
            trace <ExternalLink className="size-3" aria-hidden />
          </a>
        )}
      </div>
    </li>
  );
}
