"""simple_agent: a minimal agent that connects to Bastion instantly.

This is the simplest possible agent you can connect to Bastion. It posts a heartbeat
and can run a simple command, demonstrating the connection in under 60 seconds.

Quick start:

1. Generate a key for this agent:
   python -c "import secrets; print(secrets.token_urlsafe(32))"

2. Add it to your command centre's INGEST_AGENT_KEYS as "Simple-Agent" (or any name)

3. Set environment variables:
   export BASTION_URL=https://your-bastion.example.com
   export BASTION_INGEST_KEY=<the key from step 1>

4. Run it:
   python -m tools.simple_agent ping

You should see "Simple-Agent: ping" appear in Bastion's feed within seconds.

That's it. No database, no ledger, no configuration files. Just two environment variables.
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from collections.abc import Sequence
from typing import TextIO

AGENT_NAME = "Simple-Agent"


def main(
    argv: Sequence[str] | None = None,
    env: dict[str, str] | None = None,
    out: TextIO = sys.stdout,
    err: TextIO = sys.stderr,
) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m tools.simple_agent",
        description="Minimal agent: connect to Bastion in seconds.",
    )
    sub = parser.add_subparsers(dest="cmd", required=True)

    # ping: post one heartbeat to prove the connection
    sub.add_parser("ping", help="Post a heartbeat to Bastion")

    # run: run a command and report start/finish
    run = sub.add_parser("run", help="Run a command and report it to Bastion")
    run.add_argument("command", nargs=argparse.REMAINDER, help="The command to run")

    # loop: ping every N seconds forever (Ctrl+C to stop)
    loop = sub.add_parser("loop", help="Ping Bastion every N seconds")
    loop.add_argument("--interval", type=int, default=60, help="Seconds between pings (default: 60)")

    args = parser.parse_args(argv)
    env = dict(os.environ) if env is None else env

    if args.cmd == "ping":
        from tools.report import main as report_main

        result = report_main(
            ["send", "--kind", "heartbeat", "--summary", f"{AGENT_NAME}: ping"],
            env=env,
            err=err,
        )
        if result == 0:
            print(f"{AGENT_NAME}: heartbeat sent to Bastion", file=out)
        return result

    if args.cmd == "run":
        if not args.command:
            err.write(f"{AGENT_NAME}: run needs a command\n")
            return 2
        from tools.report import main as report_main

        # Report start
        report_main(
            ["send", "--kind", "run.started", "--summary", f"{AGENT_NAME}: started {' '.join(args.command)}"],
            env=env,
            err=err,
        )
        # Run the command
        try:
            code = subprocess.run(args.command, check=False).returncode
        except OSError as e:
            err.write(f"{AGENT_NAME}: could not start command: {e}\n")
            report_main(
                ["send", "--kind", "run.failed", "--summary", f"{AGENT_NAME}: failed to start"],
                env=env,
                err=err,
            )
            return 1
        # Report finish
        status = "finished" if code == 0 else f"failed (exit {code})"
        report_main(
            ["send", "--kind", "run.finished" if code == 0 else "run.failed", "--summary", f"{AGENT_NAME}: {status}"],
            env=env,
            err=err,
        )
        return code

    if args.cmd == "loop":
        import time

        from tools.report import main as report_main

        interval = args.interval
        count = 0
        try:
            while True:
                count += 1
                result = report_main(
                    ["send", "--kind", "heartbeat", "--summary", f"{AGENT_NAME}: heartbeat #{count}"],
                    env=env,
                    err=err,
                )
                if result == 0:
                    print(f"{AGENT_NAME}: heartbeat #{count} sent", file=out)
                else:
                    print(f"{AGENT_NAME}: heartbeat #{count} failed", file=err)
                time.sleep(interval)
        except KeyboardInterrupt:
            print(f"\n{AGENT_NAME}: loop stopped", file=out)
            return 0

    return 0
