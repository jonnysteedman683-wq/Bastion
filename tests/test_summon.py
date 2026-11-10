"""Run with: python -m unittest discover -s tests -t ."""

from __future__ import annotations

import contextlib
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


def _ints_only(value: Any) -> bool:
    """The gateway's canon() rejects non-integer numbers anywhere in the body (unisync/gateway)."""
    if isinstance(value, dict):
        return all(_ints_only(v) for v in value.values())
    if isinstance(value, list):
        return all(_ints_only(v) for v in value)
    return not isinstance(value, float)


class UsageTest(unittest.TestCase):
    def write(self, tmp: str, payload: Any) -> Path:
        path = Path(tmp) / "usage.json"
        path.write_text(payload if isinstance(payload, str) else json.dumps(payload), encoding="utf-8")
        return path

    def test_contract_fields_kept_and_cost_becomes_integer_micro_dollars(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = self.write(tmp, {
                "model": "claude-sonnet-5-5", "input_tokens": 1234, "output_tokens": 567, "cost_usd": 0.0123,
            })  # fmt: skip
            usage, warning = summon.load_usage(path)
        self.assertEqual(warning, "")
        self.assertEqual(usage, {
            "model": "claude-sonnet-5-5", "input_tokens": 1234, "output_tokens": 567, "cost_micro_usd": 12300,
        })  # fmt: skip
        self.assertTrue(_ints_only(usage))

    def test_untrusted_values_are_dropped(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = self.write(tmp, {
                "model": 7, "input_tokens": -5, "output_tokens": True, "cost_usd": "1.0",
                "extra": "x", "total": 3,
            })  # fmt: skip
            usage, warning = summon.load_usage(path)
        self.assertIsNone(usage)
        self.assertIn("no usable", warning)

    def test_whole_float_tokens_are_kept_as_integers_and_values_are_clamped(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = self.write(tmp, {"model": "m" * 500, "input_tokens": 10.0, "output_tokens": 2.5,
                                    "cost_usd": 1e9})  # fmt: skip
            usage, _ = summon.load_usage(path)
        assert usage is not None
        self.assertEqual(usage["input_tokens"], 10)
        self.assertNotIn("output_tokens", usage)  # a fractional token count is not a count
        self.assertEqual(len(usage["model"]), summon.MAX_MODEL)
        self.assertEqual(usage["cost_micro_usd"], summon.MAX_COST_MICRO_USD)
        self.assertTrue(_ints_only(usage))

    def test_missing_malformed_or_non_object_files_give_a_warning_not_an_error(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            cases = [Path(tmp) / "absent.json", self.write(tmp, "{not json"), self.write(tmp, [1, 2])]
            for path in cases:
                usage, warning = summon.load_usage(path)
                self.assertIsNone(usage)
                self.assertTrue(warning)


class FinishUsageTest(CliTest):
    def finish(self, tmp: str, code: int, *extra: str) -> tuple[str, dict[str, Any]]:
        log = Path(tmp) / "out.txt"
        log.write_text("Done.\n", encoding="utf-8")
        self.run_cli(
            "finish", "--task-id", "bastion/t-1", "--agent", "sebastian",
            "--exit-code", str(code), "--log", str(log), *extra,
        )  # fmt: skip
        _, kind, _, body = self.ledgers["UNISYNC_AGENT_TOKEN"].posts[-1]
        return kind, body

    def test_usage_rides_on_result_and_failure(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "usage.json"
            path.write_text(json.dumps({"model": "m", "input_tokens": 3, "output_tokens": 4}), encoding="utf-8")
            for code, want in ((0, "task.result"), (2, "task.failed")):
                kind, body = self.finish(tmp, code, "--usage", str(path))
                self.assertEqual(kind, want)
                self.assertEqual(body["usage"], {"model": "m", "input_tokens": 3, "output_tokens": 4})
                self.assertTrue(_ints_only(body))

    def test_bad_usage_file_still_reports_the_outcome(self) -> None:
        err = io.StringIO()
        with tempfile.TemporaryDirectory() as tmp, contextlib.redirect_stderr(err):
            kind, body = self.finish(tmp, 0, "--usage", str(Path(tmp) / "absent.json"))
        self.assertEqual(kind, "task.result")
        self.assertNotIn("usage", body)
        self.assertIn("usage", err.getvalue())

    def test_without_usage_the_body_is_unchanged(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            _, body = self.finish(tmp, 0)
        self.assertEqual(body, summon.outcome(0, "Done.\n", run_url="")[1])


if __name__ == "__main__":
    unittest.main()
