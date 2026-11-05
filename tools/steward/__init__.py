"""Steward: triage every open PR across the fleet and report it on the UniSync ledger.

The steward is a `watcher` on the ledger: it observes, reports and escalates, and never executes.
It is read-only on GitHub (trust level R0): it never merges, closes or comments. Each run it

1. lists every open PR under the owner (GitHub search), then reads each PR, its checks and statuses;
2. sorts each PR into one bucket by what it needs next (see BUCKETS) and adds flags (stale, duplicate);
3. writes a Markdown report (for the job summary) and, with ``--post``, posts one ``heartbeat`` with the
   whole triage plus one ``escalate`` per PR that needs the owner. An escalation's task id names the PR
   and its head commit, so the same PR is escalated again only after it changes.

    python -m tools.steward scan --owner jonnysteedman683-wq --summary out.md --json out.json [--post]

Environment: ``STEWARD_GITHUB_TOKEN`` (secret; read-only fine-grained token over the owner's repos:
Pull requests, Checks, Commit statuses, Metadata), ``UNISYNC_GATEWAY_URL`` (not secret) and
``UNISYNC_STEWARD_TOKEN`` (secret; posts only ``heartbeat`` and ``escalate`` as ``steward``).
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
from collections import Counter, defaultdict
from collections.abc import Callable, Iterable, Sequence
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, TextIO

AGENT = "steward"
FLOW = "pr-steward"
API = "https://api.github.com"
GITHUB_TOKEN_ENV = "STEWARD_GITHUB_TOKEN"  # noqa: S105 - env var name, not a secret
GATEWAY_ENV = "UNISYNC_GATEWAY_URL"
LEDGER_TOKEN_ENV = "UNISYNC_STEWARD_TOKEN"  # noqa: S105 - env var name, not a secret
STALE_AFTER = timedelta(days=30)
FLOOD_AT = 5  # open PRs in one repo before the report calls it a flood
MAX_ESCALATIONS = 10  # per run, so a bad day cannot flood the ledger
MAX_ITEMS = 60  # PRs carried in the heartbeat body
BOTS = frozenset({"dependabot[bot]", "renovate[bot]"})

# Bucket -> what the owner should do. Order is the report's order: most urgent first.
BUCKETS: dict[str, str] = {
    "merge-ready": "Green, mergeable dependency bump within one major version: safe to merge",
    "decide": "Needs an owner decision (major dependency bump)",
    "ci-red": "A check fails on this PR and not on its base branch",
    "conflict": "Conflicts with its base branch",
    "review": "Green and mergeable: needs a review",
    "no-ci": "Mergeable but has no checks, so nothing verifies it",
    "ci-pending": "Checks still running",
    "draft": "Draft",
}
ESCALATE_BUCKETS = frozenset({"decide", "ci-red", "conflict"})
ESCALATE_FLAGS = frozenset({"stale", "duplicate"})

PASS = frozenset({"success", "neutral", "skipped"})
FAIL = frozenset({"failure", "timed_out", "cancelled", "action_required", "startup_failure", "stale", "error"})
ITEM_ID_RE = re.compile(r"(?<![A-Za-z0-9])([A-Z]{1,3}\d{1,3}[a-z]?)(?![A-Za-z0-9])")
BUMP_RE = re.compile(r"\bfrom v?(\d+)\.\S* to v?(\d+)\.", re.IGNORECASE)

Get = Callable[[str], Any]


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
    draft: bool
    mergeable_state: str
    updated_at: str
    checks: dict[str, str] = field(default_factory=dict)  # name -> pass | fail | pending
    inherited: list[str] = field(default_factory=list)  # failing checks that also fail on the base branch
    bucket: str = ""
    flags: list[str] = field(default_factory=list)
    note: str = ""

    @property
    def ref(self) -> str:
        return f"{self.repo.split('/', 1)[1]} #{self.number}"

    @property
    def task_id(self) -> str:
        return f"{AGENT}/{self.repo.split('/', 1)[1]}#{self.number}@{self.head_sha[:7]}"


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
                "User-Agent": "bastion-steward",
            },
        )
        with urllib.request.urlopen(req, timeout=20) as resp:  # noqa: S310 - fixed https base
            return json.loads(resp.read())

    return get


def _state_of_run(run: dict[str, Any]) -> str:
    if run.get("status") != "completed":
        return "pending"
    conclusion = str(run.get("conclusion") or "")
    return "pass" if conclusion in PASS else "fail" if conclusion in FAIL else "pending"


def _state_of_status(status: dict[str, Any]) -> str:
    state = str(status.get("state") or "")
    return "pass" if state == "success" else "fail" if state in ("failure", "error") else "pending"


def read_checks(get: Get, repo: str, ref: str) -> dict[str, str]:
    """Every check run and legacy commit status on a commit, by name."""
    quoted = urllib.parse.quote(ref, safe="")
    out: dict[str, str] = {}
    runs = get(f"/repos/{repo}/commits/{quoted}/check-runs?per_page=100").get("check_runs", [])
    for run in runs:
        out[str(run["name"])] = _state_of_run(run)
    for status in get(f"/repos/{repo}/commits/{quoted}/status").get("statuses", []):
        out[str(status["context"])] = _state_of_status(status)
    return out


def search_open_prs(get: Get, owner: str) -> list[tuple[str, int]]:
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


def read_pr(get: Get, repo: str, number: int) -> PR:
    raw = get(f"/repos/{repo}/pulls/{number}")
    pr = PR(
        repo=repo,
        number=number,
        title=str(raw["title"]),
        url=str(raw["html_url"]),
        author=str((raw.get("user") or {}).get("login", "")),
        branch=str(raw["head"]["ref"]),
        base=str(raw["base"]["ref"]),
        head_sha=str(raw["head"]["sha"]),
        draft=bool(raw.get("draft")),
        mergeable_state=str(raw.get("mergeable_state") or "unknown"),
        updated_at=str(raw["updated_at"]),
    )
    pr.checks = read_checks(get, repo, pr.head_sha)
    failing = sorted(name for name, state in pr.checks.items() if state == "fail")
    if failing:
        base = read_checks(get, repo, pr.base)
        pr.inherited = [name for name in failing if base.get(name) == "fail"]
    return pr


# --- Triage -----------------------------------------------------------------------------------


def _major_bump(title: str) -> bool | None:
    """True for a major-version bump, False within one major, None when the title doesn't say."""
    m = BUMP_RE.search(title)
    return None if not m else int(m.group(2)) > int(m.group(1))


def classify(pr: PR, now: datetime) -> None:
    """Set pr.bucket and pr.note from its state. Flags are added by triage()."""
    own_failures = sorted(n for n, s in pr.checks.items() if s == "fail" and n not in pr.inherited)
    pending = any(s == "pending" for s in pr.checks.values())
    updated = datetime.fromisoformat(pr.updated_at.replace("Z", "+00:00"))
    if now - updated > STALE_AFTER:
        pr.flags.append("stale")
    if pr.draft:
        pr.bucket, pr.note = "draft", ""
    elif pr.mergeable_state == "dirty":
        pr.bucket, pr.note = "conflict", f"merge {pr.base} into the branch or close it"
    elif own_failures:
        pr.bucket, pr.note = "ci-red", "failing: " + ", ".join(own_failures)
    elif pending:
        pr.bucket, pr.note = "ci-pending", ""
    elif not pr.checks:
        pr.bucket, pr.note = "no-ci", "no checks ran"
    elif pr.author in BOTS:
        major = _major_bump(pr.title)
        if major:
            pr.bucket, pr.note = "decide", "major version bump: test before merging"
        elif major is False and pr.mergeable_state in ("clean", "unstable", "has_hooks"):
            pr.bucket, pr.note = "merge-ready", ""
        else:
            pr.bucket, pr.note = "review", "grouped or unparsed bump: check the changelog"
    else:
        pr.bucket, pr.note = "review", ""
    if pr.inherited:
        also = "also red on " + pr.base + ": " + ", ".join(pr.inherited)
        pr.note = f"{pr.note}; {also}" if pr.note else also


def mark_duplicates(prs: Sequence[PR]) -> None:
    """Flag every older PR that names the same backlog item (e.g. L7, G3a) in its branch as another."""
    by_item: dict[tuple[str, str], list[PR]] = defaultdict(list)
    for pr in prs:
        m = ITEM_ID_RE.search(pr.branch)
        if m:
            by_item[(pr.repo, m.group(1))].append(pr)
    for group in by_item.values():
        if len(group) < 2:
            continue
        newest = max(group, key=lambda p: (p.updated_at, p.number))
        for pr in group:
            if pr is not newest:
                pr.flags.append("duplicate")
                pr.note = (pr.note + "; " if pr.note else "") + f"same item as #{newest.number}"


def triage(prs: Sequence[PR], now: datetime) -> list[PR]:
    for pr in prs:
        classify(pr, now)
    mark_duplicates(prs)
    order = list(BUCKETS)
    return sorted(prs, key=lambda p: (order.index(p.bucket), p.repo.lower(), p.number))


def floods(prs: Iterable[PR]) -> dict[str, int]:
    counts = Counter(pr.repo for pr in prs)
    return {repo: n for repo, n in sorted(counts.items()) if n >= FLOOD_AT}


def needs_owner(pr: PR) -> bool:
    return pr.bucket in ESCALATE_BUCKETS or bool(ESCALATE_FLAGS & set(pr.flags))


def summary_line(prs: Sequence[PR]) -> str:
    counts = Counter(p.bucket for p in prs)
    parts = [f"{counts[b]} {b}" for b in BUCKETS if counts[b]]
    return f"{len(prs)} open PRs" + (": " + ", ".join(parts) if parts else "")


# --- Output -----------------------------------------------------------------------------------


def markdown(prs: Sequence[PR], now: datetime) -> str:
    lines = [f"### PR steward: {summary_line(prs)}", "", f"_Scanned {now:%Y-%m-%d %H:%M} UTC. Read-only._", ""]
    for repo, n in floods(prs).items():
        lines.append(f"> **Flood:** {repo} has {n} open PRs. Check the agent schedule that opens them.")
    for bucket, meaning in BUCKETS.items():
        group = [p for p in prs if p.bucket == bucket]
        if not group:
            continue
        lines += [
            "",
            f"#### {bucket} ({len(group)}): {meaning}",
            "",
            "| PR | Title | Flags | Note |",
            "|---|---|---|---|",
        ]
        for p in group:
            title = p.title.replace("|", "\\|")[:80]
            lines.append(f"| [{p.ref}]({p.url}) | {title} | {' '.join(p.flags)} | {p.note.replace('|', '/')} |")
    return "\n".join(lines) + "\n"


def heartbeat_body(prs: Sequence[PR], run_url: str) -> dict[str, Any]:
    items = [
        {"pr": p.ref, "url": p.url, "bucket": p.bucket, "flags": p.flags, "note": p.note[:160]} for p in prs[:MAX_ITEMS]
    ]
    return {
        "summary": summary_line(prs),
        "flow": FLOW,
        "counts": dict(Counter(p.bucket for p in prs)),
        "floods": floods(prs),
        "items": items,
        "truncated": max(0, len(prs) - MAX_ITEMS),
        "run_url": run_url,
    }


def escalation(pr: PR, run_url: str) -> dict[str, Any]:
    reasons = [pr.bucket] if pr.bucket in ESCALATE_BUCKETS else []
    reasons += [f for f in pr.flags if f in ESCALATE_FLAGS]
    why = ", ".join(reasons)
    reason = f"{pr.ref} needs you ({why}): {pr.note or pr.title}"[:200]
    return {"reason": reason, "summary": reason, "flow": FLOW, "url": pr.url, "run_url": run_url}


def post(ledger: Any, prs: Sequence[PR], run_url: str) -> tuple[int, int]:
    """Post the heartbeat and any new escalations. Returns (escalated, skipped as already escalated)."""
    ledger.sync()
    view = getattr(ledger, "ledger", ledger)
    seen = {e.task_id for e in view.entries if e.sender == AGENT and getattr(e.kind, "value", e.kind) == "escalate"}
    escalated = skipped = 0
    for pr in prs:
        if not needs_owner(pr):
            continue
        if pr.task_id in seen:
            skipped += 1
            continue
        if escalated >= MAX_ESCALATIONS:
            break
        ledger.post(AGENT, "escalate", pr.task_id, **escalation(pr, run_url))
        escalated += 1
    ledger.post(AGENT, "heartbeat", "", **heartbeat_body(prs, run_url))
    return escalated, skipped


def open_gateway_ledger() -> Any:
    """Lestinxor's SupabaseLedger on the UniSync gateway, knowing the steward as a watcher."""
    from lestinxor.unisync import DEFAULT_AGENTS, AgentCard, Role  # type: ignore[import-not-found]
    from lestinxor.unisync_supabase import SupabaseLedger  # type: ignore[import-not-found]

    agents = (*DEFAULT_AGENTS, AgentCard(AGENT, Role.WATCHER))
    return SupabaseLedger(url=os.environ[GATEWAY_ENV], key=os.environ[LEDGER_TOKEN_ENV], agents=agents)


# --- CLI --------------------------------------------------------------------------------------


def scan(get: Get, owner: str, now: datetime) -> list[PR]:
    return triage([read_pr(get, repo, n) for repo, n in search_open_prs(get, owner)], now)


def main(
    argv: Sequence[str] | None = None,
    *,
    env: dict[str, str] | None = None,
    make_get: Callable[[str], Get] = github_get,
    open_ledger: Callable[[], Any] = open_gateway_ledger,
    now: datetime | None = None,
    out: TextIO = sys.stdout,
) -> int:
    parser = argparse.ArgumentParser(prog="python -m tools.steward", description=(__doc__ or "").splitlines()[0])
    sub = parser.add_subparsers(dest="cmd", required=True)
    sc = sub.add_parser("scan", help="triage every open PR; read-only on GitHub")
    sc.add_argument("--owner", required=True, help="GitHub user or organization whose repos to scan")
    sc.add_argument("--summary", type=Path, help="write the Markdown report here (e.g. $GITHUB_STEP_SUMMARY)")
    sc.add_argument("--json", type=Path, help="write the triage as JSON here")
    sc.add_argument("--post", action="store_true", help="post the heartbeat and escalations to the ledger")
    sc.add_argument("--run-url", default="")
    args = parser.parse_args(argv)
    env = dict(os.environ) if env is None else env
    now = now or datetime.now(UTC)

    token = env.get(GITHUB_TOKEN_ENV, "")
    if not token:
        print(f"::error::Set {GITHUB_TOKEN_ENV} to a read-only GitHub token.", file=out)
        return 2
    try:
        prs = scan(make_get(token), args.owner, now)
    except urllib.error.HTTPError as exc:
        print(f"::error::GitHub API {exc.code} on {exc.url}: check {GITHUB_TOKEN_ENV}'s access", file=out)
        return 1

    report = markdown(prs, now)
    if args.summary:
        with args.summary.open("a", encoding="utf-8") as fh:
            fh.write(report)
    else:
        print(report, file=out)
    if args.json:
        args.json.write_text(json.dumps([asdict(p) for p in prs], indent=2) + "\n", encoding="utf-8")
    if args.post:
        escalated, skipped = post(open_ledger(), prs, args.run_url)
        print(f"posted heartbeat; {escalated} escalated, {skipped} already escalated", file=out)
    print(summary_line(prs), file=out)
    return 0
