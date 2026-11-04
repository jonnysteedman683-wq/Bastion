"""Run with: python -m unittest discover -s tests -t ."""

from __future__ import annotations

import io
import json
import tempfile
import unittest
from pathlib import Path
from typing import Any

from tools import summon


class FakeEntry:
    def __init__(self, digest: str) -> None:
        self.digest = digest


class FakeView:
    def __init__(self, digests: list[str]) -> None:
        self.entries = [FakeEntry(d) for d in digests]


class FakeLedger:
    """Stands in for lestinxor's SupabaseLedger: records posts, exposes .ledger.entries."""

    def __init__(self, digests: list[str] | None = None) -> None:
        self.ledger = FakeView(digests or [])
        self.synced = 0
        self.posts: list[tuple[str, str, str, dict[str, Any]]] = []

    def sync(self) -> int:
        self.synced += 1
        return 0

    def post(self, sender: str, kind: str, task_id: str = "", **body: Any) -> None:
        self.posts.append((sender, kind, task_id, body))


class TaskIdTest(unittest.TestCase):
    def test_linx_style_and_deterministic(self) -> None:
        a = summon.task_id("Run the Sebastian demo", head="ab" * 32)
        self.assertRegex(a, r"^bastion/run-the-sebastian-demo-[0-9a-f]{6}$")
        self.assertEqual(a, summon.task_id("Run the Sebastian demo", head="ab" * 32))

    def test_changes_with_head(self) -> None:
        self.assertNotEqual(summon.task_id("x", head="1"), summon.task_id("x", head="2"))

    def test_slug_is_bounded_and_never_empty(self) -> None:
        self.assertTrue(summon.task_id("!!!", head="").startswith("bastion/task-"))
        slug = summon.task_id("word " * 40, head="").split("/", 1)[1].rsplit("-", 1)[0]
        self.assertLessEqual(len(slug), 40)


class RequestTest(unittest.TestCase):
    def test_mode_is_recorded_when_given(self) -> None:
        ledger = FakeLedger()
        summon.request(ledger, "Run the evals", agent="sebastian", run_url="", mode="evals")
        self.assertEqual(ledger.posts[0][3]["mode"], "evals")

    def test_posts_a_task_request_as_the_orchestrator(self) -> None:
        ledger = FakeLedger(["d" * 64])
        tid = summon.request(ledger, "Run the Sebastian demo", agent="sebastian", run_url="https://x/run/1")
        self.assertEqual(ledger.synced, 1)
        sender, kind, posted_tid, body = ledger.posts[0]
        self.assertEqual((sender, kind, posted_tid), ("lestinxor", "task.request", tid))
        self.assertEqual(
            body,
            {
                "title": "Run the Sebastian demo",
                "flow": "bastion-summon",
                "agent": "sebastian",
                "unattended": True,
                "requested_by": "jonny",
                "run_url": "https://x/run/1",
            },
        )

    def test_title_is_trimmed_and_required(self) -> None:
        ledger = FakeLedger()
        summon.request(ledger, "  " + "t" * 300 + "  ", agent="sebastian", run_url="")
        self.assertEqual(len(ledger.posts[0][3]["title"]), 200)
        with self.assertRaises(ValueError):
            summon.request(FakeLedger(), "   ", agent="sebastian", run_url="")


class OutcomeTest(unittest.TestCase):
    def test_success_is_a_result_with_the_last_line(self) -> None:
        kind, body = summon.outcome(0, "step 1\nCompleted: hello.txt written\n\n", run_url="u")
        self.assertEqual(kind, "task.result")
        self.assertEqual(
            body,
            {
                "summary": "Completed: hello.txt written",
                "exit_code": 0,
                "run_url": "u",
                "output": "step 1\nCompleted: hello.txt written",
            },
        )

    def test_output_keeps_the_tail_and_is_bounded(self) -> None:
        text = "\n".join(f"line {i}" for i in range(500))
        out = summon.outcome(1, text, run_url="")[1]["output"]
        self.assertTrue(out.endswith("line 499"))
        self.assertLessEqual(len(out), summon.MAX_OUTPUT)
        self.assertLessEqual(out.count("\n") + 1, summon.MAX_OUTPUT_LINES)

    def test_failure_is_task_failed_with_a_reason(self) -> None:
        kind, body = summon.outcome(2, "Traceback\nValueError: boom\n", run_url="u")
        self.assertEqual(kind, "task.failed")
        self.assertEqual(body["reason"], "exit 2: ValueError: boom")
        self.assertEqual(body["exit_code"], 2)

    def test_empty_output_still_says_something(self) -> None:
        self.assertEqual(summon.outcome(0, "", run_url="")[1]["summary"], "finished with no output")
        self.assertEqual(summon.outcome(1, "", run_url="")[1]["reason"], "exit 1: no output")

    def test_lines_are_bounded(self) -> None:
        self.assertEqual(len(summon.outcome(0, "x" * 999, run_url="")[1]["summary"]), 200)


class CliTest(unittest.TestCase):
    def setUp(self) -> None:
        self.ledgers: dict[str, FakeLedger] = {}

        def opener(token_env: str) -> FakeLedger:
            return self.ledgers.setdefault(token_env, FakeLedger(["e" * 64]))

        self.opener = opener

    def run_cli(self, *argv: str) -> str:
        out = io.StringIO()
        code = summon.main(list(argv), open_ledger=self.opener, out=out)
        self.assertEqual(code, 0)
        return out.getvalue()

    def test_request_prints_the_task_id(self) -> None:
        tid = self.run_cli("request", "--title", "Run the demo", "--agent", "sebastian", "--run-url", "u").strip()
        self.assertTrue(tid.startswith("bastion/run-the-demo-"))
        self.assertEqual(self.ledgers["UNISYNC_ORCHESTRATOR_TOKEN"].posts[0][1], "task.request")

    def test_claim_and_finish_post_as_the_agent(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            log = Path(tmp) / "out.txt"
            log.write_text("ok\nDone.\n", encoding="utf-8")
            self.run_cli("claim", "--task-id", "bastion/t-1", "--agent", "sebastian")
            self.run_cli(
                "finish", "--task-id", "bastion/t-1", "--agent", "sebastian",
                "--exit-code", "0", "--log", str(log), "--run-url", "u",
            )  # fmt: skip
        posts = self.ledgers["UNISYNC_AGENT_TOKEN"].posts
        self.assertEqual([(p[0], p[1], p[2]) for p in posts], [
            ("sebastian", "task.claim", "bastion/t-1"),
            ("sebastian", "task.result", "bastion/t-1"),
        ])  # fmt: skip
        self.assertEqual(posts[1][3]["summary"], "Done.")

    def test_body_values_are_json_safe_for_the_gateway(self) -> None:
        # The gateway's canonical JSON refuses floats; every value must be str, int or bool.
        for body in (summon.outcome(0, "a", run_url="u")[1], summon.outcome(3, "b", run_url="u")[1]):
            for v in body.values():
                self.assertIsInstance(v, (str, int, bool))
            json.dumps(body)


if __name__ == "__main__":
    unittest.main()
