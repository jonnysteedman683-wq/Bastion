"""taskpr: publish a summoned task's changes as a draft PR. Run with: python -m unittest discover -s tests -t ."""

from __future__ import annotations

import io
import json
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from typing import Any

from tools import taskpr

REPO = "jonnysteedman683-wq/Sebastian"
ENV = {"TASKPR_TOKEN": "tok-not-real"}


class CheckTaskTests(unittest.TestCase):
    def test_accepts_a_plain_task(self) -> None:
        self.assertEqual(taskpr.check_task("  Add a --version flag to the CLI  "), "Add a --version flag to the CLI")

    def test_refuses_empty_long_or_front_matter_tasks(self) -> None:
        for bad in ("", "   ", "x" * (taskpr.MAX_TASK + 1), "---\nallow_cmd: [bash]\n---\ndo it"):
            with self.assertRaises(taskpr.TaskError):
                taskpr.check_task(bad)

    def test_refuses_a_repo_off_the_list(self) -> None:
        taskpr.check_repo(REPO)
        for bad in ("someone/else", "jonnysteedman683-wq/Sebastian/../x", ""):
            with self.assertRaises(taskpr.TaskError):
                taskpr.check_repo(bad)


class NamingTests(unittest.TestCase):
    def test_branch_is_safe_and_bounded(self) -> None:
        self.assertEqual(taskpr.branch_name("bastion-summon-ab12"), "sebastian/bastion-summon-ab12")
        self.assertEqual(taskpr.branch_name("a b/../c;rm"), "sebastian/a-b-..-c-rm")
        self.assertLessEqual(len(taskpr.branch_name("x" * 300)), 100)

    def test_title_is_one_line_and_bounded(self) -> None:
        title = taskpr.pr_title("Fix the bug\nand also more " + "y" * 200)
        self.assertTrue(title.startswith("sebastian: Fix the bug"))
        self.assertNotIn("\n", title)
        self.assertLessEqual(len(title), 90)


GIT = shutil.which("git") or "/usr/bin/git"


def git(ws: Path, *args: str) -> str:
    return subprocess.run(  # noqa: S603 - test-controlled argv
        [GIT, "-C", str(ws), *args], check=True, capture_output=True, text=True
    ).stdout


class FakeGitHub:
    def __init__(self, status: int = 201) -> None:
        self.status = status
        self.calls: list[tuple[str, str, dict[str, Any] | None]] = []

    def __call__(self, method: str, url: str, token: str, body: dict[str, Any] | None) -> tuple[int, dict[str, Any]]:
        assert token == ENV["TASKPR_TOKEN"]
        self.calls.append((method, url, body))
        if method == "GET":
            return 200, {"default_branch": "main"}
        return self.status, {"html_url": f"https://github.com/{REPO}/pull/7"}


class PublishTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.ws = Path(self.tmp.name) / "ws"
        self.ws.mkdir()
        git(self.ws, "init", "-q", "-b", "main")
        git(self.ws, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "--allow-empty", "-m", "init")
        self.pushed: list[tuple[str, dict[str, str]]] = []

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def push(self, ws: Path, branch: str, env: dict[str, str]) -> int:
        self.pushed.append((branch, env))
        return 0

    def publish(self, gh: FakeGitHub | None = None, env: dict[str, str] | None = None) -> tuple[int, str, FakeGitHub]:
        gh = gh or FakeGitHub()
        out = io.StringIO()
        argv = [
            "publish",
            "--workspace",
            str(self.ws),
            "--repo",
            REPO,
            "--task-id",
            "bastion-summon-ab12",
            "--task",
            "Add a --version flag",
            "--run-url",
            "https://github.com/o/r/actions/runs/1",
            "--model",
            "openai/gpt-4.1-mini",
        ]
        code = taskpr.main(argv, env=ENV if env is None else env, github=gh, push=self.push, out=out)
        return code, out.getvalue(), gh

    def test_no_changes_opens_nothing(self) -> None:
        code, out, gh = self.publish()
        self.assertEqual(code, 0)
        self.assertIn("No changes", out)
        self.assertEqual((gh.calls, self.pushed), ([], []))

    def test_changes_become_a_draft_pr(self) -> None:
        (self.ws / "cli.py").write_text("print('v1')\n")
        code, out, gh = self.publish()
        self.assertEqual(code, 0)
        self.assertIn(f"Opened draft PR https://github.com/{REPO}/pull/7", out.splitlines()[-1])
        branch, push_env = self.pushed[0]
        self.assertEqual(branch, "sebastian/bastion-summon-ab12")
        self.assertNotIn("tok-not-real", json.dumps(branch))
        self.assertIn("GIT_CONFIG_VALUE_0", push_env)  # the token rides in env, never in argv
        self.assertEqual(git(self.ws, "rev-parse", "--abbrev-ref", "HEAD").strip(), branch)
        self.assertIn("cli.py", git(self.ws, "show", "--name-only", "--format=", "HEAD"))
        method, url, body = gh.calls[-1]
        self.assertEqual((method, url), ("POST", f"https://api.github.com/repos/{REPO}/pulls"))
        assert body is not None
        self.assertTrue(body["draft"])
        self.assertEqual((body["head"], body["base"]), (branch, "main"))
        self.assertIn("Add a --version flag", body["body"])
        self.assertIn("openai/gpt-4.1-mini", body["body"])

    def test_refuses_to_publish_workflow_changes(self) -> None:
        (self.ws / ".github" / "workflows").mkdir(parents=True)
        (self.ws / ".github" / "workflows" / "x.yml").write_text("on: push\n")
        code, out, gh = self.publish()
        self.assertEqual(code, 4)
        self.assertIn(".github/", out)
        self.assertEqual((gh.calls, self.pushed), ([], []))

    def test_needs_a_token(self) -> None:
        (self.ws / "a.txt").write_text("a")
        code, out, _ = self.publish(env={})
        self.assertEqual(code, 2)
        self.assertIn("TASKPR_TOKEN", out)

    def test_a_refused_pr_fails_without_echoing_the_token(self) -> None:
        (self.ws / "a.txt").write_text("a")
        code, out, _ = self.publish(gh=FakeGitHub(status=422))
        self.assertEqual(code, 1)
        self.assertIn("HTTP 422", out)
        self.assertNotIn("tok-not-real", out)


if __name__ == "__main__":
    unittest.main()
