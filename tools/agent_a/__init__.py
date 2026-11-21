"""Agent-A: the fleet sentry. It patrols the perimeter and reports danger before it lands.

Agent-A is a `watcher` on the ledger like the steward: it observes, reports and escalates, and never
executes (trust level R0). Its defense is proactive detection: each run it

1. patrols every open PR under the owner (GitHub search) for two kinds of danger:
   a **secret** an added line would leak into git history, or a **guarded-path** edit, the kind a
   rogue or runaway PR makes to escalate itself (workflows, deploy policy, infra, quartermaster);
2. with ``--post``, watches the ledger for an agent **stuck in a streak** of failed outcomes and
   adds that to the report;
3. posts one ``heartbeat`` with the whole patrol plus one ``escalate`` per PR that needs the owner
   (deduped by head commit, capped), and pushes a phone alert for critical findings.

A finding reports where the danger is, never the dangerous value itself.

    python -m tools.agent_a patrol --owner jonnysteedman683-wq --summary out.md --json out.json [--post] [--notify]

Environment: ``AGENT_A_GITHUB_TOKEN`` (secret; read-only fine-grained token: Pull requests,
Metadata), ``UNISYNC_GATEWAY_URL`` (not secret), ``UNISYNC_AGENT_A_TOKEN`` (secret; posts only
``heartbeat`` and ``escalate`` as ``agent-a``), and for ``--notify`` the ``NTFY_*`` variables of
``tools.notify``.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from collections import Counter
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, TextIO

AGENT = "agent-a"
FLOW = "sentry"
API = "https://api.github.com"
GITHUB_TOKEN_ENV = "AGENT_A_GITHUB_TOKEN"  # noqa: S105 - env var name, not a secret
GATEWAY_ENV = "UNISYNC_GATEWAY_URL"
LEDGER_TOKEN_ENV = "UNISYNC_AGENT_A_TOKEN"  # noqa: S105 - env var name, not a secret
MAX_FILES = 100  # per PR, one page: a PR touching more than that is already a finding
MAX_LEDGER = 200  # trailing entries watched for failure streaks
MAX_ESCALATIONS = 10  # per run, so a bad day cannot flood the ledger
MAX_ITEMS = 60  # findings carried in the heartbeat body
FAILURE_STREAK = 3  # failed outcomes in a row before an agent is called stuck
OUTCOME_FAIL = frozenset({"task.failed", "run.failed"})
OUTCOME_OK = frozenset({"task.result"})

# The Never list of AGENTS.md, the paths no agent PR may touch. Guarded entries ending
# in "/" are directory prefixes; the others match a file name exactly.
GUARDED: tuple[str, ...] = (
    ".github/",
    "deploy/policy.json",
    "infra/",
    "tools/quartermaster/",
    "AGENTS.md",
    "CLAUDE.md",
)

# (name, severity, pattern). A match names the pattern and its location; the matched text is never
# included in a report, so a finding cannot become the leak it caught.
SECRETS: tuple[tuple[str, str, re.Pattern[str]], ...] = (
    ("aws-access-key", "critical", re.compile(r"AKIA[0-9A-Z]{16}")),
    ("github-token", "critical", re.compile(r"gh[pousr]_[A-Za-z0-9]{20,}")),
    ("github-fine-grained-token", "critical", re.compile(r"github_pat_[A-Za-z0-9_]{22,}")),
    ("model-provider-key", "critical", re.compile(r"sk-[A-Za-z0-9_-]{20,}")),
    ("slack-token", "critical", re.compile(r"xox[bpars]-[A-Za-z0-9-]{10,}")),
    ("google-api-key", "critical", re.compile(r"AIza[0-9A-Za-z_-]{35}")),
    ("private-key", "critical", re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----")),
    (
        "credential-literal",
        "high",
        re.compile(r"(?i)(?:api[_-]?key|secret|token|password|passwd)\s*[:=]\s*[\"'][^\"'\s]{16,}[\"']"),
    ),
)

SEVERITY = {"critical": 0, "high": 1, "medium": 2}
Get = Callable[[str], Any]
Send = Callable[..., int]


def is_guarded(path: str) -> bool:
    """True when a PR file touches a path on AGENTS.md's Never list."""
    return any(path == entry or path.startswith(entry) or path.endswith("/" + entry) for entry in GUARDED)


def guarded_by(path: str) -> str:
    """Which Never entry a guarded path hits."""
    return next(entry for entry in GUARDED if path == entry or path.startswith(entry) or path.endswith("/" + entry))


# --- GitHub -----------------------------------------------------------------------------------


def github_get(token: str) -> Get:
    """A GET for the GitHub REST API that returns parsed JSON."""

    def get(path: str) -> Any:
        req = urllib.request.Request(  # noqa: S310 - fixed https base
            API + path,
            headers={
                "Accept": "application/vnd.github+json",
                "Authorization": f"Bearer {token}",
                "X-GitHub-Api-Version": "2022-11-28",
                "User-Agent": "bastion-agent-a",
            },
        )
        with urllib.request.urlopen(req, timeout=20) as resp:  # noqa: S310 - fixed https base
            return json.loads(resp.read())

    return get


def open_prs(get: Get, owner: str) -> list[tuple[str, int]]:
    """Every open PR under the owner, as (repo, number), same shape as the steward's search."""
    q = urllib.parse.quote(f"is:pr is:open user:{owner}")
    found: list[tuple[str, int]] = []
    page = 1
    while True:
        data = get(f"/search/issues?q={q}&per_page=100&page={page}")
        items = data.get("items", [])
        for item in items:
            repo = "/".join(str(item["repository_url"]).rsplit("/", 2)[-2:])
            found.append((repo, int(item["number"])))
        if len(items) < 100 or len(found) >= int(data.get("total_count", 0)):
            return found
        page += 1


def read_pr(get: Get, repo: str, number: int) -> dict[str, Any]:
    """The PR's raw metadata."""
    raw: dict[str, Any] = get(f"/repos/{repo}/pulls/{number}")
    return raw


def read_files(get: Get, repo: str, number: int) -> tuple[str, list[dict[str, Any]]]:
    """The PR's base branch and its changed files (patch text where GitHub provides one)."""
    raw = read_pr(get, repo, number)
    files: list[dict[str, Any]] = []
    page = 1
    while True:
        data = get(f"/repos/{repo}/pulls/{number}/files?per_page={MAX_FILES}&page={page}")
        files += data
        if len(data) < MAX_FILES or len(files) >= MAX_FILES:
            return str(raw["base"]["ref"]), files
        page += 1


# --- Findings ----------------------------------------------------------------------------------


@dataclass
class Finding:
    kind: str  # secret | guarded
    severity: str  # critical | high | medium
    path: str
    line: int
    hint: str


@dataclass
class PR:
    repo: str  # owner/name
    number: int
    title: str
    url: str
    author: str
    branch: str
    base: str
    head_sha: str
    updated_at: str
    files: list[str] = field(default_factory=list)
    findings: list[Finding] = field(default_factory=list)

    @property
    def ref(self) -> str:
        return f"{self.repo.split('/', 1)[1]} #{self.number}"

    @property
    def task_id(self) -> str:
        return f"{AGENT}/{self.repo.split('/', 1)[1]}#{self.number}@{self.head_sha[:7]}"

    @property
    def worst(self) -> str:
        ordered = sorted((f.severity for f in self.findings), key=lambda s: SEVERITY[s])
        return ordered[0] if ordered else ""


def _line_of(patch_lines: Sequence[str], index: int) -> int:
    """The new-file line number of patch_lines[index], from the hunk header."""
    next_new = 1
    for line in patch_lines[:index]:
        if line.startswith("@@"):
            head = line.split("+", 1)[-1].split(" ", 1)[0].split(",", 1)[0] if "+" in line else ""
            next_new = int(head) if head.isdigit() else next_new
        elif not line.startswith("-") and not line.startswith("++"):
            next_new += 1
    return next_new


def inspect_file(pr: PR, filename: str, patch: str) -> None:
    """Add findings for one changed file: guarded paths, then secrets on the lines the PR adds."""
    if is_guarded(filename):
        pr.findings.append(Finding("guarded", "high", filename, 0, f"touches the Never list: {guarded_by(filename)}"))
    lines = patch.splitlines()
    for index, line in enumerate(lines):
        if not line.startswith("+") or line.startswith("+++"):
            continue
        content = line[1:]
        for name, severity, pattern in SECRETS:
            m = pattern.search(content)
            if m:
                pr.findings.append(
                    Finding(
                        "secret",
                        severity,
                        filename,
                        _line_of(lines, index),
                        f"matches {name}; rotate the credential and report only its location",
                    )
                )
                break


def scan(get: Get, owner: str) -> list[PR]:
    """Patrol every open PR under the owner, worst findings first."""
    prs: list[PR] = []
    for repo, number in open_prs(get, owner):
        try:
            raw = read_pr(get, repo, number)
            base, files = read_files(get, repo, number)
        except urllib.error.HTTPError:
            continue  # a PR vanishing between search and read is not an incident
        pr = PR(
            repo=repo,
            number=number,
            title=str(raw["title"]),
            url=str(raw["html_url"]),
            author=str((raw.get("user") or {}).get("login", "")),
            branch=str(raw["head"]["ref"]),
            base=base,
            head_sha=str(raw["head"]["sha"]),
            updated_at=str(raw["updated_at"]),
        )
        for item in files[:MAX_FILES]:
            filename = str(item.get("filename") or "")
            pr.files.append(filename)
            patch = str(item.get("patch") or "")
            if patch:
                inspect_file(pr, filename, patch)
        prs.append(pr)
    return sorted(prs, key=lambda p: (SEVERITY[p.worst] if p.worst else len(SEVERITY), p.repo.lower(), p.number))


# --- Report -----------------------------------------------------------------------------------


def summary_line(prs: Sequence[PR]) -> str:
    ris = [f for p in prs for f in p.findings]
    if not ris:
        return "perimeter clean"
    counts = Counter(r.severity for r in ris)
    parts = " ".join(f"{counts[s]} {s}" for s in SEVERITY if counts[s])
    return f"{len(ris)} findings on {len([p for p in prs if p.findings])} PRs: {parts}"


def markdown(prs: Sequence[PR], streaks: Sequence[str], now: datetime) -> str:
    lines = [
        f"### Agent-A sentry: {summary_line(prs)}",
        "",
        f"_Scanned {now:%Y-%m-%d %H:%M} UTC. Read-only. Findings name locations, never leaked values._",
        "",
    ]
    if streaks:
        lines += ["- " + s for s in streaks] + [""]
    risky = [p for p in prs if p.findings]
    if risky:
        lines += ["| PR | Kind | Location | Note |", "|---|---|---|---|"]
        for p in risky:
            for f in p.findings:
                where = f"{f.path}:{f.line}" if f.line else f.path
                lines.append(f"| [{p.ref}]({p.url}) | {f.kind}/{f.severity} | {where} | {f.hint} |")
        clean = len(prs) - len(risky)
        if clean:
            lines += ["", f"{clean} open PR(s) clean."]
    return "\n".join(lines) + "\n"


# --- Ledger watch -------------------------------------------------------------------------------


def count_streaks(entries: Iterable[Any]) -> dict[str, int]:
    """Trailing failed outcomes per agent, from ledger-ordered entries."""
    counts: dict[str, int] = {}
    for entry in entries:
        sender = str(getattr(entry, "sender", "") or "")
        kind = getattr(getattr(entry, "kind", None), "value", getattr(entry, "kind", None))
        if not sender:
            continue
        if kind in OUTCOME_FAIL:
            counts[sender] = counts.get(sender, 0) + 1
        elif kind in OUTCOME_OK:
            counts[sender] = 0
    return counts


def streak_findings(entries: Iterable[Any], now: datetime) -> list[tuple[str, dict[str, Any]]]:
    """(task_id, escalate body) per agent stuck in a failure streak, one per agent per UTC day."""
    out: list[tuple[str, dict[str, Any]]] = []
    for sender, n in sorted(count_streaks(entries).items()):
        if n < FAILURE_STREAK:
            continue
        task_id = f"{AGENT}/streak/{sender}@{now:%Y%m%d}"
        reason = f"{sender} failed {n} outcomes in a row since its last result on the ledger"
        out.append((task_id, {"reason": reason[:200], "summary": reason[:200], "flow": FLOW, "run_url": ""}))
    return out


# --- Posting -----------------------------------------------------------------------------------


def heartbeat_body(prs: Sequence[PR], streaks_posted: Sequence[str], run_url: str) -> dict[str, Any]:
    findings = sorted(((p, f) for p in prs for f in p.findings), key=lambda pf: SEVERITY[pf[1].severity])
    items = [
        {
            "pr": p.ref,
            "url": p.url,
            "kind": f.kind,
            "severity": f.severity,
            "path": f.path,
            "line": f.line,
            "hint": f.hint[:160],
        }
        for p, f in findings[:MAX_ITEMS]
    ]
    return {
        "summary": summary_line(prs),
        "flow": FLOW,
        "counts": dict(Counter(f.severity for p in prs for f in p.findings)),
        "items": items,
        "truncated": max(0, len(findings) - MAX_ITEMS),
        "streaks": list(streaks_posted),
        "run_url": run_url,
    }


def post(ledger: Any, prs: Sequence[PR], now: datetime, run_url: str) -> tuple[list[Any], list[str], int]:
    """Post the heartbeat and new escalations.

    Returns (escalations posted as (task_id, body), streak warnings for the report, skipped as seen).
    """
    ledger.sync()
    view = getattr(ledger, "ledger", ledger)
    entries = list(view.entries)
    seen = {
        e.task_id
        for e in entries
        if getattr(e, "sender", "") == AGENT
        and getattr(getattr(e, "kind", None), "value", getattr(e, "kind", None)) == "escalate"
    }
    posted: list[Any] = []
    skipped = 0
    pending = [((p, f)) for p in prs for f in p.findings]
    pending.sort(key=lambda pf: SEVERITY[pf[1].severity])
    for p, _ in pending[:MAX_ESCALATIONS]:
        if p.task_id in seen:
            skipped += 1
            continue
        sev = p.worst
        kinds = ",".join(sorted({f.kind for f in p.findings}))
        why = f"sentry: {p.ref} carries {kinds} ({sev}); hold the merge"
        body = {"reason": why[:200], "summary": why[:200], "flow": FLOW, "url": p.url, "run_url": run_url}
        ledger.post(AGENT, "escalate", p.task_id, **body)
        posted.append((p.task_id, body))
    streaks: list[str] = [str(body["reason"]) for task_id, body in streak_findings(entries, now)]
    for task_id, body in streak_findings(entries, now):
        if task_id in seen or len(posted) >= MAX_ESCALATIONS:
            continue
        ledger.post(AGENT, "escalate", task_id, **body)
        posted.append((task_id, body))
    ledger.post(
        AGENT,
        "heartbeat",
        "",
        **heartbeat_body(prs, [t for t, _ in posted if t.startswith(AGENT + "/streak/")], run_url),
    )
    return posted, streaks, skipped


def open_gateway_ledger() -> Any:
    """Lestinxor's SupabaseLedger on the UniSync gateway, knowing the sentry as a watcher."""
    from lestinxor.unisync import DEFAULT_AGENTS, AgentCard, Role  # type: ignore[import-not-found]
    from lestinxor.unisync_supabase import SupabaseLedger  # type: ignore[import-not-found]

    agents = (*DEFAULT_AGENTS, AgentCard(AGENT, Role.WATCHER))
    return SupabaseLedger(url=os.environ[GATEWAY_ENV], key=os.environ[LEDGER_TOKEN_ENV], agents=agents)


# --- Notify ------------------------------------------------------------------------------------


def notify_send(
    argv: Sequence[str] | None = None, *, env: Mapping[str, str] | None = None, out: TextIO = sys.stderr
) -> int:
    """tools.notify's send, with this run's environment, so tests can inject their own."""
    from tools import notify

    return notify.main(list(argv) if argv else None, env=env)


def warn_phone(prs: Sequence[PR], env: dict[str, str], send: Send, out: TextIO) -> int:
    """Push one urgent ntfy per critical PR finding, capped at 3, naming locations only."""
    critical = 0
    for p in prs:
        for f in p.findings:
            if f.severity != "critical" or critical >= 3:
                continue
            critical += 1
            message = (
                f"Agent-A: {p.ref} would leak a {f.hint.split(';')[0]} in {f.path}:{f.line}. Hold the merge and rotate."
            )
            code = send(
                [
                    "send",
                    "--message",
                    message,
                    "--title",
                    "Agent-A: critical finding",
                    "--priority",
                    "urgent",
                    "--tags",
                    "rotating_light",
                    "--click",
                    p.url,
                ],
                env=env,
            )
            if code != 0:
                print(f"::warning::ntfy send failed (exit {code}); the escalation is still on the ledger", file=out)
    return critical


# --- CLI ---------------------------------------------------------------------------------------


def main(
    argv: Sequence[str] | None = None,
    *,
    env: dict[str, str] | None = None,
    make_get: Callable[[str], Get] = github_get,
    open_ledger: Callable[[], Any] = open_gateway_ledger,
    send: Send = notify_send,
    now: datetime | None = None,
    out: TextIO = sys.stdout,
) -> int:
    parser = argparse.ArgumentParser(prog="python -m tools.agent_a", description=(__doc__ or "").splitlines()[0])
    sub = parser.add_subparsers(dest="cmd", required=True)
    pa = sub.add_parser("patrol", help="patrol every open PR; read-only on GitHub")
    pa.add_argument("--owner", required=True, help="GitHub user or organization whose repos to patrol")
    pa.add_argument("--summary", type=Path, help="write the Markdown report here (e.g. $GITHUB_STEP_SUMMARY)")
    pa.add_argument("--json", type=Path, help="write the patrol as JSON here")
    pa.add_argument("--post", action="store_true", help="post the heartbeat and escalations to the ledger")
    pa.add_argument("--notify", action="store_true", help="push a phone alert for critical findings")
    pa.add_argument("--run-url", default="")
    args = parser.parse_args(argv)
    env = dict(os.environ) if env is None else env
    now = now or datetime.now(UTC)

    token = env.get(GITHUB_TOKEN_ENV, "")
    if not token:
        print(f"::error::Set {GITHUB_TOKEN_ENV} to a read-only GitHub token.", file=out)
        return 2
    try:
        prs = scan(make_get(token), args.owner)
    except urllib.error.HTTPError as exc:
        print(f"::error::GitHub API {exc.code} on {exc.url}: check {GITHUB_TOKEN_ENV}'s access", file=out)
        return 1

    streaks: list[str] = []
    if args.post:
        if not env.get(GATEWAY_ENV) or not env.get(LEDGER_TOKEN_ENV):
            print(f"::error::Set {GATEWAY_ENV} and {LEDGER_TOKEN_ENV} to post to the ledger.", file=out)
            return 2
        posted, streaks, _ = post(open_ledger(), prs, now, args.run_url)
        print(f"posted heartbeat; {len(posted)} escalations", file=out)
    if args.notify:
        alerted = warn_phone([p for p in prs if p.findings], env, send, out)
        print(f"{alerted} phone alert(s)", file=out)

    report = markdown(prs, streaks, now)
    if args.summary:
        with args.summary.open("a", encoding="utf-8") as fh:
            fh.write(report)
    else:
        print(report, file=out)
    if args.json:
        args.json.write_text(json.dumps([asdict(p) for p in prs], indent=2) + "\n", encoding="utf-8")
    print(summary_line(prs), file=out)
    return 0
