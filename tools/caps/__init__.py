"""caps: enforce the daily spend caps (backlog G1d-2).

    python -m tools.caps gate --agent sebastian       # before a summon: refuse an agent over its cap
    python -m tools.caps check --notify --alerted F    # on a schedule: alert the phone once per breach per day

Spend comes from the ledger's ``bastion.spend_daily`` view through the read-only ``LEDGER_DSN``, and the caps from
``services/command-centre/caps.json``, both read with the command centre's own code so the dashboard and the
enforcement can never disagree. A day is the UTC day, like the ledger.

An agent is over when its spend today reaches its cap (``agent_daily_usd`` or ``default_agent_daily_usd``); the whole
fleet is over when the total reaches ``daily_usd``. Over means paused: ``gate`` refuses to summon it until 00:00 UTC.
The fleet-wide kill switch stays with the owner key (``linx halt``); this never signs anything.

Environment: ``LEDGER_DSN`` (never printed), and for ``--notify`` the ``NTFY_*`` variables of ``tools.notify``.

Exit codes: 0 under cap / check done, 1 the ledger or ntfy failed, 2 bad configuration, 3 gate refused (over cap).
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import sys
from collections.abc import Callable, Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path
from types import ModuleType
from typing import Any, TextIO

COMMAND_CENTRE = Path(__file__).resolve().parents[2] / "services" / "command-centre" / "app.py"
LEDGER_TIMEOUT = 10.0

Load = Callable[[Mapping[str, str], datetime], dict[str, Any]]
Send = Callable[[list[str]], int]


class ConfigError(ValueError):
    """Bad configuration. The message never contains the DSN."""


def _command_centre() -> ModuleType:
    spec = importlib.util.spec_from_file_location("bastion_command_centre", COMMAND_CENTRE)
    if spec is None or spec.loader is None:
        raise ConfigError(f"cannot load {COMMAND_CENTRE}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module  # dataclasses look their module up by name
    spec.loader.exec_module(module)
    return module


def load_report(env: Mapping[str, str], now: datetime, ledger: Any = None) -> dict[str, Any]:
    """The command centre's spend report: ``today`` (fleet total and cap) and ``agents`` (each against its cap)."""
    app = _command_centre()
    if ledger is None:
        dsn = env.get("LEDGER_DSN", "")
        if not dsn:
            raise ConfigError("LEDGER_DSN is not set")
        try:
            ledger = app.PostgresLedger(app._pg8000_connect(dsn, timeout=LEDGER_TIMEOUT))
        except ValueError:
            raise ConfigError("LEDGER_DSN must be a postgresql:// URL") from None
    report: dict[str, Any] = app.spend_report(ledger, now)
    return report


def _usd(value: object) -> float:
    return float(value) if isinstance(value, int | float) and not isinstance(value, bool) else 0.0


def breaches(report: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Who is over today: ``fleet`` first, then each agent. Each has ``who``, ``spent`` and ``cap``."""
    found = []
    today = report.get("today") or {}
    if today.get("cap_status") == "over":
        found.append({"who": "fleet", "spent": _usd(today.get("cost_usd")), "cap": _usd(today.get("cap_usd"))})
    for row in report.get("agents") or []:
        if row.get("cap_status") == "over":
            found.append(
                {"who": str(row.get("agent", "")), "spent": _usd(row.get("today_usd")), "cap": _usd(row.get("cap_usd"))}
            )
    return found


def describe(breach: Mapping[str, Any]) -> str:
    spent, cap = f"${breach['spent']:.2f}", f"${breach['cap']:.2f}"
    if breach["who"] == "fleet":
        return f"The fleet spent {spent} today, over its {cap} daily cap. Every summon is paused until 00:00 UTC."
    return f"{breach['who']} spent {spent} today, over its {cap} daily cap. Its summons are paused until 00:00 UTC."


def _read_alerted(path: Path | None, day: str) -> set[str]:
    if path is None:
        return set()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return set()
    if not isinstance(data, dict) or data.get("day") != day or not isinstance(data.get("alerted"), list):
        return set()
    return {str(who) for who in data["alerted"]}


def _write_alerted(path: Path | None, day: str, alerted: set[str]) -> None:
    if path is not None:
        path.write_text(json.dumps({"day": day, "alerted": sorted(alerted)}), encoding="utf-8")


def _summary(report: Mapping[str, Any], found: list[dict[str, Any]]) -> str:
    today = report.get("today") or {}
    lines = [
        "### Spend caps (today, UTC)",
        "",
        f"Fleet: ${_usd(today.get('cost_usd')):.2f} of ${_usd(today.get('cap_usd')):.2f} "
        f"({today.get('cap_status', 'none')})",
        "",
        "| Agent | Today | Cap | Status |",
        "| --- | --- | --- | --- |",
    ]
    for row in report.get("agents") or []:
        cap = row.get("cap_usd")
        cap_text = "none" if cap is None else f"${_usd(cap):.2f}"
        spent = _usd(row.get("today_usd"))
        lines.append(f"| {row.get('agent', '')} | ${spent:.2f} | {cap_text} | {row.get('cap_status', '')} |")
    lines += ["", *(f"- **Over:** {describe(b)}" for b in found)] if found else ["", "Nothing is over its cap."]
    return "\n".join(lines) + "\n"


def _notify(argv: list[str]) -> int:
    from tools import notify

    return notify.main(argv)


def main(
    argv: Sequence[str] | None = None,
    *,
    env: Mapping[str, str] | None = None,
    now: datetime | None = None,
    load: Load = load_report,
    send: Send = _notify,
    out: TextIO = sys.stdout,
) -> int:
    parser = argparse.ArgumentParser(prog="python -m tools.caps", description=(__doc__ or "").splitlines()[0])
    sub = parser.add_subparsers(dest="cmd", required=True)
    gate = sub.add_parser("gate", help="exit 3 if the agent or the fleet is over its daily cap")
    gate.add_argument("--agent", required=True)
    check = sub.add_parser("check", help="report every breach; with --notify, alert each once per day")
    check.add_argument("--notify", action="store_true", help="push each new breach to the phone through ntfy")
    check.add_argument("--alerted", type=Path, help="JSON file remembering today's alerts between runs")
    check.add_argument("--summary", type=Path, help="append a Markdown table here (e.g. $GITHUB_STEP_SUMMARY)")
    check.add_argument("--click", default="", help="https link opened when the alert is tapped")
    args = parser.parse_args(argv)
    env = os.environ if env is None else env
    now = now or datetime.now(UTC)
    day = now.astimezone(UTC).date().isoformat()

    if args.cmd == "check" and args.notify and not (env.get("NTFY_TOPIC") or "").strip():
        print("::error::--notify needs NTFY_TOPIC (see docs/NOTIFY.md); nothing was checked or sent", file=out)
        return 2
    try:
        report = load(env, now)
    except ConfigError as exc:
        print(f"::error::{exc}", file=out)
        return 2
    except Exception as exc:  # any driver error; its text could carry the DSN, so print only the type
        print(f"::error::could not read spend from the ledger ({type(exc).__name__})", file=out)
        return 1
    found = breaches(report)

    if args.cmd == "gate":
        agent = args.agent.casefold()
        hit = next((b for b in found if b["who"] == "fleet" or b["who"].casefold() == agent), None)
        if hit is not None:
            print(f"::error::Not summoning {args.agent}: {describe(hit)}", file=out)
            return 3
        print(f"{args.agent} is under its daily cap", file=out)
        return 0

    if args.summary is not None:
        with args.summary.open("a", encoding="utf-8") as handle:
            handle.write(_summary(report, found))
    for breach in found:
        print(f"over: {describe(breach)}", file=out)
    if not found:
        print("nothing is over its cap", file=out)
    if not args.notify:
        return 0

    alerted = _read_alerted(args.alerted, day)
    failed = False
    for breach in found:
        if breach["who"] in alerted:
            continue
        message = ["send", "--title", "Bastion: spend cap hit", "--message", describe(breach)]
        message += ["--priority", "urgent", "--tags", "warning,money_with_wings"]
        if args.click:
            message += ["--click", args.click]
        if send(message) == 0:
            alerted.add(breach["who"])
        else:
            failed = True
    _write_alerted(args.alerted, day, alerted)
    if failed:
        print("::error::an alert could not be sent; the next run tries again", file=out)
        return 1
    return 0
