"""Agent-A sentry patrol. Run with: python -m unittest discover -s tests -t .  (no network)"""

from __future__ import annotations

import dataclasses
import io
import json
import tempfile
import unittest
import urllib.error
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from tools import agent_a

NOW = datetime(2026, 11, 5, 12, 0, tzinfo=UTC)
SECRET = "ghp_AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"


def pr(**kw: Any) -> agent_a.PR:
    base: dict[str, Any] = {
        "repo": "o/Repo",
        "number": 1,
        "title": "feat: thing",
        "url": "https://github.com/o/Repo/pull/1",
        "author": "o",
        "branch": "feature",
        "base": "main",
        "head_sha": "abcdef1234567",
        "updated_at": "2026-11-04T10:00:00Z",
    }
    base.update(kw)
    return agent_a.PR(**base)


def finding(
    kind: str = "secret", severity: str = "critical", path: str = "a.py", line: int = 1, hint: str = "x"
) -> agent_a.Finding:
    return agent_a.Finding(kind, severity, path, line, hint)


def file_of(filename: str, patch: str) -> dict[str, str]:
    return {"filename": filename, "patch": patch}


class Entry:
    """A ledger entry whose kind may be a real enum-style object or a plain string."""

    def __init__(self, sender: str, kind: str, enum: bool = True) -> None:
        self.sender = sender
        self.kind = type("K", (), {"value": kind})() if enum else kind
        self.task_id = f"t/{sender}/{kind}"
        self.seq = 0


class GuardedTest(unittest.TestCase):
    def test_never_list_matches_agents_md(self) -> None:
        self.assertEqual(
            agent_a.GUARDED,
            (".github/", "deploy/policy.json", "infra/", "tools/quartermaster/", "AGENTS.md", "CLAUDE.md"),
        )

    def test_guarded_paths_are_caught(self) -> None:
        for path in (
            ".github/workflows/ci.yml",
            "deploy/policy.json",
            "infra/gcp/bootstrap.sh",
            "tools/quartermaster/validate.py",
            "AGENTS.md",
            "CLAUDE.md",
        ):
            self.assertTrue(agent_a.is_guarded(path), path)

    def test_unguarded_files_pass(self) -> None:
        for path in ("tools/agent_a/__init__.py", "services/hello/main.py", "deploy/healthchecks.json", "docs/x.md"):
            self.assertFalse(agent_a.is_guarded(path), path)

    def test_guarded_by_names_the_hit(self) -> None:
        self.assertEqual(agent_a.guarded_by(".github/workflows/ci.yml"), ".github/")


class PatchTest(unittest.TestCase):
    def test_secret_on_an_added_line_is_found(self) -> None:
        p = pr()
        agent_a.inspect_file(
            p,
            "src/app.py",
            '@@ -1,3 +1,4 @@\n context\n-removed\n+token = "' + SECRET + '"\n+ok()',
        )
        self.assertEqual([(f.kind, f.severity) for f in p.findings], [("secret", "critical")])
        f = p.findings[0]
        self.assertEqual(f.path, "src/app.py")
        self.assertEqual(f.line, 2)

    def test_deleted_and_context_lines_are_not_scanned(self) -> None:
        p = pr()
        agent_a.inspect_file(
            p,
            "src/app.py",
            '@@ -1,3 +1,2 @@\n old = "' + SECRET + '"\n-good = "' + SECRET + '"\n+clean()',
        )
        self.assertEqual(p.findings, [])

    def test_secret_value_never_reaches_a_finding(self) -> None:
        p = pr()
        agent_a.inspect_file(p, "x.txt", "@@ -1 +1,2 @@\n+leak " + SECRET)
        blob = json.dumps([dataclasses.asdict(f) for f in p.findings])
        self.assertNotIn(SECRET, blob)
        self.assertIn("github-token", blob)

    def test_line_number_may_omit_the_count(self) -> None:
        p = pr()
        agent_a.inspect_file(p, "k.txt", "@@ -7 +7 @@\n+-----BEGIN RSA PRIVATE KEY-----")
        self.assertEqual(p.findings[0].kind, "secret")
        self.assertEqual(p.findings[0].line, 7)

    def test_guarded_edit_is_high_and_secret_wins_on_severity(self) -> None:
        p = pr()
        agent_a.inspect_file(p, ".github/workflows/ci.yml", "@@ -1 +1,2 @@\n+runs-on: ubuntu-latest")
        self.assertEqual([(f.kind, f.severity) for f in p.findings], [("guarded", "high")])
        agent_a.inspect_file(p, ".github/workflows/x.yml", '@@ -1 +1,2 @@\n+key = "' + SECRET + '"')
        self.assertEqual(p.findings[-1].severity, "critical")
        self.assertEqual(p.worst, "critical")


def fake_get() -> Any:
    """The GitHub API for a risky PR and a clean one."""
    data = {
        "/search/issues?q=is%3Apr%20is%3Aopen%20user%3Ao&per_page=100&page=1": {
            "total_count": 2,
            "items": [
                {"number": 1, "repository_url": "https://api.github.com/repos/o/Repo"},
                {"number": 2, "repository_url": "https://api.github.com/repos/o/Clean"},
            ],
        },
        "/repos/o/Repo/pulls/1": {
            "title": "add key",
            "html_url": "https://github.com/o/Repo/pull/1",
            "user": {"login": "runaway-bot"},
            "head": {"ref": "feat", "sha": "abc1234"},
            "base": {"ref": "main"},
            "updated_at": "2026-11-04T10:00:00Z",
        },
        "/repos/o/Repo/pulls/1/files?per_page=100&page=1": [
            file_of("src/app.py", '@@ -1 +1,2 @@\n+token = "' + SECRET + '"'),
            file_of("deploy/policy.json", '@@ -1 +1 @@\n-"instances": 2\n+"instances": 9'),
        ],
        "/repos/o/Clean/pulls/2": {
            "title": "docs",
            "html_url": "https://github.com/o/Clean/pull/2",
            "user": {"login": "o"},
            "head": {"ref": "docs", "sha": "def5678"},
            "base": {"ref": "main"},
            "updated_at": "2026-11-04T10:00:00Z",
        },
        "/repos/o/Clean/pulls/2/files?per_page=100&page=1": [file_of("README.md", "@@ -1 +1,2 @@\n+hello")],
    }

    def get(path: str) -> Any:
        return data[path]

    return get


class ScanTest(unittest.TestCase):
    def test_scan_sorts_risky_first(self) -> None:
        prs = agent_a.scan(fake_get(), "o")
        self.assertEqual([p.ref for p in prs], ["Repo #1", "Clean #2"])
        self.assertEqual(prs[0].worst, "critical")
        self.assertEqual(prs[0].author, "runaway-bot")
        self.assertEqual({f.path for f in prs[0].findings}, {"src/app.py", "deploy/policy.json"})
        self.assertEqual([f.kind for f in prs[0].findings], ["secret", "guarded"])
        self.assertEqual(prs[1].findings, [])

    def test_a_vanished_pr_does_not_stop_the_patrol(self) -> None:
        def get(path: str) -> Any:
            if "/pulls/1/files" in path:
                raise urllib.error.HTTPError(path, 404, "gone", None, io.BytesIO(b""))
            if path.startswith("/search/"):
                return {
                    "total_count": 1,
                    "items": [{"number": 1, "repository_url": "https://api.github.com/repos/o/R"}],
                }
            return {
                "title": "t",
                "html_url": "u",
                "user": {"login": "a"},
                "head": {"ref": "b", "sha": "s"},
                "base": {"ref": "m"},
                "updated_at": "z",
            }

        self.assertEqual(agent_a.scan(get, "o"), [])


class ReportTest(unittest.TestCase):
    def test_summary_and_markdown_name_kinds_not_values(self) -> None:
        red = finding(hint="matches github-token; rotate the credential", path="src/a.py", line=3)
        md = agent_a.markdown([pr(findings=[red]), pr(number=2, repo="o/Clean")], [], NOW)
        self.assertIn("### Agent-A sentry: 1 findings on 1 PRs: 1 critical", md)
        self.assertIn("src/a.py:3", md)
        self.assertIn("1 open PR(s) clean.", md)
        self.assertNotIn("ghp_", md)

    def test_perimeter_clean(self) -> None:
        self.assertEqual(agent_a.summary_line([pr()]), "perimeter clean")
        self.assertIn("perimeter clean", agent_a.markdown([pr()], [], NOW))

    def test_streaks_go_into_the_report(self) -> None:
        md = agent_a.markdown([], ["sebastian failed 3 outcomes in a row"], NOW)
        self.assertIn("- sebastian failed 3 outcomes in a row", md)


class StreakTest(unittest.TestCase):
    def test_three_failures_in_a_row_flag_the_agent(self) -> None:
        entries = [
            Entry("sebastian", "task.request"),
            Entry("sebastian", "task.failed"),
            Entry("sebastian", "task.failed"),
            Entry("other", "task.result"),
            Entry("sebastian", "task.failed"),
        ]
        found = agent_a.streak_findings(entries, NOW)
        self.assertEqual(len(found), 1)
        task_id, body = found[0]
        self.assertEqual(task_id, "agent-a/streak/sebastian@20261105")
        self.assertIn("sebastian failed 3 outcomes in a row", body["reason"])
        self.assertEqual(body["flow"], agent_a.FLOW)

    def test_a_result_resets_the_streak(self) -> None:
        entries = [
            Entry("s", "task.failed"),
            Entry("s", "task.failed"),
            Entry("s", "task.failed"),
            Entry("s", "task.result"),
            Entry("s", "task.failed"),
            Entry("s", "task.failed"),
        ]
        self.assertEqual(agent_a.streak_findings(entries, NOW), [])

    def test_plain_string_kinds_are_accepted_too(self) -> None:
        entries = [Entry("s", "run.failed", enum=False) for _ in range(3)]
        self.assertEqual(len(agent_a.streak_findings(entries, NOW)), 1)

    def test_entries_without_a_sender_are_ignored(self) -> None:
        entries = [Entry("", "task.failed") for _ in range(3)]
        self.assertEqual(agent_a.streak_findings(entries, NOW), [])


class FakeLedger:
    def __init__(self, entries: list[Any]) -> None:
        self.ledger = type("V", (), {"entries": entries})()
        self.posted: list[tuple[tuple[Any, ...], dict[str, Any]]] = []

    def sync(self) -> None:
        return None

    def post(self, *args: Any, **kw: Any) -> None:
        self.posted.append((args, kw))


class PostTest(unittest.TestCase):
    def risky(self, sha: str, n: int) -> agent_a.PR:
        return pr(number=n, head_sha=sha, findings=[finding()])

    def test_escalations_are_deduped_and_capped(self) -> None:
        prs = [self.risky(f"sha{n}", n) for n in range(1, 13)]
        seen = agent_a.AGENT + "/Repo#1@sha1"
        ledger = FakeLedger([Entry(agent_a.AGENT, "escalate")])
        ledger.ledger.entries[0].task_id = seen
        posted, streaks_posted, skipped = agent_a.post(ledger, prs, NOW, "u")
        self.assertEqual(skipped, 1)
        self.assertEqual(len(posted), agent_a.MAX_ESCALATIONS - 1)
        self.assertEqual(streaks_posted, [])
        kinds = {args[1] for args, _ in ledger.posted}
        self.assertEqual(kinds, {"escalate", "heartbeat"})

    def test_streak_failure_becomes_one_daily_escalation(self) -> None:
        entries = [Entry("sebastian", "task.failed") for _ in range(3)]
        ledger = FakeLedger(entries)
        posted, streaks_posted, _ = agent_a.post(ledger, [], NOW, "u")
        self.assertEqual(len(posted), 1)
        self.assertEqual(posted[0][0], "agent-a/streak/sebastian@20261105")
        self.assertEqual(streaks_posted, [posted[0][1]["reason"]])
        heartbeat = next(kw for args, kw in ledger.posted if args[1] == "heartbeat")
        self.assertEqual(heartbeat["summary"], "perimeter clean")
        self.assertIn("agent-a/streak/sebastian@20261105", heartbeat["streaks"])

    def test_a_posted_streak_is_not_posted_again_the_same_day(self) -> None:
        entries = [Entry("s", "task.failed") for _ in range(3)]
        seen = agent_a.AGENT + "/streak/s@20261105"
        ledger = FakeLedger(entries)
        ledger.ledger.entries.append(Entry(agent_a.AGENT, "escalate"))
        ledger.ledger.entries[-1].task_id = seen
        posted, _, _ = agent_a.post(ledger, [], NOW, "u")
        self.assertEqual(posted, [])

    def test_heartbeat_body_carries_sorted_findings(self) -> None:
        low = finding(kind="guarded", severity="high", path="AGENTS.md", line=0, hint="y")
        red = finding(severity="critical", path="a.py", line=1, hint="x")
        body = agent_a.heartbeat_body([pr(findings=[low, red])], [], "u")
        self.assertEqual([i["severity"] for i in body["items"]], ["critical", "high"])
        self.assertEqual(body["run_url"], "u")

    def test_heartbeat_items_are_capped(self) -> None:
        prs = [pr(number=n, findings=[finding()]) for n in range(agent_a.MAX_ITEMS + 5)]
        body = agent_a.heartbeat_body(prs, [], "u")
        self.assertEqual(len(body["items"]), agent_a.MAX_ITEMS)
        self.assertEqual(body["truncated"], 5)


class NotifyTest(unittest.TestCase):
    def test_only_critical_findings_buzz_and_values_stay_hidden(self) -> None:
        red = finding(hint="matches github-token; rotate", path="src/a.py", line=3)
        low = finding(kind="guarded", severity="high", path="AGENTS.md", line=0, hint="y")
        sent: list[list[str]] = []

        def send(argv: Any, env: Any = None, out: Any = None) -> int:
            sent.append(list(argv))
            return 0

        code = agent_a.warn_phone(
            [pr(findings=[red, low]), pr(number=2, findings=[finding(path="b.py")])],
            {},
            send,
            io.StringIO(),
        )
        self.assertEqual(code, 2)
        for call in sent:
            self.assertNotIn(SECRET, " ".join(call))
            self.assertIn("urgent", call)
            self.assertIn("--click", call)

    def test_cap_of_three_phone_alerts(self) -> None:
        red = finding(severity="critical")
        calls: list[list[str]] = []

        def send(argv: Any, env: Any = None, out: Any = None) -> int:
            calls.append(list(argv))
            return 0

        code = agent_a.warn_phone([pr(number=n, findings=[red]) for n in range(1, 6)], {}, send, io.StringIO())
        self.assertEqual(code, 3)


class MainTest(unittest.TestCase):
    def test_missing_token_exits_2(self) -> None:
        self.assertEqual(agent_a.main(["patrol", "--owner", "o"], env={}, out=io.StringIO()), 2)

    def test_missing_ledger_env_exits_2_before_opening_the_ledger(self) -> None:
        def boom() -> None:
            raise AssertionError("ledger must not be opened")

        code = agent_a.main(
            ["patrol", "--owner", "o", "--post"],
            env={agent_a.GITHUB_TOKEN_ENV: "t"},
            make_get=lambda t: fake_get(),
            open_ledger=boom,
            out=io.StringIO(),
        )
        self.assertEqual(code, 2)

    def test_github_failure_exits_1(self) -> None:
        def get(path: str) -> Any:
            raise urllib.error.HTTPError(path, 401, "bad token", None, io.BytesIO(b""))

        code = agent_a.main(
            ["patrol", "--owner", "o"],
            env={agent_a.GITHUB_TOKEN_ENV: "t"},
            make_get=lambda t: get,
            out=io.StringIO(),
        )
        self.assertEqual(code, 1)

    def test_happy_path_writes_report_and_posts(self) -> None:
        entries = [Entry("s", "task.failed") for _ in range(3)]
        ledger = FakeLedger(entries)

        def get(path: str) -> Any:
            if path.startswith("/search/"):
                return {
                    "total_count": 1,
                    "items": [{"number": 1, "repository_url": "https://api.github.com/repos/o/R"}],
                }
            if "/files" in path:
                return [file_of("src/a.py", '@@ -1 +1,2 @@\n+token = "' + SECRET + '"')]
            return {
                "title": "t",
                "html_url": "u",
                "user": {"login": "runaway-bot"},
                "head": {"ref": "b", "sha": "abc1234"},
                "base": {"ref": "m"},
                "updated_at": "z",
            }

        with tempfile.TemporaryDirectory() as d:
            summary = Path(d) / "summary.md"
            code = agent_a.main(
                ["patrol", "--owner", "o", "--summary", str(summary), "--post", "--run-url", "https://run"],
                env={agent_a.GITHUB_TOKEN_ENV: "t", agent_a.GATEWAY_ENV: "https://gw", agent_a.LEDGER_TOKEN_ENV: "k"},
                make_get=lambda t: get,
                open_ledger=lambda: ledger,
                send=lambda argv, env=None, out=None: 0,
                now=NOW,
                out=io.StringIO(),
            )
            self.assertEqual(code, 0)
            text = summary.read_text(encoding="utf-8")
            self.assertIn("### Agent-A sentry:", text)
            self.assertIn("s failed 3 outcomes in a row", text)
            self.assertNotIn(SECRET, text)
            self.assertEqual([args[1] for args, _ in ledger.posted], ["escalate", "escalate", "heartbeat"])


if __name__ == "__main__":
    unittest.main()
