"""command-centre: Bastion's private dashboard. Serves the built web app and a small read-only API.

    GET /health        -> {"status": "ok", "service": "command-centre", "version": "<git sha>"}
    GET /api/roster    -> {"source": ..., "agents": [{name, role, runs_on, last_seen, status}]}
                          the ledger's agents, then the AI systems registered in sources.json
    GET /api/feed      -> {"source": ..., "events": [{id, ts, agent, kind, summary}]}, newest first
                          ?after=<id> returns only newer events; ?limit=<n> caps the count (max 200)
    GET /api/spend     -> {"source": ..., "today"/"last_7d"/"last_30d": {cost_usd, tokens, runs, ...},
                          "daily": [30 UTC days, oldest first], "agents": [...], "caps": ...}
    GET /api/approvals -> {"source": ..., "approvals": [{task_id, title, ..., approve_command, reject_command}]},
                          oldest first. Read-only: the owner signs decisions with LinX on their own machine.
    GET /*             -> the web app (web/dist), with index.html for client-side routes

The browser only ever talks to this server. The ledger is read server-side:
  - LEDGER_DSN set (a Secret Manager secret): the UniSync ledger in Supabase, read as the read-only
    bastion_reader role through the bastion.feed and bastion.roster views (see ledger/bastion_reader.sql).
  - otherwise: a demo ledger, generated from the clock so the feed visibly moves. The UI labels it.

Standard library only, plus pg8000 when a ledger is configured.
Logs one JSON line per request to stderr. Shuts down cleanly on SIGTERM.
"""

from __future__ import annotations

import hmac
import json
import math
import mimetypes
import os
import re
import signal
import sqlite3
import sys
import threading
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Mapping
from dataclasses import asdict, dataclass
from datetime import UTC, date, datetime, timedelta
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Protocol

SERVICE = "command-centre"
HERE = Path(__file__).resolve().parent
STATIC_DIR = HERE / "web" / "dist"
ROSTER_PATH = HERE / "roster.json"
CAPS_PATH = HERE / "caps.json"
SOURCES_PATH = Path(os.environ.get("SOURCES_CONFIG", str(HERE / "sources.json")))
SOURCE_ROLES = {
    "ollama": "Ollama models",
    "openai": "OpenAI-compatible models",
    "push": "Reports to Bastion",
}
DEFAULT_POLL_SECONDS = 60
MIN_POLL_SECONDS = 10
MAX_POLL_SECONDS = 86_400
# An auth entry names the environment variable that holds the key. It is never the key itself.
ENV_NAME = re.compile(r"[A-Z][A-Z0-9_]{0,63}")
SPEND_DAYS = 30
SPEND_FIELDS = ("day", "agent", "runs", "input_tokens", "output_tokens", "cost_usd")
CAP_NEAR = 0.8  # share of a cap at which spend shows as "near"
MAX_LIMIT = 200
ACTIVE_WITHIN = timedelta(hours=1)
IDLE_WITHIN = timedelta(days=1)
INGEST_DB_PATH = Path(os.environ.get("INGEST_DB", str(Path(__file__).resolve().parent / "ingest.db")))
LOCAL_EVENT_ID_OFFSET = 10_000_000
# Task ids come from agents, and the owner pastes the command into a shell: only plain ids get one.
# The first character can't be '-', so an id can never be read as a LinX flag.
SAFE_TASK_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,79}")
APPROVAL_FIELDS = (
    "task_id",
    "title",
    "requested_by",
    "agent",
    "mode",
    "run_url",
    "requested_ts",
    "updated_ts",
    "detail",
)
MAX_REQUEST_BODY_BYTES = 64 * 1024
MAX_EMAIL_LENGTH = 254
MAX_STRING_FIELD_LENGTH = 128
MAX_SUMMARY_LENGTH = 4096

# Only our own origin may load scripts, styles, data or frames. No inline script.
CSP = (
    "default-src 'self'; img-src 'self' data:; style-src 'self'; script-src 'self'; "
    "frame-ancestors 'none'; base-uri 'none'"
)


@dataclass(frozen=True)
class Event:
    id: int
    ts: str
    agent: str
    kind: str
    summary: str


class Ledger(Protocol):
    source: str

    def events(self, after: int, limit: int) -> list[Event]:
        """Events with id > after, newest first, at most limit."""
        ...

    def roster(self) -> list[dict[str, str]]:
        """Every agent: name, role, runs_on."""
        ...

    def operations(self) -> dict[str, object]: ...

    def approvals(self) -> list[dict[str, str]]: ...

    def spend(self, since: str) -> list[dict[str, object]]: ...


class AuthError(Exception):
    """A failed or unavailable Supabase Auth request."""


class Auth(Protocol):
    configured: bool

    def current_user(self, access_token: str) -> dict[str, object]: ...

    def send_magic_link(self, email: str, callback_url: str) -> None: ...

    def verify_magic_link(self, token_hash: str, token_type: str) -> str: ...


class AuthClient:
    """Small server-only Supabase Auth REST client."""

    def __init__(self, url: str, publishable_key: str) -> None:
        self._url = url.rstrip("/")
        self._key = publishable_key
        self.configured = bool(self._url and self._key)

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> AuthClient:
        source = os.environ if env is None else env
        return cls(source.get("SUPABASE_URL", ""), source.get("SUPABASE_PUBLISHABLE_KEY", ""))

    def _request(
        self, method: str, path: str, payload: dict[str, object] | None = None, token: str | None = None
    ) -> dict[str, object]:
        if not self.configured:
            raise AuthError("auth not configured")
        headers = {"apikey": self._key}
        if token:
            headers["Authorization"] = f"Bearer {token}"
        data = None
        if payload is not None:
            headers["Content-Type"] = "application/json"
            data = json.dumps(payload).encode("utf-8")
        request = urllib.request.Request(  # noqa: S310 -- Supabase URL is an operator-controlled setting.
            f"{self._url}/auth/v1{path}", data=data, headers=headers, method=method
        )
        try:
            with urllib.request.urlopen(request, timeout=5) as response:  # noqa: S310
                return json.loads(response.read() or b"{}")
        except (urllib.error.HTTPError, urllib.error.URLError, ValueError) as err:
            raise AuthError("auth request failed") from err

    def send_magic_link(self, email: str, callback_url: str) -> None:
        self._request("POST", "/otp", {"email": email, "should_create_user": False, "email_redirect_to": callback_url})

    def verify_magic_link(self, token_hash: str, token_type: str) -> str:
        response = self._request("POST", "/verify", {"token_hash": token_hash, "type": token_type})
        token = response.get("access_token")
        if not isinstance(token, str) or not token:
            raise AuthError("missing access token")
        return token

    def current_user(self, access_token: str) -> dict[str, object]:
        response = self._request("GET", "/user", token=access_token)
        if not isinstance(response.get("id"), str):
            raise AuthError("missing user")
        return response


# --- Demo ledger -----------------------------------------------------------------------------

DEMO_STEP = timedelta(minutes=3)
DEMO_EPOCH = datetime(2026, 1, 1, tzinfo=UTC)
_DEMO_SCRIPT: tuple[tuple[str, str, str], ...] = (
    ("Sebastian", "task.request", "Summon: run the nightly demo"),
    ("Sebastian", "task.result", "Demo finished: 3 checks passed"),
    ("Claude", "task.request", "Draft backlog item S3 (summon button)"),
    ("Claude", "task.result", "Opened a pull request for review"),
    ("Quartermaster", "approval.request", "Deploy hello to production: waiting for the owner"),
    ("Jules", "task.request", "Pin the python base image by digest"),
    ("Jules", "memory.write", "Learned: Cloud Run reserves paths ending in z"),
    ("Vortex", "approval.decision", "Owner key approved the production deploy"),
)


class DemoLedger:
    """One event every DEMO_STEP, derived from the clock, so the feed moves without any storage."""

    source = "demo"

    def __init__(self, clock: Any = None) -> None:
        self._clock = clock or (lambda: datetime.now(UTC))

    def _event(self, n: int) -> Event:
        agent, kind, summary = _DEMO_SCRIPT[n % len(_DEMO_SCRIPT)]
        ts = DEMO_EPOCH + n * DEMO_STEP
        return Event(n, ts.strftime("%Y-%m-%dT%H:%M:%SZ"), agent, kind, summary)

    def roster(self) -> list[dict[str, str]]:
        return load_roster()

    def events(self, after: int, limit: int) -> list[Event]:
        latest = int((self._clock() - DEMO_EPOCH) / DEMO_STEP)
        start = max(after + 1, latest - limit + 1, 0)
        return [self._event(n) for n in range(latest, start - 1, -1)]

    def operations(self) -> dict[str, object]:
        now = self._clock().strftime("%Y-%m-%dT%H:%M:%SZ")
        tasks = [
            {
                "task_id": "demo-approval",
                "title": "Deploy hello",
                "status": "awaiting_approval",
                "agent": "Quartermaster",
                "updated_ts": now,
                "detail": "Owner approval required",
            },
            {
                "task_id": "demo-complete",
                "title": "Run nightly demo",
                "status": "done",
                "agent": "Sebastian",
                "updated_ts": now,
                "detail": "3 checks passed",
            },
        ]
        return {
            "generated_at": now,
            "attention": [{"kind": "awaiting_approval", "title": "Deploy hello", "detail": "Owner approval required"}],
            "tasks": tasks,
            "pr_report": None,
            "completions": [
                {"agent": "Sebastian", "kind": "task.result", "summary": "Demo finished: 3 checks passed", "ts": now}
            ],
            "control": {"halted": False, "reason": None, "since": None},
        }

    def approvals(self) -> list[dict[str, str]]:
        asked = (self._clock() - timedelta(hours=2)).strftime("%Y-%m-%dT%H:%M:%SZ")
        return [
            {
                "task_id": "demo-approval",
                "title": "Deploy hello",
                "requested_by": "Quartermaster",
                "agent": "Quartermaster",
                "mode": "deploy",
                "run_url": "",
                "requested_ts": asked,
                "updated_ts": asked,
                "detail": "Owner approval required",
            }
        ]

    def spend(self, since: str) -> list[dict[str, object]]:
        """A month of plausible spend, fixed per day so reloads agree."""
        today = self._clock().date()
        rows: list[dict[str, object]] = []
        for back in range(SPEND_DAYS):
            day = today - timedelta(days=back)
            n = day.toordinal()
            for agent, base, spread in (("Sebastian", 0.6, 7), ("Claude", 0.9, 11), ("Jules", 0.3, 5)):
                runs = 1 + n % spread % 4
                cost = round(base * runs * (0.6 + (n * 7 + len(agent)) % 10 / 10), 4)
                rows.append(
                    {
                        "day": day.isoformat(),
                        "agent": agent,
                        "runs": runs,
                        "input_tokens": int(cost * 180_000),
                        "output_tokens": int(cost * 22_000),
                        "cost_usd": cost,
                    }
                )
        return [row for row in rows if str(row["day"]) >= since]


# --- UniSync ledger (Supabase Postgres) -----------------------------------------------------


class Connect(Protocol):
    def __call__(self) -> Any: ...


def _pg8000_connect(dsn: str, timeout: float) -> Connect:
    """Connection factory for a postgresql:// DSN. pg8000 is imported only when a ledger is configured."""
    parts = urllib.parse.urlsplit(dsn)
    if parts.scheme not in ("postgres", "postgresql") or not parts.hostname:
        raise ValueError("LEDGER_DSN must be a postgresql:// URL")

    def connect() -> Any:
        import ssl

        import pg8000.native  # type: ignore[import-not-found]  # installed in the image, not needed for tests

        return pg8000.native.Connection(
            user=urllib.parse.unquote(parts.username or ""),
            password=urllib.parse.unquote(parts.password or ""),
            host=parts.hostname,
            port=parts.port or 5432,
            database=(parts.path.lstrip("/") or "postgres"),
            ssl_context=ssl.create_default_context(),
            timeout=timeout,
        )

    return connect


FEED_SQL = "SELECT id, ts, agent, kind, summary FROM bastion.feed WHERE id > :after ORDER BY id DESC LIMIT :limit"
ROSTER_SQL = "SELECT name, role, repos FROM bastion.roster ORDER BY name"
DASHBOARD_TASKS_SQL = (
    "SELECT task_id, title, requested_by, agent, status, mode, run_url, requested_ts, claimed_ts, "
    "finished_ts, updated_ts, detail FROM bastion.dashboard_tasks ORDER BY updated_ts DESC LIMIT :limit"
)
STEWARD_REPORT_SQL = "SELECT scanned_ts, summary, counts, floods, items, truncated FROM bastion.steward_report LIMIT 1"
CONTROL_SQL = "SELECT halted, reason, since FROM bastion.control LIMIT 1"
SPEND_SQL = (
    "SELECT day, agent, runs, input_tokens, output_tokens, cost_usd "
    "FROM bastion.spend_daily WHERE day >= :since ORDER BY day, agent"
)
APPROVALS_SQL = (
    "SELECT task_id, title, requested_by, agent, mode, run_url, requested_ts, updated_ts, detail "
    "FROM bastion.dashboard_tasks WHERE status = 'awaiting_approval' ORDER BY requested_ts ASC NULLS LAST LIMIT :limit"
)


class PostgresLedger:
    """Reads the UniSync ledger through the read-only bastion.feed and bastion.roster views."""

    source = "ledger"

    def __init__(self, connect: Connect) -> None:
        self._connect = connect

    def _run(self, sql: str, **params: object) -> list[list[Any]]:
        conn = self._connect()
        try:
            rows: list[list[Any]] = conn.run(sql, **params)
            return rows
        finally:
            conn.close()

    def events(self, after: int, limit: int) -> list[Event]:
        rows = self._run(FEED_SQL, after=after, limit=limit)
        return [Event(int(r[0]), str(r[1]), str(r[2]), str(r[3]), str(r[4])) for r in rows]

    def roster(self) -> list[dict[str, str]]:
        out = []
        for name, role, repos in self._run(ROSTER_SQL):
            out.append({"name": str(name), "role": str(role), "runs_on": ", ".join(repos or []) or "-"})
        return out

    def dashboard_tasks(self, limit: int = 100) -> list[dict[str, str]]:
        fields = (
            "task_id",
            "title",
            "requested_by",
            "agent",
            "status",
            "mode",
            "run_url",
            "requested_ts",
            "claimed_ts",
            "finished_ts",
            "updated_ts",
            "detail",
        )
        return [
            dict(zip(fields, ("" if value is None else str(value) for value in row), strict=True))
            for row in self._run(DASHBOARD_TASKS_SQL, limit=limit)
        ]

    def spend(self, since: str) -> list[dict[str, object]]:
        return [
            {**dict(zip(SPEND_FIELDS, row, strict=True)), "cost_usd": float(row[5] or 0)}
            for row in self._run(SPEND_SQL, since=since)
        ]

    def approvals(self, limit: int = MAX_LIMIT) -> list[dict[str, str]]:
        return [
            dict(zip(APPROVAL_FIELDS, ("" if value is None else str(value) for value in row), strict=True))
            for row in self._run(APPROVALS_SQL, limit=limit)
        ]

    def operations(self) -> dict[str, object]:
        tasks = self.dashboard_tasks()
        attention = [
            {"kind": task["status"], "title": task["title"], "detail": task["detail"]}
            for task in tasks
            if task["status"] in {"failed", "awaiting_approval"}
        ]
        report_rows = self._run(STEWARD_REPORT_SQL)
        report = None
        if report_rows:
            scanned_ts, summary, counts, floods, items, truncated = report_rows[0]

            def decode(value: object, default: object) -> object:
                return json.loads(value) if isinstance(value, str) else (value or default)

            report = {
                "scanned_ts": str(scanned_ts),
                "summary": str(summary),
                "counts": decode(counts, {}),
                "floods": decode(floods, {}),
                "items": decode(items, []),
                "truncated": int(truncated or 0),
            }
        control_rows = self._run(CONTROL_SQL)
        control = {"halted": False, "reason": None, "since": None}
        if control_rows:
            halted, reason, since = control_rows[0]
            control = {
                "halted": bool(halted),
                "reason": None if reason is None else str(reason),
                "since": None if since is None else str(since),
            }
            if control["halted"]:
                attention.insert(
                    0,
                    {
                        "kind": "halted",
                        "title": "Fleet halted",
                        "detail": control["reason"] or "The owner halted the fleet",
                    },
                )
        completions = [asdict(event) for event in self.events(0, 50) if event.kind in {"task.result", "deploy.record"}]
        return {
            "generated_at": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "attention": attention,
            "tasks": tasks,
            "pr_report": report,
            "completions": completions,
            "control": control,
        }


def ledger_from_env(env: dict[str, str] | None = None) -> Ledger:
    env = dict(os.environ) if env is None else env
    dsn = env.get("LEDGER_DSN", "")
    if dsn:
        return PostgresLedger(_pg8000_connect(dsn, timeout=5.0))
    return DemoLedger()


# --- Ingest buffer (local AI push) -----------------------------------------------------------


def ingest_agent_keys(env: Mapping[str, str] | None = None) -> dict[str, str]:
    """Return a valid per-agent ingest registry, or disable ingest on bad configuration."""
    raw = (os.environ if env is None else env).get("INGEST_AGENT_KEYS", "")
    try:
        configured = json.loads(raw)
    except json.JSONDecodeError:
        return {}
    if not isinstance(configured, dict) or not configured:
        return {}

    keys: dict[str, str] = {}
    secrets: set[str] = set()
    for agent, secret in configured.items():
        if not isinstance(agent, str) or not agent.strip() or not isinstance(secret, str) or not secret:
            return {}
        if secret in secrets:
            return {}
        keys[agent] = secret
        secrets.add(secret)
    return keys


def agent_for_ingest_token(authorization: str, keys: Mapping[str, str]) -> str | None:
    """Resolve a Bearer token to its configured agent without exposing key material."""
    if not authorization.startswith("Bearer "):
        return None
    token = authorization.removeprefix("Bearer ").strip()
    if not token:
        return None
    matches = [agent for agent, secret in keys.items() if hmac.compare_digest(token, secret)]
    return matches[0] if len(matches) == 1 else None


def _init_ingest_db(path: Path | None = None) -> None:
    conn = sqlite3.connect(str(path or INGEST_DB_PATH))
    try:
        conn.execute(
            "CREATE TABLE IF NOT EXISTS ingest ("
            "id INTEGER PRIMARY KEY AUTOINCREMENT, "
            "ts TEXT NOT NULL, agent TEXT NOT NULL, kind TEXT NOT NULL, "
            "summary TEXT NOT NULL, source TEXT)"
        )
        conn.commit()
    finally:
        conn.close()


def ingest_event(source: str, agent: str, kind: str, summary: str, ts: str | None = None) -> Event:
    _init_ingest_db()
    if ts is None:
        ts = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    conn = sqlite3.connect(str(INGEST_DB_PATH))
    try:
        cur = conn.execute(
            "INSERT INTO ingest (ts, agent, kind, summary, source) VALUES (?,?,?,?,?)",
            (ts, agent, kind, summary, source),
        )
        row_id = cur.lastrowid
        conn.commit()
    finally:
        conn.close()
    return Event(id=row_id, ts=ts, agent=agent, kind=kind, summary=summary)


def ingest_events(after: int = 0, limit: int = MAX_LIMIT) -> list[Event]:
    _init_ingest_db()
    conn = sqlite3.connect(str(INGEST_DB_PATH))
    try:
        rows = conn.execute(
            "SELECT id, ts, agent, kind, summary FROM ingest WHERE id > ? ORDER BY id DESC LIMIT ?",
            (after, limit),
        ).fetchall()
    finally:
        conn.close()
    return [Event(int(r[0]), str(r[1]), str(r[2]), str(r[3]), str(r[4])) for r in rows]


# --- Source registry (other AI systems) ------------------------------------------------------


@dataclass(frozen=True)
class Source:
    """One named AI system connected to Bastion.

    type "ollama" or "openai": a model server Bastion can poll at `endpoint`.
    type "push": a system that reports in through POST /api/ingest under the same name.
    """

    name: str
    type: str
    endpoint: str | None
    auth_env: str | None
    poll_interval: int


def _warn(message: str) -> None:
    sys.stderr.write(json.dumps({"severity": "WARNING", "service": SERVICE, "message": message}) + "\n")


def _source_endpoint(value: object) -> str:
    if not isinstance(value, str) or len(value) > 2048:
        raise ValueError("endpoint must be an http(s) URL")
    parts = urllib.parse.urlsplit(value.strip())
    if parts.scheme not in ("http", "https") or not parts.hostname:
        raise ValueError("endpoint must be an http(s) URL")
    if parts.username is not None or parts.password is not None or parts.query or parts.fragment:
        raise ValueError("endpoint must not carry credentials, a query or a fragment")
    _ = parts.port  # raises ValueError on a port that is not a number in range
    return urllib.parse.urlunsplit((parts.scheme, parts.netloc, parts.path.rstrip("/"), "", ""))


def _parse_source(entry: object) -> Source:
    if not isinstance(entry, dict):
        raise ValueError("must be a JSON object")
    name = entry.get("name")
    if not isinstance(name, str) or not name.strip() or len(name.strip()) > MAX_STRING_FIELD_LENGTH:
        raise ValueError("name must be 1 to 128 characters")
    kind = entry.get("type")
    if not isinstance(kind, str) or kind not in SOURCE_ROLES:
        raise ValueError("type must be one of " + ", ".join(sorted(SOURCE_ROLES)))
    endpoint = None
    if kind != "push" or entry.get("endpoint") is not None:
        endpoint = _source_endpoint(entry.get("endpoint"))
    auth_env = None
    auth = entry.get("auth")
    if auth is not None:
        # Never echo what was configured here: a mistake is most likely a pasted key.
        if not isinstance(auth, dict) or set(auth) != {"env"}:
            raise ValueError('auth must be {"env": "<ENVIRONMENT_VARIABLE_NAME>"}')
        auth_env = auth["env"]
        if not isinstance(auth_env, str) or not ENV_NAME.fullmatch(auth_env):
            raise ValueError("auth.env must name an environment variable, not hold a key")
    interval = entry.get("poll_interval", DEFAULT_POLL_SECONDS)
    if isinstance(interval, bool) or not isinstance(interval, int):
        raise ValueError("poll_interval must be a whole number of seconds")
    return Source(name.strip(), kind, endpoint, auth_env, min(max(interval, MIN_POLL_SECONDS), MAX_POLL_SECONDS))


def load_sources(path: Path | None = None, warn: Any = _warn) -> list[Source]:
    """Read the source registry. A bad entry is skipped with a warning; it never stops the server."""
    path = SOURCES_PATH if path is None else path
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return []
    except (OSError, ValueError):
        warn(f"sources: {path.name} is not readable JSON; no sources loaded")
        return []
    if not isinstance(data, list):
        warn(f"sources: {path.name} must be a JSON list; no sources loaded")
        return []
    sources: list[Source] = []
    seen: set[str] = set()
    for index, entry in enumerate(data):
        try:
            source = _parse_source(entry)
            if source.name.casefold() in seen:
                raise ValueError("name is already used by another source")
        except ValueError as err:
            warn(f"sources: entry {index} skipped: {err}")
            continue
        seen.add(source.name.casefold())
        sources.append(source)
    return sources


def source_roster_entry(source: Source) -> dict[str, str]:
    """What the roster shows for a source: where it is, never how Bastion authenticates to it."""
    runs_on = urllib.parse.urlsplit(source.endpoint).netloc if source.endpoint else "Reports in"
    return {"name": source.name, "role": SOURCE_ROLES[source.type], "runs_on": runs_on}


# --- Ledger wrapper that merges ingest buffer -----------------------------------------------


class MergedLedger:
    """Wraps a base ledger, merging in locally ingested events and the configured sources."""

    def __init__(self, base: Ledger, sources: list[Source] | None = None) -> None:
        self._base = base
        self._sources = list(sources or [])
        self.source = base.source

    def events(self, after: int, limit: int) -> list[Event]:
        base_events = self._base.events(after, limit)
        local_after = max(after - LOCAL_EVENT_ID_OFFSET, 0) if after >= LOCAL_EVENT_ID_OFFSET else 0
        local_events = [
            Event(LOCAL_EVENT_ID_OFFSET + event.id, event.ts, event.agent, event.kind, event.summary)
            for event in ingest_events(local_after, limit)
        ]
        return sorted([*base_events, *local_events], key=lambda event: (event.ts, event.id), reverse=True)[:limit]

    def roster(self) -> list[dict[str, str]]:
        roster = list(self._base.roster())
        known = {agent["name"].casefold() for agent in roster}
        return [*roster, *(source_roster_entry(s) for s in self._sources if s.name.casefold() not in known)]

    def operations(self) -> dict[str, object]:
        return self._base.operations()

    def approvals(self) -> list[dict[str, str]]:
        return self._base.approvals()

    def spend(self, since: str) -> list[dict[str, object]]:
        return self._base.spend(since)


# --- Spend ---------------------------------------------------------------------------------


def load_caps(path: Path = CAPS_PATH) -> dict[str, Any]:
    """Daily USD caps: fleet-wide, per-agent default, and per-agent overrides. Missing means no cap."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        data = {}

    def cap(value: object) -> float | None:
        number = _number(value)
        return number if number > 0 else None

    overrides = data.get("agent_daily_usd") or {}
    return {
        "daily_usd": cap(data.get("daily_usd")),
        "default_agent_daily_usd": cap(data.get("default_agent_daily_usd")),
        "agent_daily_usd": {str(k): v for k, v in ((k, cap(v)) for k, v in overrides.items()) if v is not None},
    }


def _number(value: object) -> float:
    """A finite, positive number, else 0. Ledger rows are agent-written, so never trust their types."""
    if isinstance(value, bool):
        return 0.0
    try:
        number = float(value)  # type: ignore[arg-type]  # int, float, str or Decimal (pg8000)
    except (TypeError, ValueError):
        return 0.0
    return number if math.isfinite(number) and number > 0 else 0.0


def _cap_status(spent: float, cap: float | None) -> str:
    if cap is None:
        return "none"
    return "over" if spent >= cap else "near" if spent >= cap * CAP_NEAR else "ok"


def summarise_spend(rows: list[dict[str, object]], caps: Mapping[str, Any], today: date) -> dict[str, object]:
    """Totals for today, 7 and 30 UTC days, a zero-filled daily series and per-agent spend against caps."""
    days = [(today - timedelta(days=back)).isoformat() for back in range(SPEND_DAYS - 1, -1, -1)]
    week = set(days[-7:])
    keys = ("runs", "input_tokens", "output_tokens", "cost_usd")
    daily = {day: dict.fromkeys(keys, 0.0) for day in days}
    agents: dict[str, dict[str, float]] = {}
    for row in rows:
        day = str(row.get("day", ""))
        if day not in daily:
            continue
        values = {key: _number(row.get(key)) for key in keys}
        for key in keys:
            daily[day][key] += values[key]
        a = agents.setdefault(
            str(row.get("agent", "")), {"today": 0.0, "week": 0.0, "month": 0.0, "tokens": 0.0, "runs": 0.0}
        )
        a["month"] += values["cost_usd"]
        if day in week:
            a["week"] += values["cost_usd"]
            a["tokens"] += values["input_tokens"] + values["output_tokens"]
            a["runs"] += values["runs"]
        if day == days[-1]:
            a["today"] += values["cost_usd"]

    def window(selected: list[str]) -> dict[str, object]:
        return {
            "cost_usd": round(sum(daily[d]["cost_usd"] for d in selected), 4),
            "input_tokens": int(sum(daily[d]["input_tokens"] for d in selected)),
            "output_tokens": int(sum(daily[d]["output_tokens"] for d in selected)),
            "runs": int(sum(daily[d]["runs"] for d in selected)),
        }

    fleet_cap = caps.get("daily_usd")
    today_total = {
        **window(days[-1:]),
        "cap_usd": fleet_cap,
        "cap_status": _cap_status(daily[days[-1]]["cost_usd"], fleet_cap),
    }
    agent_rows = []
    for name, a in sorted(agents.items(), key=lambda item: (-item[1]["month"], item[0])):
        cap = (caps.get("agent_daily_usd") or {}).get(name, caps.get("default_agent_daily_usd"))
        agent_rows.append(
            {
                "agent": name,
                "today_usd": round(a["today"], 4),
                "last_7d_usd": round(a["week"], 4),
                "last_30d_usd": round(a["month"], 4),
                "last_7d_tokens": int(a["tokens"]),
                "last_7d_runs": int(a["runs"]),
                "cap_usd": cap,
                "cap_status": _cap_status(a["today"], cap),
            }
        )
    return {
        "currency": "USD",
        "reporting": bool(agents),
        "caps": dict(caps),
        "today": today_total,
        "last_7d": window(days[-7:]),
        "last_30d": window(days),
        "daily": [
            {
                "day": d,
                "cost_usd": round(v["cost_usd"], 4),
                "input_tokens": int(v["input_tokens"]),
                "output_tokens": int(v["output_tokens"]),
                "runs": int(v["runs"]),
            }
            for d, v in daily.items()
        ],
        "agents": agent_rows,
    }


def spend_report(ledger: Ledger, now: datetime) -> dict[str, object]:
    today = now.astimezone(UTC).date()
    since = (today - timedelta(days=SPEND_DAYS - 1)).isoformat()
    return {
        "source": ledger.source,
        "generated_at": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
        **summarise_spend(ledger.spend(since), load_caps(), today),
    }


def linx_commands(task_id: str) -> dict[str, str | None]:
    """The LinX commands that decide a task, or None for ids that aren't safe to paste into a shell."""
    if not SAFE_TASK_ID.fullmatch(task_id):
        return {"approve_command": None, "reject_command": None}
    return {"approve_command": f"linx approve {task_id}", "reject_command": f'linx reject {task_id} --reason "<why>"'}


def load_roster(path: Path = ROSTER_PATH) -> list[dict[str, str]]:
    data = json.loads(path.read_text(encoding="utf-8"))
    return [{"name": a["name"], "role": a["role"], "runs_on": a["runs_on"]} for a in data]


def roster_with_status(roster: list[dict[str, str]], events: list[Event], now: datetime) -> list[dict[str, Any]]:
    last: dict[str, str] = {}
    for e in events:  # newest first, so the first sighting is the latest
        last.setdefault(e.agent, e.ts)
    out = []
    for a in roster:
        seen = last.get(a["name"])
        status = "silent"
        if seen:
            age = now - datetime.fromisoformat(seen.replace("Z", "+00:00"))
            status = "active" if age <= ACTIVE_WITHIN else "idle" if age <= IDLE_WITHIN else "silent"
        out.append({**a, "last_seen": seen, "status": status})
    return out


# --- HTTP --------------------------------------------------------------------------------------


def _parse_int(value: str | None, default: int, low: int, high: int) -> int:
    try:
        n = int(value) if value is not None else default
    except ValueError:
        return default
    return min(max(n, low), high)


def make_handler(version: str, ledger: Ledger, static_dir: Path, auth: Any = None) -> type[BaseHTTPRequestHandler]:
    static_root = static_dir.resolve()

    class Handler(BaseHTTPRequestHandler):
        server_version = "bastion-command-centre"
        sys_version = ""

        def do_GET(self) -> None:
            parsed = urllib.parse.urlsplit(self.path)
            query = urllib.parse.parse_qs(parsed.query)
            path = parsed.path
            if path == "/health":
                self._json(200, {"status": "ok", "service": SERVICE, "version": version})
            elif path == "/auth/callback":
                self._callback(query)
            elif path == "/login" or path.startswith("/assets/"):
                self._static(path)
            elif path == "/api/auth/me":
                user = self._current_user()
                if user is not None:
                    self._json(200, {"user": {"id": user["id"], "email": user.get("email")}})
            elif path == "/api/feed":
                if self._current_user() is None:
                    return
                after = _parse_int(query.get("after", [None])[0], 0, 0, 2**62)
                limit = _parse_int(query.get("limit", [None])[0], 50, 1, MAX_LIMIT)
                self._ledger_call(
                    lambda: {"source": ledger.source, "events": [asdict(e) for e in ledger.events(after, limit)]}
                )
            elif path == "/api/roster":
                if self._current_user() is None:
                    return
                self._ledger_call(
                    lambda: {
                        "source": ledger.source,
                        "agents": roster_with_status(
                            ledger.roster(),
                            ledger.events(0, MAX_LIMIT),
                            datetime.now(UTC),
                        ),
                    }
                )
            elif path == "/api/operations":
                if self._current_user() is None:
                    return
                self._ledger_call(lambda: {"source": ledger.source, **ledger.operations()})
            elif path == "/api/spend":
                if self._current_user() is None:
                    return
                self._ledger_call(lambda: spend_report(ledger, datetime.now(UTC)))
            elif path == "/api/approvals":
                if self._current_user() is None:
                    return
                self._ledger_call(
                    lambda: {
                        "source": ledger.source,
                        "approvals": [{**item, **linx_commands(item["task_id"])} for item in ledger.approvals()],
                    }
                )
            elif path.startswith("/api/"):
                self._json(404, {"error": "not found"})
            else:
                if self._current_user(redirect=True) is None:
                    return
                self._static(path)

        def do_POST(self) -> None:
            path = urllib.parse.urlsplit(self.path).path
            if path == "/auth/magic-link":
                self._magic_link()
            elif path == "/auth/sign-out":
                self._sign_out()
            elif path == "/api/ingest":
                self._ingest()
            else:
                self._json(404, {"error": "not found"})

        def _current_user(self, redirect: bool = False) -> dict[str, object] | None:
            if auth is None or not auth.configured:
                self._json(503, {"error": "authentication unavailable"})
                return None
            cookie = SimpleCookie()
            cookie.load(self.headers.get("Cookie", ""))
            session = cookie.get("bastion_session")
            if session is None:
                if redirect:
                    self._redirect("/login")
                else:
                    self._json(401, {"error": "authentication required"})
                return None
            try:
                return auth.current_user(session.value)
            except AuthError:
                if redirect:
                    self._redirect("/login")
                else:
                    self._json(401, {"error": "authentication required"})
                return None

        def _public_origin(self) -> str:
            configured = os.environ.get("BASTION_PUBLIC_URL") or os.environ.get("PUBLIC_BASE_URL") or ""
            if configured:
                parsed = urllib.parse.urlsplit(configured)
                if parsed.scheme and parsed.netloc:
                    return f"{parsed.scheme}://{parsed.netloc}".rstrip("/")
                return configured.rstrip("/")
            host = self.headers.get("Host", "").strip()
            if not host:
                return ""
            proto = self.headers.get("X-Forwarded-Proto", "https")
            return f"{proto}://{host}".rstrip("/")

        def _read_json_body(self, limit: int = MAX_REQUEST_BODY_BYTES) -> dict[str, object] | None:
            try:
                length = int(self.headers.get("Content-Length", "0"))
            except ValueError:
                self._json(400, {"error": "invalid content-length"})
                return None
            if length < 0 or length > limit:
                self._json(413, {"error": "request too large"})
                return None
            raw = self.rfile.read(length) if length else b""
            try:
                payload = json.loads(raw) if raw else {}
            except json.JSONDecodeError:
                self._json(400, {"error": "invalid JSON"})
                return None
            if not isinstance(payload, dict):
                self._json(400, {"error": "JSON object required"})
                return None
            return payload

        def _magic_link(self) -> None:
            if auth is None or not auth.configured:
                self._json(503, {"error": "authentication unavailable"})
                return
            payload = self._read_json_body(16 * 1024)
            if payload is None:
                return
            try:
                email = str(payload.get("email", "")).strip()
                if not email or len(email) > MAX_EMAIL_LENGTH:
                    raise ValueError
                origin = self._public_origin()
                if not origin:
                    raise ValueError
                auth.send_magic_link(email, f"{origin}/auth/callback")
            except (AuthError, ValueError, json.JSONDecodeError):
                self._json(400, {"error": "unable to send sign-in link"})
                return
            self._json(202, {"status": "check your email"})

        def _callback(self, query: dict[str, list[str]]) -> None:
            if auth is None or not auth.configured:
                self._redirect("/login?error=link")
                return
            token_hash = query.get("token_hash", [""])[0]
            token_type = query.get("type", [""])[0]
            try:
                access_token = auth.verify_magic_link(token_hash, token_type)
            except AuthError:
                self._redirect("/login?error=link")
                return
            self._redirect(
                "/", {"Set-Cookie": f"bastion_session={access_token}; HttpOnly; Secure; SameSite=Lax; Path=/"}
            )

        def _sign_out(self) -> None:
            self._send(
                204,
                "text/plain; charset=utf-8",
                b"",
                headers={"Set-Cookie": "bastion_session=; HttpOnly; Secure; SameSite=Lax; Path=/; Max-Age=0"},
            )

        def _ingest(self) -> None:
            agent = agent_for_ingest_token(self.headers.get("Authorization", ""), ingest_agent_keys())
            if agent is None:
                self._json(401, {"error": "invalid or missing API key"})
                return
            payload = self._read_json_body(MAX_REQUEST_BODY_BYTES)
            if payload is None:
                return
            source = str(payload.get("source", "unknown")).strip()[:MAX_STRING_FIELD_LENGTH] or "unknown"
            kind = str(payload.get("kind", "event")).strip()[:MAX_STRING_FIELD_LENGTH] or "event"
            summary = str(payload.get("summary", "")).strip()
            if not summary:
                self._json(400, {"error": "summary required"})
                return
            if len(summary) > MAX_SUMMARY_LENGTH:
                self._json(400, {"error": "summary too long"})
                return
            ts = payload.get("ts")
            event = ingest_event(source, agent, kind, summary, None if ts is None else str(ts))
            self._json(201, {"id": event.id, "ts": event.ts})

        def _ledger_call(self, build: Any) -> None:
            try:
                self._json(200, build())
            except Exception as err:  # the ledger is remote; never take the page down with it
                self.log_error("ledger read failed: %s", type(err).__name__)
                self._json(502, {"error": "ledger unavailable"})

        def _static(self, path: str) -> None:
            rel = urllib.parse.unquote(path).lstrip("/") or "index.html"
            target = (static_root / rel).resolve()
            if not target.is_relative_to(static_root) or not target.is_file():
                # Unknown paths without a file extension are client-side routes: serve the app shell.
                if "." in Path(rel).name:
                    self._send(404, "text/plain; charset=utf-8", b"not found\n")
                    return
                target = static_root / "index.html"
                if not target.is_file():
                    self._send(503, "text/plain; charset=utf-8", b"web app not built\n")
                    return
            ctype = mimetypes.guess_type(target.name)[0] or "application/octet-stream"
            if ctype.startswith("text/") or ctype in ("application/javascript", "image/svg+xml"):
                ctype += "; charset=utf-8"
            # Hashed build assets never change; everything else is revalidated.
            cache = "public, max-age=31536000, immutable" if rel.startswith("assets/") else "no-cache"
            self._send(200, ctype, target.read_bytes(), cache)

        def _json(self, status: int, payload: object) -> None:
            self._send(status, "application/json", json.dumps(payload).encode("utf-8"))

        def _redirect(self, location: str, headers: Mapping[str, str] | None = None) -> None:
            self.send_response(303)
            self.send_header("Location", location)
            self.send_header("Cache-Control", "no-store")
            for name, value in (headers or {}).items():
                self.send_header(name, value)
            self.end_headers()

        def _send(
            self,
            status: int,
            content_type: str,
            body: bytes,
            cache: str = "no-store",
            headers: Mapping[str, str] | None = None,
        ) -> None:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", cache)
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Referrer-Policy", "no-referrer")
            self.send_header("Content-Security-Policy", CSP)
            for name, value in (headers or {}).items():
                self.send_header(name, value)
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, format: str, *args: object) -> None:
            entry = {"severity": "INFO", "service": SERVICE, "message": format % args}
            sys.stderr.write(json.dumps(entry) + "\n")

    return Handler


def build_server(
    port: int,
    version: str,
    host: str = "0.0.0.0",
    ledger: Ledger | None = None,
    static_dir: Path = STATIC_DIR,
    auth: Auth | None = None,
    sources_path: Path | None = None,
) -> ThreadingHTTPServer:
    merged = MergedLedger(ledger or ledger_from_env(), load_sources(sources_path))
    handler = make_handler(version, merged, static_dir, auth or AuthClient.from_env())
    server = ThreadingHTTPServer((host, port), handler)
    server.daemon_threads = True
    return server


def main() -> None:
    port = int(os.environ.get("PORT", "8080"))
    version = os.environ.get("BASTION_VERSION", "dev")
    server = build_server(port, version)

    def _stop(_signum: int, _frame: object) -> None:
        threading.Thread(target=server.shutdown, daemon=True).start()

    signal.signal(signal.SIGTERM, _stop)
    signal.signal(signal.SIGINT, _stop)
    sys.stderr.write(json.dumps({"severity": "INFO", "service": SERVICE, "message": f"listening on {port}"}) + "\n")
    try:
        server.serve_forever()
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
