"""Run with: python -m unittest discover -s tests -t ."""

from __future__ import annotations

import io
import json
import unittest
import urllib.error
from typing import Any

from tools import report

ENV = {"BASTION_URL": "https://bastion.example", "BASTION_INGEST_KEY": "agent-secret"}


class FakeResponse:
    def __init__(self, status: int = 201) -> None:
        self.status = status

    def __enter__(self) -> FakeResponse:
        return self

    def __exit__(self, *exc: object) -> None:
        return None


class Recorder:
    """Stands in for report.post_event."""

    def __init__(self, fail: bool = False) -> None:
        self.events: list[dict[str, str]] = []
        self.fail = fail

    def __call__(self, base_url: str, key: str, kind: str, summary: str, source: str) -> None:
        if self.fail:
            raise report.ReportError("Bastion is unreachable")
        self.events.append({"base_url": base_url, "key": key, "kind": kind, "summary": summary, "source": source})


class FakeCompleted:
    def __init__(self, returncode: int) -> None:
        self.returncode = returncode


class IngestUrlTest(unittest.TestCase):
    def test_builds_the_ingest_url(self) -> None:
        self.assertEqual(report.ingest_url("https://bastion.example/"), "https://bastion.example/api/ingest")
        self.assertEqual(report.ingest_url("http://localhost:8080"), "http://localhost:8080/api/ingest")
        self.assertEqual(report.ingest_url("http://127.0.0.1:8080"), "http://127.0.0.1:8080/api/ingest")

    def test_refuses_to_send_a_key_in_the_clear_or_to_an_odd_url(self) -> None:
        for bad in (
            "http://bastion.example",  # plain http off this machine would expose the key
            "ftp://bastion.example",
            "https://user:pw@bastion.example",
            "https://bastion.example/?x=1",
            "bastion.example",
            "",
            "http://[::1",  # urlsplit itself rejects this
            "https://bastion.example:notaport",
        ):
            with self.subTest(bad), self.assertRaises(report.ReportError):
                report.ingest_url(bad)


class PostEventTest(unittest.TestCase):
    def test_posts_json_with_the_key_as_a_bearer_token(self) -> None:
        seen: dict[str, Any] = {}

        def opener(request: Any, timeout: float) -> FakeResponse:
            seen["url"] = request.full_url
            seen["auth"] = request.get_header("Authorization")
            seen["body"] = json.loads(request.data)
            seen["timeout"] = timeout
            return FakeResponse()

        report.post_event("https://bastion.example", "agent-secret", "task.completed", "done", "pc", opener=opener)
        self.assertEqual(seen["url"], "https://bastion.example/api/ingest")
        self.assertEqual(seen["auth"], "Bearer agent-secret")
        self.assertEqual(seen["body"], {"source": "pc", "kind": "task.completed", "summary": "done"})
        self.assertLessEqual(seen["timeout"], 10)

    def test_long_summaries_are_cut_to_what_bastion_accepts(self) -> None:
        seen: dict[str, Any] = {}

        def opener(request: Any, timeout: float) -> FakeResponse:
            seen["body"] = json.loads(request.data)
            return FakeResponse()

        report.post_event("https://bastion.example", "k", "event", "x" * 10_000, "pc", opener=opener)
        self.assertEqual(len(seen["body"]["summary"]), report.MAX_SUMMARY_LENGTH)

    def test_failures_become_report_errors_without_the_key(self) -> None:
        def refused(request: Any, timeout: float) -> FakeResponse:
            raise urllib.error.HTTPError(request.full_url, 401, "Unauthorized", {}, None)  # type: ignore[arg-type]

        def down(request: Any, timeout: float) -> FakeResponse:
            raise urllib.error.URLError("connection refused")

        for opener in (refused, down):
            with self.assertRaises(report.ReportError) as caught:
                report.post_event("https://bastion.example", "agent-secret", "event", "s", "pc", opener=opener)
            self.assertNotIn("agent-secret", str(caught.exception))


class SendCommandTest(unittest.TestCase):
    def test_send_posts_one_event(self) -> None:
        post = Recorder()
        code = report.main(["send", "--kind", "memory.write", "--summary", "saved 3 notes"], env=ENV, post=post)
        self.assertEqual(code, 0)
        self.assertEqual(
            post.events,
            [
                {
                    "base_url": "https://bastion.example",
                    "key": "agent-secret",
                    "kind": "memory.write",
                    "summary": "saved 3 notes",
                    "source": report.default_source(),
                }
            ],
        )

    def test_send_fails_loudly_when_unconfigured_or_unreachable(self) -> None:
        err = io.StringIO()
        self.assertEqual(report.main(["send", "--summary", "x"], env={}, post=Recorder(), err=err), 2)
        self.assertIn("BASTION_INGEST_KEY", err.getvalue())
        err = io.StringIO()
        self.assertEqual(report.main(["send", "--summary", "x"], env=ENV, post=Recorder(fail=True), err=err), 1)
        self.assertNotIn("agent-secret", err.getvalue())


class RunCommandTest(unittest.TestCase):
    def run_cmd(
        self, argv: list[str], returncode: int = 0, env: dict[str, str] | None = None, fail: bool = False
    ) -> tuple[int, Recorder, list[list[str]], str]:
        post = Recorder(fail=fail)
        ran: list[list[str]] = []

        def run(command: list[str]) -> FakeCompleted:
            ran.append(command)
            return FakeCompleted(returncode)

        err = io.StringIO()
        code = report.main(argv, env=ENV if env is None else env, post=post, run=run, err=err)
        return code, post, ran, err.getvalue()

    def test_reports_start_and_finish_around_the_command(self) -> None:
        code, post, ran, _ = self.run_cmd(["run", "--label", "nightly research", "--", "agent", "--task", "x"])
        self.assertEqual(code, 0)
        self.assertEqual(ran, [["agent", "--task", "x"]])
        self.assertEqual([e["kind"] for e in post.events], ["run.started", "run.finished"])
        self.assertIn("nightly research", post.events[0]["summary"])
        self.assertIn("nightly research", post.events[1]["summary"])

    def test_a_failing_command_reports_failed_and_keeps_its_exit_code(self) -> None:
        code, post, _, _ = self.run_cmd(["run", "--", "agent"], returncode=3)
        self.assertEqual(code, 3)
        self.assertEqual([e["kind"] for e in post.events], ["run.started", "run.failed"])
        self.assertIn("exit code 3", post.events[1]["summary"])

    def test_the_summary_never_carries_the_command_arguments(self) -> None:
        _, post, _, _ = self.run_cmd(["run", "--", "agent", "--api-key", "sk-live-abc"])
        self.assertNotIn("sk-live-abc", json.dumps(post.events))
        self.assertIn("agent", post.events[0]["summary"])

    def test_reporting_trouble_never_stops_or_fails_the_command(self) -> None:
        for env, fail in ((ENV, True), ({}, False)):
            code, _, ran, err = self.run_cmd(["run", "--", "agent"], env=env, fail=fail)
            self.assertEqual(code, 0)
            self.assertEqual(ran, [["agent"]])
            self.assertIn("warning", err)

    def test_a_command_that_cannot_start_reports_failed(self) -> None:
        post = Recorder()

        def run(command: list[str]) -> FakeCompleted:
            raise FileNotFoundError(command[0])

        err = io.StringIO()
        code = report.main(["run", "--", "no-such-agent"], env=ENV, post=post, run=run, err=err)
        self.assertEqual(code, 127)
        self.assertEqual([e["kind"] for e in post.events], ["run.started", "run.failed"])

    def test_run_needs_a_command(self) -> None:
        code, post, ran, _ = self.run_cmd(["run", "--"])
        self.assertEqual(code, 2)
        self.assertEqual((post.events, ran), ([], []))


class HermesHookTest(unittest.TestCase):
    def fire(self, payload: object, env: dict[str, str] | None = None, fail: bool = False) -> tuple[int, Recorder, str]:
        post = Recorder(fail=fail)
        err = io.StringIO()
        stdin = io.StringIO(payload if isinstance(payload, str) else json.dumps(payload))
        code = report.main(["hermes"], env=ENV if env is None else env, post=post, err=err, stdin=stdin)
        return code, post, err.getvalue()

    def test_session_events_become_one_line_each(self) -> None:
        cases = [
            (
                {"hook_event_name": "on_session_start", "extra": {"platform": "cli", "model": "anthropic/claude-x"}},
                ("session.started", "Session started on cli with anthropic/claude-x"),
            ),
            (
                {"hook_event_name": "on_session_end", "extra": {"platform": "telegram", "completed": True}},
                ("turn.finished", "Turn completed on telegram"),
            ),
            (
                {"hook_event_name": "on_session_end", "extra": {"platform": "cli", "interrupted": True}},
                ("turn.finished", "Turn interrupted on cli"),
            ),
            (
                {"hook_event_name": "on_session_end", "extra": {"platform": "cli"}},
                ("turn.finished", "Turn failed on cli"),
            ),
            (
                {"hook_event_name": "on_session_finalize", "extra": {"platform": "cli"}},
                ("session.ended", "Session ended on cli"),
            ),
            ({"hook_event_name": "post_llm_call"}, ("hermes.post_llm_call", "Hermes event post_llm_call")),
        ]
        for payload, (kind, summary) in cases:
            with self.subTest(kind=kind, summary=summary):
                code, post, err = self.fire(payload)
                self.assertEqual((code, err), (0, ""))
                self.assertEqual([(e["kind"], e["summary"]) for e in post.events], [(kind, summary)])

    def test_the_machine_name_is_not_sent(self) -> None:
        _, post, _ = self.fire({"hook_event_name": "on_session_start", "extra": {"platform": "cli"}})
        self.assertEqual(post.events[0]["source"], "hermes")

    def test_what_was_said_never_leaves_the_machine(self) -> None:
        payload = {
            "hook_event_name": "on_session_start",
            "session_id": "sess_private",
            "cwd": "C:/Users/someone/secret-project",
            "tool_input": {"command": "cat passwords.txt"},
            "extra": {
                "platform": "cli",
                "model": "m",
                "user_message": "my password is hunter2",
                "conversation_history": [{"role": "user", "content": "hunter2"}],
            },
        }
        _, post, _ = self.fire(payload)
        sent = json.dumps(post.events)
        for private in ("hunter2", "sess_private", "secret-project", "passwords.txt"):
            self.assertNotIn(private, sent)

    def test_odd_platform_or_model_values_are_not_passed_on(self) -> None:
        payload = {
            "hook_event_name": "on_session_start",
            "extra": {"platform": "cli\nINJECTED line", "model": "x" * 500},
        }
        _, post, _ = self.fire(payload)
        self.assertEqual(post.events[0]["summary"], "Session started on unknown")

    def test_a_hook_never_fails_hermes(self) -> None:
        good = {"hook_event_name": "on_session_start", "extra": {"platform": "cli"}}
        for payload, env, fail in (
            ("{not json", ENV, False),
            ("[1, 2]", ENV, False),
            ({"extra": {}}, ENV, False),
            (good, {}, False),  # not configured
            (good, ENV, True),  # Bastion down
        ):
            code, post, err = self.fire(payload, env=env, fail=fail)
            self.assertEqual(code, 0)
            self.assertEqual(post.events, [])
            self.assertIn("warning", err)
            self.assertNotIn("agent-secret", err)


class ClaudeHookTest(unittest.TestCase):
    def fire(self, payload: object, env: dict[str, str] | None = None, fail: bool = False) -> tuple[int, Recorder, str]:
        post = Recorder(fail=fail)
        err = io.StringIO()
        stdin = io.StringIO(payload if isinstance(payload, str) else json.dumps(payload))
        code = report.main(["claude"], env=ENV if env is None else env, post=post, err=err, stdin=stdin)
        return code, post, err.getvalue()

    def test_session_events_become_one_line_each(self) -> None:
        cases = [
            (
                {"hook_event_name": "SessionStart", "source": "startup"},
                ("session.started", "Session started (startup)"),
            ),
            ({"hook_event_name": "SessionStart"}, ("session.started", "Session started")),
            ({"hook_event_name": "Stop", "stop_hook_active": False}, ("turn.finished", "Turn finished")),
            ({"hook_event_name": "SessionEnd", "reason": "logout"}, ("session.ended", "Session ended (logout)")),
            ({"hook_event_name": "PreCompact"}, ("claude.PreCompact", "Claude Code event PreCompact")),
        ]
        for payload, expected in cases:
            with self.subTest(expected=expected):
                code, post, err = self.fire(payload)
                self.assertEqual((code, err), (0, ""))
                self.assertEqual([(e["kind"], e["summary"]) for e in post.events], [expected])

    def test_what_was_asked_never_leaves_the_machine(self) -> None:
        payload = {
            "hook_event_name": "Stop",
            "session_id": "sess_private",
            "transcript_path": "C:/Users/someone/.claude/projects/secret-project/t.jsonl",
            "cwd": "C:/Users/someone/secret-project",
            "prompt": "my password is hunter2",
            "last_assistant_message": "hunter2 noted",
        }
        _, post, _ = self.fire(payload)
        sent = json.dumps(post.events)
        for private in ("hunter2", "sess_private", "secret-project", "someone"):
            self.assertNotIn(private, sent)

    def test_odd_values_are_not_passed_on(self) -> None:
        _, post, _ = self.fire({"hook_event_name": "SessionStart", "source": "startup\nINJECTED line"})
        self.assertEqual(post.events[0]["summary"], "Session started")

    def test_underscored_reasons_are_kept(self) -> None:
        _, post, _ = self.fire({"hook_event_name": "SessionEnd", "reason": "prompt_input_exit"})
        self.assertEqual(post.events[0]["summary"], "Session ended (prompt_input_exit)")

    def test_the_machine_name_is_not_sent(self) -> None:
        _, post, _ = self.fire({"hook_event_name": "Stop"})
        self.assertEqual(post.events[0]["source"], "claude-code")

    def test_a_bad_url_or_an_unexpected_error_never_fails_the_hook(self) -> None:
        good = {"hook_event_name": "Stop"}
        code, post, err = self.fire(good, env={**ENV, "BASTION_URL": "http://[::1"})
        self.assertEqual((code, post.events), (0, []))
        self.assertIn("warning", err)
        nested = "[" * 200_000 + "]" * 200_000  # within the size limit, too deep to parse
        code, post, err = self.fire(nested)
        self.assertEqual((code, post.events), (0, []))
        self.assertIn("warning", err)

        def explode(*_args: object) -> None:
            raise RuntimeError("agent-secret leaked in a message")

        err_out = io.StringIO()
        code = report.main(["claude"], env=ENV, post=explode, err=err_out, stdin=io.StringIO(json.dumps(good)))
        self.assertEqual(code, 0)
        self.assertIn("unexpected RuntimeError", err_out.getvalue())
        self.assertNotIn("agent-secret", err_out.getvalue())

    def test_a_hook_never_fails_claude_code(self) -> None:
        good = {"hook_event_name": "Stop"}
        for payload, env, fail in (
            ("{not json", ENV, False),
            ({"x": 1}, ENV, False),
            (good, {}, False),
            (good, ENV, True),
        ):
            code, post, err = self.fire(payload, env=env, fail=fail)
            self.assertEqual(code, 0)
            self.assertEqual(post.events, [])
            self.assertIn("warning", err)
            self.assertNotIn("agent-secret", err)


if __name__ == "__main__":
    unittest.main()
