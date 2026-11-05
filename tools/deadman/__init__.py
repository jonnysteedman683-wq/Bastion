"""Deadman: keep a Healthchecks.io check for every scheduled job in the fleet.

`deploy/healthchecks.json` lists each job (slug, repo, workflow, cron, grace). This tool validates that
file and upserts each check through the Healthchecks.io Management API v3: a POST to `/checks/` with
`unique: ["slug"]` updates the matching check (200) or creates it (201). Checks are never deleted here.

    python -m tools.deadman validate          # check the registry; no network
    python -m tools.deadman plan              # print the API payloads; no network, no key
    python -m tools.deadman sync              # upsert every check (needs HC_API_KEY)

Environment: `HC_API_KEY` (secret, read-write Management API key) and optional `HC_API_URL`
(default https://healthchecks.io/api/v3/, for a self-hosted instance). Jobs ping
`https://hc-ping.com/<HC_PING_KEY>/<slug>`, which is wired up separately (backlog G3b).
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import urllib.error
import urllib.request
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, TextIO, cast
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

ROOT = Path(__file__).resolve().parents[2]
REGISTRY = ROOT / "deploy" / "healthchecks.json"
API_KEY_ENV = "HC_API_KEY"
API_URL_ENV = "HC_API_URL"
DEFAULT_API_URL = "https://healthchecks.io/api/v3/"
TAG = "bastion"
MIN_GRACE, MAX_GRACE = 60, 31_536_000  # Healthchecks.io limits, in seconds
TIMEOUT = 10.0

SLUG_RE = re.compile(r"^[a-z0-9_-]{1,100}$")
REPO_RE = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
WORKFLOW_RE = re.compile(r"^[A-Za-z0-9_.-]+\.ya?ml$")
CRON_FIELD_RE = re.compile(r"^[0-9*/,\-]+$")
FIELDS = {"slug", "repo", "workflow", "schedule", "grace", "tz"}


@dataclass(frozen=True)
class Check:
    slug: str
    repo: str
    workflow: str
    schedule: str
    grace: int
    tz: str = "UTC"

    def payload(self) -> dict[str, Any]:
        """The Management API body that creates this check or updates the one with the same slug."""
        repo_name = self.repo.split("/", 1)[1].lower()
        return {
            "name": self.slug,
            "slug": self.slug,
            "tags": f"{TAG} {repo_name}",
            "desc": f"{self.repo} .github/workflows/{self.workflow} ({self.schedule} {self.tz})",
            "schedule": self.schedule,
            "tz": self.tz,
            "grace": self.grace,
            "unique": ["slug"],
        }


def _valid_tz(tz: str) -> bool:
    try:
        ZoneInfo(tz)
    except (ZoneInfoNotFoundError, ValueError):
        return False
    return True


def validate(data: Any) -> tuple[list[Check], list[str]]:
    """Parse the registry. Returns the checks and a list of problems (empty when valid)."""
    if not isinstance(data, dict) or not isinstance(data.get("checks"), list):
        return [], ['registry must be an object with a "checks" list']
    checks: list[Check] = []
    errors: list[str] = []
    seen: set[str] = set()
    for i, raw in enumerate(data["checks"]):
        where = f"checks[{i}]"
        if not isinstance(raw, dict):
            errors.append(f"{where}: must be an object")
            continue
        unknown = sorted(set(raw) - FIELDS)
        if unknown:
            errors.append(f"{where}: unknown field(s) {', '.join(unknown)}")
        slug, repo, workflow = raw.get("slug"), raw.get("repo"), raw.get("workflow")
        schedule, grace, tz = raw.get("schedule"), raw.get("grace"), raw.get("tz", "UTC")
        before = len(errors)
        if not isinstance(slug, str) or not SLUG_RE.match(slug):
            errors.append(f"{where}: slug must be 1-100 of a-z, 0-9, '-', '_'")
        elif slug in seen:
            errors.append(f"{where}: duplicate slug {slug}")
        else:
            seen.add(slug)
            where = f"{slug}"
        if not isinstance(repo, str) or not REPO_RE.match(repo):
            errors.append(f"{where}: repo must be owner/name")
        if not isinstance(workflow, str) or not WORKFLOW_RE.match(workflow):
            errors.append(f"{where}: workflow must be a file name ending in .yml or .yaml")
        fields = schedule.split() if isinstance(schedule, str) else []
        if len(fields) != 5 or not all(CRON_FIELD_RE.match(f) for f in fields):
            errors.append(f"{where}: schedule must be a 5-field numeric cron expression")
        if isinstance(grace, bool) or not isinstance(grace, int) or not MIN_GRACE <= grace <= MAX_GRACE:
            errors.append(f"{where}: grace must be whole seconds between {MIN_GRACE} and {MAX_GRACE}")
        if not isinstance(tz, str) or not _valid_tz(tz):
            errors.append(f"{where}: tz must be an IANA time zone")
        if len(errors) == before and not unknown:
            checks.append(Check(str(slug), str(repo), str(workflow), " ".join(fields), cast(int, grace), str(tz)))
    return checks, errors


def load(path: Path = REGISTRY) -> tuple[list[Check], list[str]]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return [], [f"{path}: {exc}"]
    return validate(data)


Post = Callable[[str, dict[str, Any], str], tuple[int, str]]


def http_post(url: str, body: dict[str, Any], api_key: str) -> tuple[int, str]:
    """POST JSON with the API key header. Returns (status, response text); HTTP errors are returned, not raised."""
    if not url.startswith("https://") and not url.startswith("http://127.0.0.1"):
        raise ValueError("the Healthchecks API URL must be https")
    req = urllib.request.Request(  # noqa: S310 - scheme checked above
        url,
        data=json.dumps(body).encode(),
        headers={"X-Api-Key": api_key, "Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:  # noqa: S310 - scheme checked above
            return int(resp.status), resp.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as exc:
        return int(exc.code), exc.read().decode("utf-8", "replace")


def sync(checks: Sequence[Check], *, api_url: str, api_key: str, post: Post, out: TextIO) -> int:
    """Upsert every check. Keeps going past failures; returns 1 if any failed."""
    endpoint = api_url.rstrip("/") + "/checks/"
    failed = 0
    for check in checks:
        try:
            status, text = post(endpoint, check.payload(), api_key)
        except (OSError, ValueError) as exc:
            status, text = 0, str(exc)
        if status == 201:
            print(f"created {check.slug}", file=out)
        elif status == 200:
            print(f"updated {check.slug}", file=out)
        else:
            failed += 1
            hint = {401: "API key missing or invalid", 403: "check limit reached"}.get(status, text.strip()[:200])
            print(f"::error::{check.slug}: HTTP {status}: {hint}", file=out)
    print(f"{len(checks) - failed}/{len(checks)} checks in sync", file=out)
    return 1 if failed else 0


def main(
    argv: Sequence[str] | None = None,
    *,
    env: dict[str, str] | None = None,
    post: Post = http_post,
    out: TextIO = sys.stdout,
) -> int:
    parser = argparse.ArgumentParser(prog="python -m tools.deadman", description=(__doc__ or "").splitlines()[0])
    parser.add_argument("--registry", type=Path, default=REGISTRY)
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("validate", help="check the registry; no network")
    sub.add_parser("plan", help="print the API payloads; no network, no key")
    sub.add_parser("sync", help=f"upsert every check (needs {API_KEY_ENV})")
    args = parser.parse_args(argv)
    env = dict(os.environ) if env is None else env

    checks, errors = load(args.registry)
    for err in errors:
        print(f"::error::{err}", file=out)
    if errors:
        return 1
    if args.cmd == "validate":
        for c in checks:
            print(f"ok {c.slug}: {c.repo} {c.workflow} '{c.schedule}' grace {c.grace}s", file=out)
        print(f"{len(checks)} checks valid", file=out)
        return 0
    if args.cmd == "plan":
        print(json.dumps([c.payload() for c in checks], indent=2), file=out)
        return 0
    api_key = env.get(API_KEY_ENV, "")
    if not api_key:
        print(f"::error::Set {API_KEY_ENV} to a read-write Healthchecks.io Management API key.", file=out)
        return 2
    return sync(checks, api_url=env.get(API_URL_ENV) or DEFAULT_API_URL, api_key=api_key, post=post, out=out)
