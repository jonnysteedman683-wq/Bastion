"""command-centre: Bastion's private dashboard. Serves the built web app and a small read-only API.

    GET /health        -> {"status": "ok", "service": "command-centre", "version": "<git sha>"}
    GET /api/roster    -> {"source": ..., "agents": [{name, role, runs_on, last_seen, status}]}
    GET /api/feed      -> {"source": ..., "events": [{id, ts, agent, kind, summary}]}, newest first
                          ?after=<id> returns only newer events; ?limit=<n> caps the count (max 200)
    GET /*             -> the web app (web/dist), with index.html for client-side routes

The browser only ever talks to this server. The ledger is read server-side:
  - LEDGER_URL and LEDGER_READ_KEY set: the UniSync ledger in Supabase, via its REST API, with a key for
    the read-only bastion_reader role (see ledger/schema.sql). The key comes from Secret Manager.
  - otherwise: a demo ledger, generated from the clock so the feed visibly moves. The UI labels it.

Standard library only. Logs one JSON line per request to stderr. Shuts down cleanly on SIGTERM.
"""

from __future__ import annotations

import json
import mimetypes
import os
import signal
import sys
import threading
import urllib.parse
import urllib.request
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Protocol

SERVICE = "command-centre"
HERE = Path(__file__).resolve().parent
STATIC_DIR = HERE / "web" / "dist"
ROSTER_PATH = HERE / "roster.json"
MAX_LIMIT = 200
ACTIVE_WITHIN = timedelta(hours=1)
IDLE_WITHIN = timedelta(days=1)

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


# --- Demo ledger -----------------------------------------------------------------------------

DEMO_STEP = timedelta(minutes=3)
DEMO_EPOCH = datetime(2026, 1, 1, tzinfo=UTC)
_DEMO_SCRIPT: tuple[tuple[str, str, str], ...] = (
    ("Sebastian", "task.request", "Summon: run the nightly demo"),
    ("Sebastian", "task.result", "Demo finished: 3 checks passed"),
    ("Claude", "task.request", "Draft backlog item S3 (summon button)"),
    ("Claude", "task.result", "Opened a pull request for review"),
    ("Quartermaster", "deploy.record", "hello deployed; health check passed, traffic shifted"),
    ("Jules", "task.request", "Pin the python base image by digest"),
    ("Jules", "memory.write", "Learned: Cloud Run reserves paths ending in z"),
    ("Vortex", "task.result", "Owner key approved a production deploy"),
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

    def events(self, after: int, limit: int) -> list[Event]:
        latest = int((self._clock() - DEMO_EPOCH) / DEMO_STEP)
        start = max(after + 1, latest - limit + 1, 0)
        return [self._event(n) for n in range(latest, start - 1, -1)]


# --- UniSync ledger (Supabase) -----------------------------------------------------------------


class SupabaseLedger:
    """Reads the ledger_events table through Supabase's REST API with a read-only key."""

    source = "ledger"

    def __init__(self, url: str, key: str, timeout: float = 5.0) -> None:
        if not url.startswith("https://"):
            raise ValueError("LEDGER_URL must be an https:// Supabase project URL")
        self._base = url.rstrip("/") + "/rest/v1/ledger_events"
        self._key = key
        self._timeout = timeout

    def request_url(self, after: int, limit: int) -> str:
        query = urllib.parse.urlencode(
            {"select": "id,ts,agent,kind,summary", "id": f"gt.{after}", "order": "id.desc", "limit": str(limit)}
        )
        return f"{self._base}?{query}"

    def events(self, after: int, limit: int) -> list[Event]:
        req = urllib.request.Request(  # noqa: S310 - URL is fixed to https:// in __init__
            self.request_url(after, limit),
            headers={"apikey": self._key, "Authorization": f"Bearer {self._key}", "Accept": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=self._timeout) as resp:  # noqa: S310
            rows = json.load(resp)
        return [Event(int(r["id"]), str(r["ts"]), str(r["agent"]), str(r["kind"]), str(r["summary"])) for r in rows]


def ledger_from_env(env: dict[str, str] | None = None) -> Ledger:
    env = dict(os.environ) if env is None else env
    url, key = env.get("LEDGER_URL", ""), env.get("LEDGER_READ_KEY", "")
    if url and key:
        return SupabaseLedger(url, key)
    return DemoLedger()


# --- Roster ------------------------------------------------------------------------------------


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


def make_handler(
    version: str, ledger: Ledger, roster: list[dict[str, str]], static_dir: Path
) -> type[BaseHTTPRequestHandler]:
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
            elif path == "/api/feed":
                after = _parse_int(query.get("after", [None])[0], 0, 0, 2**62)
                limit = _parse_int(query.get("limit", [None])[0], 50, 1, MAX_LIMIT)
                self._ledger_call(
                    lambda: {"source": ledger.source, "events": [asdict(e) for e in ledger.events(after, limit)]}
                )
            elif path == "/api/roster":
                self._ledger_call(
                    lambda: {
                        "source": ledger.source,
                        "agents": roster_with_status(roster, ledger.events(0, MAX_LIMIT), datetime.now(UTC)),
                    }
                )
            elif path.startswith("/api/"):
                self._json(404, {"error": "not found"})
            else:
                self._static(path)

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

        def _send(self, status: int, content_type: str, body: bytes, cache: str = "no-store") -> None:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", cache)
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Referrer-Policy", "no-referrer")
            self.send_header("Content-Security-Policy", CSP)
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
) -> ThreadingHTTPServer:
    handler = make_handler(version, ledger or ledger_from_env(), load_roster(), static_dir)
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
