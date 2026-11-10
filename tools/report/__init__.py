"""report: tell Bastion what an agent on this machine is doing.

    python -m tools.report send --kind memory.write --summary "saved 3 notes"
    python -m tools.report run --label "nightly research" -- <the agent's command>
    python -m tools.report hermes        (a Hermes shell hook: reads the hook's JSON from stdin)

`send` posts one event to the command centre's POST /api/ingest. `run` wraps any command: it posts
`run.started`, runs the command, then posts `run.finished` or `run.failed`, and exits with the
command's own exit code. Reporting trouble is only ever a warning for `run`; it never stops or
fails the agent. `hermes` turns a Hermes session hook into one event and always exits 0, so a
reporting problem can never disturb Hermes.

Configuration comes from the environment, never from arguments or files in this repository:

    BASTION_URL          the command centre, e.g. https://bastion.example (default http://localhost:8080)
    BASTION_INGEST_KEY   this agent's own ingest key. Bastion derives the agent's name from it.

Standard library only.
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable, Mapping, Sequence
from pathlib import PurePath
from typing import Any, TextIO

DEFAULT_URL = "http://localhost:8080"
LOCAL_HOSTS = frozenset({"localhost", "127.0.0.1", "::1"})
MAX_SUMMARY_LENGTH = 4096  # the command centre rejects longer summaries
MAX_FIELD_LENGTH = 128
TIMEOUT_SECONDS = 5.0

MAX_HOOK_BYTES = 8 * 1024 * 1024
# Hermes hook event -> the kind Bastion shows. Anything else is reported as hermes.<event>.
HERMES_KINDS = {
    "on_session_start": "session.started",
    "on_session_end": "turn.finished",
    "on_session_finalize": "session.ended",
    "on_session_reset": "session.reset",
    "subagent_stop": "subagent.finished",
}
SAFE_WORD = frozenset("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._:/-")

Post = Callable[[str, str, str, str, str], None]
Run = Callable[[list[str]], Any]


class ReportError(Exception):
    """Reporting could not be done. The message never contains the ingest key."""


def default_source() -> str:
    return (platform.node() or "unknown")[:MAX_FIELD_LENGTH]


def ingest_url(base_url: str) -> str:
    """The ingest endpoint for a command centre URL, refusing any URL that would expose the key."""
    parts = urllib.parse.urlsplit(base_url.strip())
    if parts.scheme not in ("http", "https") or not parts.hostname:
        raise ReportError("BASTION_URL must be an http(s) URL")
    if parts.username is not None or parts.password is not None or parts.query or parts.fragment:
        raise ReportError("BASTION_URL must not carry credentials, a query or a fragment")
    if parts.scheme == "http" and parts.hostname not in LOCAL_HOSTS:
        raise ReportError("BASTION_URL must use https unless it is this machine (localhost)")
    return urllib.parse.urlunsplit((parts.scheme, parts.netloc, parts.path.rstrip("/") + "/api/ingest", "", ""))


def post_event(
    base_url: str,
    key: str,
    kind: str,
    summary: str,
    source: str,
    opener: Callable[..., Any] = urllib.request.urlopen,
) -> None:
    """Post one event. Raises ReportError on any failure."""
    body = {
        "source": source[:MAX_FIELD_LENGTH],
        "kind": kind[:MAX_FIELD_LENGTH],
        "summary": summary[:MAX_SUMMARY_LENGTH],
    }
    request = urllib.request.Request(  # noqa: S310 - ingest_url allows only http(s)
        ingest_url(base_url),
        data=json.dumps(body).encode("utf-8"),
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
        method="POST",
    )
    try:
        with opener(request, timeout=TIMEOUT_SECONDS):
            pass
    except urllib.error.HTTPError as err:
        err.close()
        raise ReportError(f"Bastion answered HTTP {err.code}") from None
    except (urllib.error.URLError, OSError) as err:
        reason = getattr(err, "reason", err)
        raise ReportError(f"Bastion is unreachable: {type(reason).__name__}") from None


def _run_command(command: list[str]) -> Any:
    return subprocess.run(command, check=False)  # noqa: S603 - the operator's own command, no shell


def _config(env: Mapping[str, str]) -> tuple[str, str]:
    key = env.get("BASTION_INGEST_KEY", "").strip()
    if not key:
        raise ReportError("BASTION_INGEST_KEY is not set")
    base_url = env.get("BASTION_URL", "").strip() or DEFAULT_URL
    ingest_url(base_url)
    return base_url, key


def _duration(seconds: float) -> str:
    whole = round(seconds)
    return f"{whole // 60}m {whole % 60}s" if whole >= 60 else f"{whole}s"


def _send(args: argparse.Namespace, env: Mapping[str, str], post: Post, err: TextIO) -> int:
    try:
        base_url, key = _config(env)
    except ReportError as problem:
        err.write(f"report: {problem}\n")
        return 2
    try:
        post(base_url, key, args.kind, args.summary, args.source)
    except ReportError as problem:
        err.write(f"report: {problem}\n")
        return 1
    return 0


def _run(args: argparse.Namespace, env: Mapping[str, str], post: Post, run: Run, err: TextIO) -> int:
    command = list(args.command[1:] if args.command[:1] == ["--"] else args.command)
    if not command:
        err.write("report: run needs a command after --\n")
        return 2
    # Arguments can hold prompts or keys, so the summary names the program only.
    label = args.label or PurePath(command[0]).name

    def tell(kind: str, summary: str) -> None:
        try:
            base_url, key = _config(env)
            post(base_url, key, kind, summary, args.source)
        except ReportError as problem:
            err.write(f"report: warning: {kind} not reported: {problem}\n")

    tell("run.started", f"{label}: started")
    started = time.monotonic()
    try:
        code = int(run(command).returncode)
    except OSError as problem:
        tell("run.failed", f"{label}: could not start ({type(problem).__name__})")
        err.write(f"report: could not start {command[0]}: {type(problem).__name__}\n")
        return 127
    took = _duration(time.monotonic() - started)
    if code == 0:
        tell("run.finished", f"{label}: finished in {took}")
    else:
        tell("run.failed", f"{label}: failed with exit code {code} after {took}")
    return code


def _word(value: object, fallback: str) -> str:
    """A short identifier (model, platform) safe to show; anything odd becomes the fallback."""
    if not isinstance(value, str) or not value or len(value) > MAX_FIELD_LENGTH or not set(value) <= SAFE_WORD:
        return fallback
    return value


def hermes_event(payload: object) -> tuple[str, str]:
    """The (kind, summary) for a Hermes shell-hook payload.

    Only the event name, platform, model and outcome flags are used. Messages, history and tool
    input stay on the machine: Bastion gets one line about what happened, never what was said.
    """
    if not isinstance(payload, dict):
        raise ReportError("hook payload must be a JSON object")
    name = _word(payload.get("hook_event_name"), "")
    if not name:
        raise ReportError("hook payload has no hook_event_name")
    extra = payload.get("extra")
    extra = extra if isinstance(extra, dict) else {}
    platform_name = _word(extra.get("platform"), "unknown")
    model = _word(extra.get("model"), "")
    kind = HERMES_KINDS.get(name, f"hermes.{name}")
    if name == "on_session_start":
        summary = f"Session started on {platform_name}" + (f" with {model}" if model else "")
    elif name == "on_session_end":
        if extra.get("completed") is True:
            outcome = "completed"
        elif extra.get("interrupted") is True:
            outcome = "interrupted"
        else:
            outcome = "failed"
        summary = f"Turn {outcome} on {platform_name}"
    elif name == "on_session_finalize":
        summary = f"Session ended on {platform_name}"
    elif name == "on_session_reset":
        summary = f"Session reset on {platform_name}"
    elif name == "subagent_stop":
        summary = "Subagent finished"
    else:
        summary = f"Hermes event {name}"
    return kind, summary


def _hermes(args: argparse.Namespace, env: Mapping[str, str], post: Post, stdin: TextIO, err: TextIO) -> int:
    try:
        raw = stdin.read(MAX_HOOK_BYTES + 1)
        if len(raw) > MAX_HOOK_BYTES:
            raise ReportError("hook payload is too large")
        try:
            payload = json.loads(raw)
        except ValueError:
            raise ReportError("hook payload is not JSON") from None
        kind, summary = hermes_event(payload)
        base_url, key = _config(env)
        post(base_url, key, kind, summary, args.source)
    except ReportError as problem:
        err.write(f"report: warning: Hermes event not reported: {problem}\n")
    return 0  # never disturb Hermes over a reporting problem


def main(
    argv: Sequence[str] | None = None,
    env: Mapping[str, str] | None = None,
    post: Post = post_event,
    run: Run = _run_command,
    err: TextIO | None = None,
    stdin: TextIO | None = None,
) -> int:
    parser = argparse.ArgumentParser(prog="python -m tools.report", description="Report agent activity to Bastion.")
    sub = parser.add_subparsers(dest="action", required=True)
    send = sub.add_parser("send", help="post one event")
    send.add_argument("--kind", default="event", help="event kind, e.g. task.completed")
    send.add_argument("--summary", required=True, help="one line saying what happened")
    send.add_argument("--source", default=default_source(), help="where this ran (default: this machine's name)")
    wrap = sub.add_parser("run", help="run a command and report its start and outcome")
    wrap.add_argument("--label", default="", help="what to call this run (default: the program's name)")
    wrap.add_argument("--source", default=default_source(), help="where this ran (default: this machine's name)")
    wrap.add_argument("command", nargs=argparse.REMAINDER, help="-- then the command to run")
    hook = sub.add_parser("hermes", help="report a Hermes shell-hook event read from stdin")
    hook.add_argument("--source", default=default_source(), help="where this ran (default: this machine's name)")
    args = parser.parse_args(argv)
    env = os.environ if env is None else env
    err = sys.stderr if err is None else err
    if args.action == "send":
        return _send(args, env, post, err)
    if args.action == "hermes":
        return _hermes(args, env, post, sys.stdin if stdin is None else stdin, err)
    return _run(args, env, post, run, err)
