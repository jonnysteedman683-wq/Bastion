"""PR steward triage. Run with: python -m unittest discover -s tests -t .  (no network)"""

from __future__ import annotations

import io
import json
import tempfile
import unittest
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from tools import steward

NOW = datetime(2026, 11, 5, 12, 0, tzinfo=UTC)
FRESH = "2026-11-04T10:00:00Z"
OLD = "2026-09-01T10:00:00Z"


def pr(**kw: Any) -> steward.PR:
    base: dict[str, Any] = {
        "repo": "o/Repo",
        "number": 1,
        "title": "feat: thing",
        "url": "https://github.com/o/Repo/pull/1",
        "author": "o",
        "branch": "feature",
        "base": "main",
        "head_sha": "abcdef1234567",
        "draft": False,
        "mergeable_state": "clean",
        "updated_at": FRESH,
        "checks": {"test": "pass"},
    }
    base.update(kw)
    return steward.PR(**base)


def bucket(p: steward.PR) -> str:
    steward.classify(p, NOW)
    return p.bucket


class ClassifyTest(unittest.TestCase):
    def test_green_human_pr_needs_review(self) -> None:
        self.assertEqual(bucket(pr()), "review")

    def test_draft_wins(self) -> None:
        self.assertEqual(bucket(pr(draft=True, mergeable_state="dirty", checks={"t": "fail"})), "draft")

    def test_conflict_before_ci(self) -> None:
        p = pr(mergeable_state="dirty", checks={"t": "fail"})
        self.assertEqual(bucket(p), "conflict")
        self.assertIn("merge main", p.note)

    def test_own_failure_is_red_and_named(self) -> None:
        p = pr(checks={"lint": "fail", "test": "pass"})
        self.assertEqual(bucket(p), "ci-red")
        self.assertEqual(p.note, "failing: lint")

    def test_failure_also_on_base_is_not_this_prs(self) -> None:
        p = pr(checks={"Vercel": "fail", "test": "pass"}, inherited=["Vercel"])
        self.assertEqual(bucket(p), "review")
        self.assertIn("also red on main: Vercel", p.note)

    def test_pending_and_no_ci(self) -> None:
        self.assertEqual(bucket(pr(checks={"t": "pending"})), "ci-pending")
        self.assertEqual(bucket(pr(checks={})), "no-ci")

    def test_dependabot_minor_is_merge_ready_major_is_decide(self) -> None:
        minor = pr(author="dependabot[bot]", title="bump qs from 6.15.3 to 6.16.2")
        major = pr(author="dependabot[bot]", title="build(deps): bump isolated-vm from 6.2.0 to 7.0.1")
        grouped = pr(author="dependabot[bot]", title="bump the npm_and_yarn group with 5 updates")
        self.assertEqual(bucket(minor), "merge-ready")
        self.assertEqual(bucket(major), "decide")
        self.assertEqual(bucket(grouped), "review")

    def test_bot_minor_with_unknown_mergeability_is_not_merge_ready(self) -> None:
        p = pr(author="dependabot[bot]", title="bump qs from 6.1.0 to 6.2.0", mergeable_state="unknown")
        self.assertEqual(bucket(p), "review")

    def test_stale_flag(self) -> None:
        p = pr(updated_at=OLD)
        steward.classify(p, NOW)
        self.assertEqual(p.flags, ["stale"])


class TriageTest(unittest.TestCase):
    def test_duplicates_by_backlog_item_keep_the_newest(self) -> None:
        old = pr(number=16, branch="agent/L7-fix-a", updated_at="2026-11-03T00:00:00Z")
        new = pr(number=17, branch="agent/L7-fix-b", updated_at="2026-11-04T00:00:00Z")
        other = pr(number=18, branch="agent/L8-other")
        steward.triage([old, new, other], NOW)
        self.assertEqual(old.flags, ["duplicate"])
        self.assertIn("same item as #17", old.note)
        self.assertEqual(new.flags, [])
        self.assertEqual(other.flags, [])

    def test_item_ids_are_per_repo(self) -> None:
        a = pr(repo="o/A", branch="agent/H1-x")
        b = pr(repo="o/B", branch="agent/H1-y")
        steward.triage([a, b], NOW)
        self.assertEqual(a.flags + b.flags, [])

    def test_sorted_most_urgent_first(self) -> None:
        prs = steward.triage([pr(number=1, draft=True), pr(number=2, checks={"t": "fail"}), pr(number=3)], NOW)
        self.assertEqual([p.bucket for p in prs], ["ci-red", "review", "draft"])

    def test_floods_and_summary(self) -> None:
        prs = steward.triage([pr(number=i) for i in range(5)] + [pr(repo="o/Other", number=9, draft=True)], NOW)
        self.assertEqual(steward.floods(prs), {"o/Repo": 5})
        self.assertEqual(steward.summary_line(prs), "6 open PRs: 5 review, 1 draft")

    def test_markdown_escapes_pipes_and_reports_floods(self) -> None:
        prs = steward.triage([pr(number=i, title="a | b") for i in range(5)], NOW)
        md = steward.markdown(prs, NOW)
        self.assertIn("**Flood:** o/Repo has 5 open PRs", md)
        self.assertIn("a \\| b", md)
        self.assertIn("[Repo #0](https://github.com/o/Repo/pull/1)", md)


class FakeGitHub:
    """Serves canned REST responses by path and records every request."""

    def __init__(self) -> None:
        self.calls: list[str] = []
        self.routes: dict[str, Any] = {
            "/search/issues": {
                "total_count": 2,
                "items": [
                    {"repository_url": "https://api.github.com/repos/o/Repo", "number": 1},
                    {"repository_url": "https://api.github.com/repos/o/Lib", "number": 7},
                ],
            },
            "/repos/o/Repo/pulls/1": self._pull("o/Repo", 1, "aaa", "o"),
            "/repos/o/Lib/pulls/7": self._pull("o/Lib", 7, "bbb", "dependabot[bot]", "bump x from 1.0.0 to 2.0.0"),
            "/repos/o/Repo/commits/aaa/check-runs": {
                "check_runs": [{"name": "test", "status": "completed", "conclusion": "success"}]
            },
            "/repos/o/Repo/commits/aaa/status": {"statuses": [{"context": "Vercel", "state": "failure"}]},
            "/repos/o/Repo/commits/main/check-runs": {"check_runs": []},
            "/repos/o/Repo/commits/main/status": {"statuses": [{"context": "Vercel", "state": "failure"}]},
            "/repos/o/Lib/commits/bbb/check-runs": {
                "check_runs": [{"name": "ci", "status": "completed", "conclusion": "success"}]
            },
            "/repos/o/Lib/commits/bbb/status": {"statuses": []},
        }

    @staticmethod
    def _pull(repo: str, n: int, sha: str, author: str, title: str = "feat: x") -> dict[str, Any]:
        return {
            "title": title,
            "html_url": f"https://github.com/{repo}/pull/{n}",
            "user": {"login": author},
            "head": {"ref": "feature", "sha": sha},
            "base": {"ref": "main"},
            "draft": False,
            "mergeable_state": "clean",
            "updated_at": FRESH,
        }

    def __call__(self, path: str) -> Any:
        self.calls.append(path)
        return self.routes[path.split("?", 1)[0]]


class ScanTest(unittest.TestCase):
    def test_scan_reads_prs_checks_and_base_only_when_failing(self) -> None:
        gh = FakeGitHub()
        prs = steward.scan(gh, "o", NOW)
        by_ref = {p.ref: p for p in prs}
        self.assertEqual(by_ref["Repo #1"].bucket, "review")
        self.assertEqual(by_ref["Repo #1"].inherited, ["Vercel"])
        self.assertEqual(by_ref["Lib #7"].bucket, "decide")
        self.assertIn("/repos/o/Repo/commits/main/status", gh.calls)
        self.assertNotIn("/repos/o/Lib/commits/main/status", gh.calls)
        self.assertIn("q=is%3Apr%20is%3Aopen%20user%3Ao", gh.calls[0])

    def test_search_pages_until_total(self) -> None:
        pages = {
            1: {"total_count": 101, "items": [{"repository_url": "x/repos/o/R", "number": i} for i in range(100)]},
            2: {"total_count": 101, "items": [{"repository_url": "x/repos/o/R", "number": 100}]},
        }
        found = steward.search_open_prs(lambda p: pages[int(p.rsplit("page=", 1)[1])], "o")
        self.assertEqual(len(found), 101)


class FakeEntry:
    def __init__(self, sender: str, kind: str, task_id: str) -> None:
        self.sender, self.kind, self.task_id = sender, kind, task_id


class FakeLedger:
    def __init__(self, entries: list[FakeEntry] | None = None) -> None:
        self.entries = entries or []
        self.posts: list[tuple[str, str, str, dict[str, Any]]] = []
        self.synced = 0

    def sync(self) -> int:
        self.synced += 1
        return 0

    def post(self, sender: str, kind: str, task_id: str = "", **body: Any) -> None:
        self.posts.append((sender, kind, task_id, body))


class PostTest(unittest.TestCase):
    def prs(self) -> list[steward.PR]:
        return steward.triage(
            [
                pr(number=1, checks={"t": "fail"}),  # ci-red: escalate
                pr(number=2),  # review: no escalation
                pr(number=3, updated_at=OLD),  # stale review: escalate
            ],
            NOW,
        )

    def test_heartbeat_plus_one_escalation_per_pr_needing_the_owner(self) -> None:
        ledger = FakeLedger()
        escalated, skipped = steward.post(ledger, self.prs(), "https://run")
        self.assertEqual((escalated, skipped), (2, 0))
        kinds = [(k, t) for _, k, t, _ in ledger.posts]
        self.assertEqual(kinds[-1], ("heartbeat", ""))
        self.assertEqual([t for k, t in kinds if k == "escalate"], ["steward/Repo#1@abcdef1", "steward/Repo#3@abcdef1"])
        esc = ledger.posts[0][3]
        self.assertTrue(esc["reason"].startswith("Repo #1 needs you (ci-red): failing: t"))
        self.assertEqual(esc["summary"], esc["reason"])
        beat = ledger.posts[-1][3]
        self.assertEqual(beat["summary"], "3 open PRs: 1 ci-red, 2 review")
        self.assertEqual(beat["flow"], "pr-steward")
        self.assertTrue(all(p[0] == "steward" for p in ledger.posts))

    def test_already_escalated_head_is_skipped(self) -> None:
        ledger = FakeLedger([FakeEntry("steward", "escalate", "steward/Repo#1@abcdef1")])
        escalated, skipped = steward.post(ledger, self.prs(), "")
        self.assertEqual((escalated, skipped), (1, 1))

    def test_escalations_are_capped(self) -> None:
        many = steward.triage([pr(number=i, checks={"t": "fail"}) for i in range(15)], NOW)
        ledger = FakeLedger()
        escalated, _ = steward.post(ledger, many, "")
        self.assertEqual(escalated, steward.MAX_ESCALATIONS)
        self.assertEqual(ledger.posts[-1][1], "heartbeat")


class CliTest(unittest.TestCase):
    def run_cli(self, *argv: str, env: dict[str, str], ledger: FakeLedger | None = None) -> tuple[int, str]:
        out = io.StringIO()
        code = steward.main(
            list(argv),
            env=env,
            make_get=lambda token: FakeGitHub(),
            open_ledger=lambda: ledger,
            now=NOW,
            out=out,
        )
        return code, out.getvalue()

    def test_needs_a_github_token(self) -> None:
        code, out = self.run_cli("scan", "--owner", "o", env={})
        self.assertEqual(code, 2)
        self.assertIn("Set STEWARD_GITHUB_TOKEN", out)

    def test_scan_writes_summary_and_json_without_posting(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            md, js = Path(d) / "s.md", Path(d) / "s.json"
            code, out = self.run_cli(
                "scan", "--owner", "o", "--summary", str(md), "--json", str(js), env={"STEWARD_GITHUB_TOKEN": "t"}
            )
            self.assertEqual(code, 0)
            self.assertIn("### PR steward: 2 open PRs: 1 decide, 1 review", md.read_text())
            self.assertEqual({p["bucket"] for p in json.loads(js.read_text())}, {"decide", "review"})
        self.assertIn("2 open PRs", out)
        self.assertNotIn("posted", out)

    def test_post_flag_posts(self) -> None:
        ledger = FakeLedger()
        code, out = self.run_cli("scan", "--owner", "o", "--post", env={"STEWARD_GITHUB_TOKEN": "t"}, ledger=ledger)
        self.assertEqual(code, 0)
        self.assertIn("posted heartbeat; 1 escalated, 0 already escalated", out)
        self.assertEqual([k for _, k, _, _ in ledger.posts], ["escalate", "heartbeat"])


if __name__ == "__main__":
    unittest.main()
