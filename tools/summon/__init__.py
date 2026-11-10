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
    python -m tools.summon finish  --task-id ID --agent sebastian --exit-code N --log FILE --run-url URL \
                                   [--usage USAGE.json]

``--usage`` attaches the agent's token and cost report to the outcome (backlog G1b), in the Spend view's
contract (``services/command-centre/ledger/bastion_spend.sql``). The gateway refuses non-integer numbers anywhere
in an envelope, so ``cost_usd`` from the file is sent as integer ``cost_micro_usd``. A bad usage file is a warning,
never a reason to lose the outcome.

The same file may carry ``trace_url``, the run's Langfuse trace (backlog G1c). It is posted as the outcome's
top-level ``trace_url`` only if it is an https link to a Langfuse trace page on Langfuse Cloud or on the https
``LANGFUSE_HOST``; anything else is dropped with a warning, because the command centre renders it as a link.

Environment: ``UNISYNC_GATEWAY_URL`` (not secret), ``UNISYNC_ORCHESTRATOR_TOKEN`` and
``UNISYNC_AGENT_TOKEN`` (GitHub secrets; only their sha256 is stored in the ledger).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import re
import sys
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any, TextIO
from urllib.parse import urlsplit

ORCHESTRATOR = "lestinxor"
OWNER = "jonny"
FLOW = "bastion-summon"
PREFIX = "bastion"
MAX_LINE = 200
MAX_OUTPUT = 1500  # characters of output tail kept on the ledger
MAX_OUTPUT_LINES = 25
GATEWAY_ENV = "UNISYNC_GATEWAY_URL"
ORCHESTRATOR_TOKEN_ENV = "UNISYNC_ORCHESTRATOR_TOKEN"  # noqa: S105 - env var name, not a secret
AGENT_TOKEN_ENV = "UNISYNC_AGENT_TOKEN"  # noqa: S105 - env var name, not a secret
MAX_USAGE_BYTES = 4096
MAX_MODEL = 100
MAX_TOKENS = 10**9  # matches the clamp in bastion.spend_daily
MAX_COST_MICRO_USD = 10**10  # $10,000, the view's cost_usd clamp
MAX_TRACE_URL = 500
LANGFUSE_HOST_ENV = "LANGFUSE_HOST"
LANGFUSE_CLOUD_HOSTS = frozenset(
    {"cloud.langfuse.com", "us.cloud.langfuse.com", "jp.cloud.langfuse.com", "hipaa.cloud.langfuse.com"}
)
TRACE_PATH = re.compile(r"/project/[A-Za-z0-9_-]{1,100}/traces/[A-Za-z0-9_.:-]{1,200}")


def task_id(title: str, head: str) -> str:
    """LinX's task id shape (`ops.assign`): a slug of the title plus 6 hex of sha256(head + title)."""
    slug = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")[:40].strip("-") or "task"
    return f"{PREFIX}/{slug}-{hashlib.sha256((head + title).encode()).hexdigest()[:6]}"


def request(ledger: Any, title: str, *, agent: str, run_url: str, mode: str = "") -> str:
    """Post the task.request as the orchestrator. Returns the task id."""
    title = title.strip()[:MAX_LINE]
    if not title:
        raise ValueError("a summon needs a title")
    ledger.sync()
    view = getattr(ledger, "ledger", ledger)
    head = view.entries[-1].digest if view.entries else ""
    tid = task_id(title, head)
    body: dict[str, Any] = {
        "title": title,
        "flow": FLOW,
        "agent": agent,
        "unattended": True,
        "requested_by": OWNER,
        "run_url": run_url,
    }
    if mode:
        body["mode"] = mode
    ledger.post(ORCHESTRATOR, "task.request", tid, **body)
    return tid


def _last_line(output: str) -> str:
    lines = [line.strip() for line in output.splitlines() if line.strip()]
    return lines[-1][:MAX_LINE] if lines else ""


def _tail(output: str) -> str:
    """The last lines of output, at most MAX_OUTPUT_LINES lines and MAX_OUTPUT characters."""
    lines = output.rstrip().splitlines()[-MAX_OUTPUT_LINES:]
    return "\n".join(lines)[-MAX_OUTPUT:]


def outcome(exit_code: int, output: str, *, run_url: str) -> tuple[str, dict[str, Any]]:
    """The kind and body that report a finished run: task.result on exit 0, else task.failed.

    Both carry the tail of the run's output, so the command centre can show why a run failed.
    """
    line = _last_line(output)
    tail = _tail(output)
    if exit_code == 0:
        body: dict[str, Any] = {"summary": line or "finished with no output", "exit_code": 0, "run_url": run_url}
    else:
        reason = f"exit {exit_code}: {line or 'no output'}"[:MAX_LINE]
        body = {"reason": reason, "exit_code": exit_code, "run_url": run_url}
    if tail:
        body["output"] = tail
    return ("task.result" if exit_code == 0 else "task.failed"), body


def _count(value: Any, ceiling: int) -> int | None:
    """A non-negative whole number, clamped; None for anything else (bools, strings, fractions)."""
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0:
        return None
    if isinstance(value, float) and not value.is_integer():
        return None
    return min(int(value), ceiling)


def read_report(path: Path) -> tuple[dict[str, Any] | None, str]:
    """Read the agent's usage file as a JSON object. Returns (data or None, warning or "")."""
    try:
        raw = path.read_bytes()[: MAX_USAGE_BYTES + 1]
        if len(raw) > MAX_USAGE_BYTES:
            return None, f"usage file {path} is over {MAX_USAGE_BYTES} bytes; ignored"
        data = json.loads(raw.decode("utf-8"))
    except (OSError, UnicodeDecodeError, ValueError) as exc:
        return None, f"usage file {path} not read ({type(exc).__name__}); outcome posted without usage"
    if not isinstance(data, dict):
        return None, f"usage file {path} is not a JSON object; ignored"
    return data, ""


def usage_from(data: dict[str, Any], path: Path) -> tuple[dict[str, Any] | None, str]:
    """The ledger usage contract from an agent's report. Returns (usage or None, warning or "").

    Keeps only ``model`` and integer ``input_tokens``/``output_tokens``; ``cost_usd`` becomes
    ``cost_micro_usd`` (rounded integer) because the gateway rejects floats. Agents write this file, so
    nothing in it is trusted: wrong types, negatives and unknown keys are dropped and values are clamped.
    """
    usage: dict[str, Any] = {}
    model = data.get("model")
    if isinstance(model, str) and model.strip():
        usage["model"] = model.strip()[:MAX_MODEL]
    for key in ("input_tokens", "output_tokens"):
        count = _count(data.get(key), MAX_TOKENS)
        if count is not None:
            usage[key] = count
    cost = data.get("cost_usd")
    if not isinstance(cost, bool) and isinstance(cost, (int, float)) and math.isfinite(cost) and cost >= 0:
        usage["cost_micro_usd"] = min(round(cost * 1_000_000), MAX_COST_MICRO_USD)
    if not usage:
        return None, f"usage file {path} has no usable fields; ignored"
    return usage, ""


def load_usage(path: Path) -> tuple[dict[str, Any] | None, str]:
    """Read an agent's usage file into the ledger contract. Returns (usage or None, warning or "")."""
    data, warning = read_report(path)
    return (None, warning) if data is None else usage_from(data, path)


def trace_url(value: Any, *, env: dict[str, str] | Any, present: bool = True) -> tuple[str | None, str]:
    """A Langfuse trace link safe to show as a dashboard link. Returns (url or None, warning or "").

    Only ``https://<host>/project/<id>/traces/<id>`` with no userinfo, port, query or fragment, where the
    host is Langfuse Cloud or the host of an https ``LANGFUSE_HOST``.
    """
    if not present:
        return None, ""
    reject = (None, "trace_url is not a Langfuse trace link; outcome posted without it")
    if not isinstance(value, str) or len(value) > MAX_TRACE_URL or not value.isprintable() or " " in value:
        return reject
    try:
        parts = urlsplit(value)
        port = parts.port
    except ValueError:
        return reject
    hosts = set(LANGFUSE_CLOUD_HOSTS)
    own = urlsplit(str(env.get(LANGFUSE_HOST_ENV, "") or ""))
    if own.scheme == "https" and own.hostname:
        hosts.add(own.hostname.lower())
    ok = (
        parts.scheme == "https"
        and parts.hostname in hosts
        and parts.netloc.lower() == parts.hostname
        and port is None
        and not parts.query
        and not parts.fragment
        and TRACE_PATH.fullmatch(parts.path) is not None
    )
    return (value, "") if ok else reject


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
    req.add_argument("--mode", default="", help="which summon this is, e.g. demo or evals")
    claim = sub.add_parser("claim", help="the agent claims the task")
    claim.add_argument("--task-id", required=True)
    claim.add_argument("--agent", required=True)
    fin = sub.add_parser("finish", help="the agent posts task.result or task.failed")
    fin.add_argument("--task-id", required=True)
    fin.add_argument("--agent", required=True)
    fin.add_argument("--exit-code", type=int, required=True)
    fin.add_argument("--log", type=Path, required=True)
    fin.add_argument("--run-url", default="")
    fin.add_argument(
        "--usage", type=Path, help="the agent's usage JSON (model, input/output tokens, cost_usd, trace_url)"
    )
    args = parser.parse_args(argv)

    if args.cmd == "request":
        tid = request(
            open_ledger(ORCHESTRATOR_TOKEN_ENV), args.title, agent=args.agent, run_url=args.run_url, mode=args.mode
        )
        print(tid, file=out)
    elif args.cmd == "claim":
        open_ledger(AGENT_TOKEN_ENV).post(args.agent, "task.claim", args.task_id)
        print(f"claimed {args.task_id}", file=out)
    else:
        output = args.log.read_text(encoding="utf-8", errors="replace") if args.log.is_file() else ""
        kind, body = outcome(args.exit_code, output, run_url=args.run_url)
        if args.usage is not None:
            data, warning = read_report(args.usage)
            warnings = [warning]
            if data is not None:
                usage, warning = usage_from(data, args.usage)
                trace, trace_warning = trace_url(data.get("trace_url"), env=os.environ, present="trace_url" in data)
                if usage:
                    body["usage"] = usage
                if trace:
                    body["trace_url"] = trace
                # A file holding only a trace link is not a usage problem.
                warnings = [trace_warning] if trace and not usage else [warning, trace_warning]
            for warning in filter(None, warnings):
                print(f"summon: {warning}", file=sys.stderr)
        open_ledger(AGENT_TOKEN_ENV).post(args.agent, kind, args.task_id, **body)
        print(f"{kind} {args.task_id}", file=out)
    return 0
