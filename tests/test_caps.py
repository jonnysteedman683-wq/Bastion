"""caps: daily spend cap enforcement (G1d-2). Run with: python -m unittest discover -s tests -t ."""

from __future__ import annotations

import io
import json
import tempfile
import unittest
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from tools import caps

NOW = datetime(2026, 11, 15, 3, 0, tzinfo=UTC)
DSN = "postgresql://db.example.com/postgres"
ENV = {"LEDGER_DSN": DSN, "NTFY_TOPIC": "topic-abc"}


def _status(spent: float, cap: float | None) -> str:
    return "none" if cap is None else "over" if spent >= cap else "ok"


def report(fleet: float = 1.0, fleet_cap: float | None = 5.0, **agents: tuple[float, float | None]) -> dict[str, Any]:
    return {
        "today": {"cost_usd": fleet, "cap_usd": fleet_cap, "cap_status": _status(fleet, fleet_cap)},
        "agents": [
            {"agent": name, "today_usd": spent, "cap_usd": cap, "cap_status": _status(spent, cap)}
            for name, (spent, cap) in agents.items()
        ],
    }


class Sent:
    def __init__(self, code: int = 0) -> None:
        self.code = code
        self.messages: list[list[str]] = []

    def __call__(self, argv: list[str]) -> int:
        self.messages.append(argv)
        return self.code


def run(
    argv: list[str], rep: dict[str, Any] | Exception, env: dict[str, str] | None = None, send: Sent | None = None
) -> tuple[int, str, Sent]:
    out = io.StringIO()
    sent = send or Sent()

    def load(_env: Any, _now: datetime) -> dict[str, Any]:
        if isinstance(rep, Exception):
            raise rep
        return rep

    code = caps.main(argv, env=ENV if env is None else env, now=NOW, load=load, send=sent, out=out)
    return code, out.getvalue(), sent


class BreachTests(unittest.TestCase):
    def test_lists_the_fleet_and_each_agent_over_its_cap(self) -> None:
        found = caps.breaches(report(6.0, 5.0, sebastian=(2.5, 2.0), claude=(0.5, 2.0), jules=(9.0, None)))
        self.assertEqual([b["who"] for b in found], ["fleet", "sebastian"])
        self.assertEqual((found[1]["spent"], found[1]["cap"]), (2.5, 2.0))

    def test_nothing_under_cap(self) -> None:
        self.assertEqual(caps.breaches(report(1.0, 5.0, sebastian=(1.0, 2.0))), [])


class GateTests(unittest.TestCase):
    def test_lets_an_agent_under_its_cap_through(self) -> None:
        code, out, _ = run(["gate", "--agent", "sebastian"], report(1.0, 5.0, sebastian=(1.0, 2.0)))
        self.assertEqual(code, 0)
        self.assertIn("under", out)

    def test_refuses_an_agent_over_its_cap_whatever_the_case(self) -> None:
        code, out, sent = run(["gate", "--agent", "Sebastian"], report(2.5, 5.0, sebastian=(2.5, 2.0)))
        self.assertEqual(code, 3)
        self.assertIn("::error::", out)
        self.assertIn("$2.50", out)
        self.assertEqual(sent.messages, [])

    def test_refuses_everyone_when_the_fleet_is_over(self) -> None:
        code, out, _ = run(["gate", "--agent", "claude"], report(5.0, 5.0, sebastian=(1.0, 2.0)))
        self.assertEqual(code, 3)
        self.assertIn("fleet", out)

    def test_an_agent_with_no_spend_today_passes(self) -> None:
        code, _, _ = run(["gate", "--agent", "aider"], report(1.0, 5.0))
        self.assertEqual(code, 0)


class CheckTests(unittest.TestCase):
    def test_alerts_each_breach_once_a_day(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            alerted = Path(tmp) / "alerted.json"
            rep = report(1.0, 5.0, sebastian=(2.5, 2.0))
            code, _, sent = run(["check", "--notify", "--alerted", str(alerted)], rep)
            self.assertEqual(code, 0)
            self.assertEqual(len(sent.messages), 1)
            argv = sent.messages[0]
            self.assertEqual(argv[0], "send")
            message = argv[argv.index("--message") + 1]
            self.assertIn("sebastian", message)
            self.assertIn("$2.50", message)
            self.assertIn("paused", message)
            self.assertEqual(argv[argv.index("--priority") + 1], "urgent")
            self.assertEqual(json.loads(alerted.read_text()), {"day": "2026-11-15", "alerted": ["sebastian"]})

            _, _, again = run(["check", "--notify", "--alerted", str(alerted)], rep)
            self.assertEqual(again.messages, [])

    def test_a_new_day_alerts_again(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            alerted = Path(tmp) / "alerted.json"
            alerted.write_text(json.dumps({"day": "2026-11-14", "alerted": ["sebastian"]}))
            _, _, sent = run(["check", "--notify", "--alerted", str(alerted)], report(1.0, 5.0, sebastian=(3.0, 2.0)))
            self.assertEqual(len(sent.messages), 1)

    def test_a_failed_send_is_retried_next_run(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            alerted = Path(tmp) / "alerted.json"
            code, out, _ = run(
                ["check", "--notify", "--alerted", str(alerted)], report(1.0, 5.0, sebastian=(3.0, 2.0)), send=Sent(1)
            )
            self.assertEqual(code, 1)
            self.assertIn("::error::", out)
            self.assertEqual(json.loads(alerted.read_text())["alerted"], [])

    def test_a_corrupt_alerted_file_is_treated_as_empty(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            alerted = Path(tmp) / "alerted.json"
            alerted.write_text("{not json")
            code, _, sent = run(["check", "--notify", "--alerted", str(alerted)], report(9.0, 5.0))
            self.assertEqual(code, 0)
            self.assertEqual(len(sent.messages), 1)

    def test_notify_needs_a_topic_and_sends_nothing_without_one(self) -> None:
        code, out, sent = run(["check", "--notify"], report(9.0, 5.0), env={"LEDGER_DSN": DSN})
        self.assertEqual(code, 2)
        self.assertIn("NTFY_TOPIC", out)
        self.assertEqual(sent.messages, [])

    def test_writes_a_summary_table(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            summary = Path(tmp) / "summary.md"
            run(["check", "--summary", str(summary)], report(1.0, 5.0, sebastian=(2.5, 2.0), claude=(0.1, 2.0)))
            text = summary.read_text()
            self.assertIn("| sebastian | $2.50 | $2.00 | over |", text)
            self.assertIn("| claude | $0.10 | $2.00 | ok |", text)


class ConfigTests(unittest.TestCase):
    def test_needs_a_ledger(self) -> None:
        out_buf = io.StringIO()
        code = caps.main(["gate", "--agent", "sebastian"], env={}, now=NOW, out=out_buf)
        out = out_buf.getvalue()
        self.assertEqual(code, 2)
        self.assertIn("LEDGER_DSN", out)

    def test_an_unreachable_ledger_fails_without_echoing_the_dsn(self) -> None:
        code, out, _ = run(["gate", "--agent", "sebastian"], ConnectionError(DSN))
        self.assertEqual(code, 1)
        self.assertNotIn("db.example.com", out)

    def test_the_real_loader_reads_the_command_centre_spend_report(self) -> None:
        rep = caps.load_report({}, NOW, ledger=FixedSpend())
        self.assertEqual(rep["today"]["cost_usd"], 3.0)
        self.assertEqual(rep["agents"][0]["agent"], "sebastian")
        self.assertEqual(rep["agents"][0]["cap_status"], "over")


class FixedSpend:
    source = "test"

    def spend(self, since: str) -> list[dict[str, object]]:
        row = {"day": "2026-11-15", "agent": "sebastian", "runs": 1, "input_tokens": 1, "output_tokens": 1}
        return [{**row, "cost_usd": 3.0}]


if __name__ == "__main__":
    unittest.main()
