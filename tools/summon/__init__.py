"""Summon: ask an agent to run a task through the UniSync ledger, the way LinX does.

The orchestrator (``lestinxor``) posts the ``task.request`` with ``flow: bastion-summon``, as LinX's
nightly loop does, so ``linx log``, ``linx brief`` and the command centre all show it. Posting as the
orchestrator, not as ``jonny``, keeps owner entries for the owner's own signed key.

The agent then posts ``task.claim``, runs, and posts ``task.result`` or ``task.failed``. Every post
goes through the UniSync gateway with a token scoped to one agent and its allowed kinds, using
Lestinxor's ``SupabaseLedger``, which replays the whole ledger and checks each rule before writing.

Run from the summon workflow:

    python -m tools.summon request --title T --agent sebastian --run-url URL   # prints the task id
    python -m tools.summon claim   --task-id ID --agent sebastian
    python -m tools.summon finish  --task-id ID --agent sebastian --exit-code N --log FILE --run-url URL

Environment: ``UNISYNC_GATEWAY_URL`` (not secret), ``UNISYNC_ORCHESTRATOR_TOKEN`` and
``UNISYNC_AGENT_TOKEN`` (GitHub secrets; only their sha256 is stored in the ledger).
"""

from __future__ import annotations

import argparse
import hashlib
import os
import re
import sys
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any, TextIO

ORCHESTRATOR = "lestinxor"
OWNER = "jonny"
FLOW = "bastion-summon"
PREFIX = "bastion"
MAX_LINE = 200
GATEWAY_ENV = "UNISYNC_GATEWAY_URL"
ORCHESTRATOR_TOKEN_ENV = "UNISYNC_ORCHESTRATOR_TOKEN"  # noqa: S105 - env var name, not a secret
AGENT_TOKEN_ENV = "UNISYNC_AGENT_TOKEN"  # noqa: S105 - env var name, not a secret


def task_id(title: str, head: str) -> str:
    """LinX's task id shape (`ops.assign`): a slug of the title plus 6 hex of sha256(head + title)."""
    slug = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")[:40].strip("-") or "task"
    return f"{PREFIX}/{slug}-{hashlib.sha256((head + title).encode()).hexdigest()[:6]}"


def request(ledger: Any, title: str, *, agent: str, run_url: str) -> str:
    """Post the task.request as the orchestrator. Returns the task id."""
    title = title.strip()[:MAX_LINE]
    if not title:
        raise ValueError("a summon needs a title")
    ledger.sync()
    view = getattr(ledger, "ledger", ledger)
    head = view.entries[-1].digest if view.entries else ""
    tid = task_id(title, head)
    ledger.post(
        ORCHESTRATOR,
        "task.request",
        tid,
        title=title,
        flow=FLOW,
        agent=agent,
        unattended=True,
        requested_by=OWNER,
        run_url=run_url,
    )
    return tid


def _last_line(output: str) -> str:
    lines = [line.strip() for line in output.splitlines() if line.strip()]
    return lines[-1][:MAX_LINE] if lines else ""


def outcome(exit_code: int, output: str, *, run_url: str) -> tuple[str, dict[str, Any]]:
    """The kind and body that report a finished run: task.result on exit 0, else task.failed."""
    line = _last_line(output)
    if exit_code == 0:
        return "task.result", {"summary": line or "finished with no output", "exit_code": 0, "run_url": run_url}
    reason = f"exit {exit_code}: {line or 'no output'}"[:MAX_LINE]
    return "task.failed", {"reason": reason, "exit_code": exit_code, "run_url": run_url}


def open_gateway_ledger(token_env: str) -> Any:
    """Lestinxor's SupabaseLedger pointed at the UniSync gateway with one agent's token."""
    from lestinxor.unisync_supabase import SupabaseLedger  # type: ignore[import-not-found]  # installed by the workflow

    return SupabaseLedger(url=os.environ[GATEWAY_ENV], key=os.environ[token_env])


def main(
    argv: Sequence[str] | None = None,
    *,
    open_ledger: Callable[[str], Any] = open_gateway_ledger,
    out: TextIO = sys.stdout,
) -> int:
    parser = argparse.ArgumentParser(prog="python -m tools.summon", description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="cmd", required=True)
    req = sub.add_parser("request", help="orchestrator posts task.request; prints the task id")
    req.add_argument("--title", required=True)
    req.add_argument("--agent", required=True)
    req.add_argument("--run-url", default="")
    claim = sub.add_parser("claim", help="the agent claims the task")
    claim.add_argument("--task-id", required=True)
    claim.add_argument("--agent", required=True)
    fin = sub.add_parser("finish", help="the agent posts task.result or task.failed")
    fin.add_argument("--task-id", required=True)
    fin.add_argument("--agent", required=True)
    fin.add_argument("--exit-code", type=int, required=True)
    fin.add_argument("--log", type=Path, required=True)
    fin.add_argument("--run-url", default="")
    args = parser.parse_args(argv)

    if args.cmd == "request":
        tid = request(open_ledger(ORCHESTRATOR_TOKEN_ENV), args.title, agent=args.agent, run_url=args.run_url)
        print(tid, file=out)
    elif args.cmd == "claim":
        open_ledger(AGENT_TOKEN_ENV).post(args.agent, "task.claim", args.task_id)
        print(f"claimed {args.task_id}", file=out)
    else:
        output = args.log.read_text(encoding="utf-8", errors="replace") if args.log.is_file() else ""
        kind, body = outcome(args.exit_code, output, run_url=args.run_url)
        open_ledger(AGENT_TOKEN_ENV).post(args.agent, kind, args.task_id, **body)
        print(f"{kind} {args.task_id}", file=out)
    return 0
