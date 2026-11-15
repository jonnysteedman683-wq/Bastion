"""taskpr: publish a summoned Sebastian task as a draft pull request (backlog S4).

    python -m tools.taskpr check --task "Add a --version flag" --repo jonnysteedman683-wq/Sebastian
    python -m tools.taskpr publish --workspace ws --repo OWNER/NAME --task-id ID --task TEXT \
        --run-url URL --model MODEL

``check`` refuses a task or repository before anything runs. ``publish`` runs after Sebastian has finished: when the
workspace has changes it commits them to ``sebastian/<task id>``, pushes, and opens a **draft** PR against the
repository's default branch. It refuses changes under ``.github/``: a summoned task never edits workflows.

The push and the PR use ``TASKPR_TOKEN`` (a fine-grained token: Contents and Pull requests write on the listed
repositories, no Workflows permission). It is read only here, after the agent has exited, so the agent never sees
it; it is passed to git through the environment, never on a command line, and never printed.

Exit codes: 0 done (or nothing to publish), 1 git or GitHub failed, 2 bad input or configuration, 4 refused.

Standard library only.
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import re
import shutil
import subprocess
import sys
import urllib.error
import urllib.request
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Any, TextIO

OWNER = "jonnysteedman683-wq"
# Repositories a summoned task may change. Add one here (and to summon-task.yml's choice list) to allow it.
REPOS = frozenset(
    f"{OWNER}/{name}" for name in ("Sebastian", "Bastion", "LESTINXOR-AGENT", "O0", "Omniordial", "Sentinel")
)
MAX_TASK = 2000
MAX_BRANCH = 100
MAX_TITLE = 90
API = "https://api.github.com"
TIMEOUT_SECONDS = 20.0
GIT = shutil.which("git") or "/usr/bin/git"
AUTHOR = ("Sebastian (via Bastion)", "sebastian@users.noreply.github.com")

GitHub = Callable[[str, str, str, dict[str, Any] | None], tuple[int, dict[str, Any]]]
Push = Callable[[Path, str, dict[str, str]], int]


class TaskError(ValueError):
    """A task, repository or configuration that must not run. Never contains the token."""


def check_task(text: str) -> str:
    task = text.strip()
    if not task:
        raise TaskError("the task is empty")
    if len(task) > MAX_TASK:
        raise TaskError(f"the task is over {MAX_TASK} characters")
    if task.startswith("---"):
        raise TaskError("the task may not start with '---' (front matter would set Sebastian's options)")
    return task


def check_repo(repo: str) -> str:
    if repo not in REPOS:
        raise TaskError(f"{repo or 'no repository'} is not on the task list in tools/taskpr")
    return repo


def branch_name(task_id: str) -> str:
    safe = re.sub(r"[^A-Za-z0-9._-]+", "-", task_id).strip("-.") or "task"
    return f"sebastian/{safe}"[:MAX_BRANCH].rstrip("-.")


def pr_title(task: str) -> str:
    first = " ".join(task.split())
    title = f"sebastian: {first}"
    return title if len(title) <= MAX_TITLE else title[: MAX_TITLE - 1].rstrip() + "…"


def pr_body(task: str, run_url: str, model: str, changed: list[str]) -> str:
    files = "\n".join(f"- `{path}`" for path in changed[:50])
    more = f"\n- …and {len(changed) - 50} more" if len(changed) > 50 else ""
    quoted = "\n".join(f"> {line}" for line in task.splitlines())
    return (
        f"Summoned from Bastion. Sebastian ran this task with `{model}` on GitHub Models.\n\n"
        f"## Task\n\n{quoted}\n\n## Files changed\n\n{files}{more}\n\n"
        f"[Run log]({run_url}). This is a draft: review it before marking it ready."
    )


def _git(ws: Path, *args: str) -> str:
    done = subprocess.run([GIT, "-C", str(ws), *args], check=True, capture_output=True, text=True)  # noqa: S603 - fixed argv, no shell
    return done.stdout


def changed_paths(ws: Path) -> list[str]:
    paths = []
    for line in _git(ws, "status", "--porcelain", "--untracked-files=all", "-z").split("\0"):
        if len(line) > 3:
            paths.append(line[3:])
    return sorted(set(paths))


def github_api(method: str, url: str, token: str, body: dict[str, Any] | None) -> tuple[int, dict[str, Any]]:
    data = None if body is None else json.dumps(body).encode()
    req = urllib.request.Request(url, data=data, method=method)  # noqa: S310 - callers pass the fixed https API
    req.add_header("Authorization", f"Bearer {token}")
    req.add_header("Accept", "application/vnd.github+json")
    req.add_header("X-GitHub-Api-Version", "2022-11-28")
    if data is not None:
        req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT_SECONDS) as resp:  # noqa: S310 - fixed https API host
            return resp.status, json.loads(resp.read() or b"{}")
    except urllib.error.HTTPError as exc:
        return exc.code, {}


def git_push(ws: Path, branch: str, env: dict[str, str]) -> int:
    done = subprocess.run(  # noqa: S603 - fixed argv, no shell
        [GIT, "-C", str(ws), "push", "--quiet", "origin", f"HEAD:refs/heads/{branch}"],
        env={**os.environ, **env},
        capture_output=True,
        text=True,
    )
    return done.returncode


def _token_env(token: str) -> dict[str, str]:
    """git reads this extra header from the environment, so the token never appears in a process's arguments."""
    basic = base64.b64encode(f"x-access-token:{token}".encode()).decode()
    return {
        "GIT_CONFIG_COUNT": "1",
        "GIT_CONFIG_KEY_0": "http.https://github.com/.extraheader",
        "GIT_CONFIG_VALUE_0": f"AUTHORIZATION: basic {basic}",
        "GIT_TERMINAL_PROMPT": "0",
    }


def publish(args: argparse.Namespace, env: Mapping[str, str], github: GitHub, push: Push, out: TextIO) -> int:
    repo, task = check_repo(args.repo), check_task(args.task)
    ws = Path(args.workspace)
    changed = changed_paths(ws)
    if not changed:
        print("No changes to publish: Sebastian finished without editing any file.", file=out)
        return 0
    guarded = [path for path in changed if path.startswith(".github/")]
    if guarded:
        print(
            f"::error::Refused: the task changed {', '.join(guarded[:5])}; summoned tasks never edit .github/", file=out
        )
        return 4
    token = (env.get("TASKPR_TOKEN") or "").strip()
    if not token:
        raise TaskError("TASKPR_TOKEN is not set; nothing was pushed")

    branch = branch_name(args.task_id)
    status, info = github("GET", f"{API}/repos/{repo}", token, None)
    if status != 200 or not isinstance(info.get("default_branch"), str):
        print(f"::error::could not read {repo} (HTTP {status})", file=out)
        return 1
    _git(ws, "checkout", "-q", "-b", branch)
    _git(ws, "add", "-A")
    name, email = AUTHOR
    _git(ws, "-c", f"user.name={name}", "-c", f"user.email={email}", "commit", "-q", "-m", pr_title(task))
    if push(ws, branch, _token_env(token)) != 0:
        print(f"::error::git push of {branch} to {repo} failed", file=out)
        return 1
    body = {
        "title": pr_title(task),
        "head": branch,
        "base": info["default_branch"],
        "body": pr_body(task, args.run_url, args.model, changed),
        "draft": True,
    }
    status, made = github("POST", f"{API}/repos/{repo}/pulls", token, body)
    url = made.get("html_url") if isinstance(made.get("html_url"), str) else ""
    if status != 201 or not url:
        print(f"::error::GitHub refused the pull request (HTTP {status}); the branch {branch} was pushed", file=out)
        return 1
    print(f"Opened draft PR {url} ({len(changed)} files)", file=out)
    return 0


def main(
    argv: Sequence[str] | None = None,
    *,
    env: Mapping[str, str] | None = None,
    github: GitHub = github_api,
    push: Push = git_push,
    out: TextIO = sys.stdout,
) -> int:
    parser = argparse.ArgumentParser(prog="python -m tools.taskpr", description=(__doc__ or "").splitlines()[0])
    sub = parser.add_subparsers(dest="cmd", required=True)
    check = sub.add_parser("check", help="refuse a task or repository before anything runs")
    check.add_argument("--task", required=True)
    check.add_argument("--repo", required=True)
    pub = sub.add_parser("publish", help="commit, push and open a draft PR for the workspace's changes")
    pub.add_argument("--workspace", required=True)
    pub.add_argument("--repo", required=True)
    pub.add_argument("--task-id", required=True)
    pub.add_argument("--task", required=True)
    pub.add_argument("--run-url", required=True)
    pub.add_argument("--model", required=True)
    args = parser.parse_args(argv)
    env = os.environ if env is None else env
    try:
        if args.cmd == "check":
            check_task(args.task)
            check_repo(args.repo)
            print("ok", file=out)
            return 0
        return publish(args, env, github, push, out)
    except TaskError as exc:
        print(f"::error::{exc}", file=out)
        return 2
    except subprocess.CalledProcessError as exc:
        print(f"::error::git {exc.cmd[3] if len(exc.cmd) > 3 else ''} failed (exit {exc.returncode})", file=out)
        return 1
