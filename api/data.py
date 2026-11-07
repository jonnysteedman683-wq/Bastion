"""Vercel function: GET /api/data -> the command centre's five public, read-only datasets.

    -> {"stats": {"status": "ok", "data": [...]}, "roster": ..., "feed": ..., "tasks": ..., "control": ...}

Reads the UniSync ledger as the read-only `bastion_reader` role (LEDGER_DSN, a Vercel environment
variable) through the private `bastion` views only. The queries are fixed here; nothing from the
request reaches SQL. See services/command-centre/ledger/*.sql for the views and the role.
"""

from __future__ import annotations

import json
import os
import ssl
import urllib.parse
from collections.abc import Callable
from http.server import BaseHTTPRequestHandler
from typing import Any

QUERIES: dict[str, str] = {
    "stats": (
        "select (select count(*) from bastion.roster)::int as agents, "
        "(select count(distinct agent) from bastion.feed where ts::timestamptz > now() - interval '24 hours')::int "
        "as active_24h, "
        "(select count(*) from bastion.feed where ts::timestamptz > now() - interval '7 days')::int as events_7d, "
        "(select count(*) from bastion.tasks where status = 'failed' "
        "and finished_ts::timestamptz > now() - interval '7 days')::int as failures_7d, "
        "(select count(*) from bastion.tasks where status = 'done' "
        "and finished_ts::timestamptz > now() - interval '7 days')::int as done_7d, "
        "(select max(ts) from bastion.feed) as last_event, (select max(id) from bastion.feed)::int as ledger_head"
    ),
    "roster": (
        "select r.name, r.role, coalesce(array_to_string(r.repos, ', '), '') as repos, max(f.ts) as last_seen, "
        "count(f.id)::int as events, case when max(f.ts) is null then 'silent' "
        "when max(f.ts)::timestamptz > now() - interval '1 hour' then 'active' "
        "when max(f.ts)::timestamptz > now() - interval '1 day' then 'idle' else 'silent' end as status "
        "from bastion.roster r left join bastion.feed f on f.agent = r.name "
        "group by r.name, r.role, r.repos order by (max(f.ts) is null), max(f.ts) desc, r.name"
    ),
    "feed": "select id, ts, agent, kind, summary from bastion.feed order by id desc limit 200",
    "tasks": (
        "select task_id, title, requested_by, agent, status, mode, run_url, requested_ts, claimed_ts, "
        "finished_ts, updated_ts, detail, output from bastion.tasks order by updated_seq desc limit 100"
    ),
    "control": "select halted, reason, since from bastion.control",
}


def connect_from_dsn(dsn: str, timeout: float = 8.0) -> Any:
    """A pg8000 connection for a postgresql:// DSN, over TLS."""
    import pg8000.native  # type: ignore[import-not-found]  # installed by Vercel from requirements.txt

    parts = urllib.parse.urlsplit(dsn)
    if parts.scheme not in ("postgres", "postgresql") or not parts.hostname:
        raise ValueError("LEDGER_DSN must be a postgresql:// URL")
    return pg8000.native.Connection(
        user=urllib.parse.unquote(parts.username or ""),
        password=urllib.parse.unquote(parts.password or ""),
        host=parts.hostname,
        port=parts.port or 5432,
        database=parts.path.lstrip("/") or "postgres",
        ssl_context=ssl.create_default_context(),
        timeout=timeout,
    )


def load(connect: Callable[[], Any]) -> dict[str, dict[str, Any]]:
    """Run every query on one connection. A failing query is reported on its own; the rest still load."""
    conn = connect()
    out: dict[str, dict[str, Any]] = {}
    try:
        for name, sql in QUERIES.items():
            try:
                rows = conn.run(sql)
                cols = [c["name"] for c in conn.columns]
                out[name] = {"status": "ok", "data": [dict(zip(cols, r, strict=False)) for r in rows]}
            except Exception:  # one view's failure must not hide the others
                out[name] = {"status": "error", "data": [], "message": "query failed"}
    finally:
        conn.close()
    return out


class handler(BaseHTTPRequestHandler):  # noqa: N801 - Vercel's Python runtime looks for this name
    def do_GET(self) -> None:
        dsn = os.environ.get("LEDGER_DSN", "")
        if not dsn:
            self._send(503, {"error": "LEDGER_DSN not configured"})
            return
        try:
            self._send(200, load(lambda: connect_from_dsn(dsn)))
        except Exception as err:  # never leak connection details
            self._send(502, {"error": "ledger unavailable", "type": type(err).__name__})

    def _send(self, status: int, payload: object) -> None:
        body = json.dumps(payload, default=str).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(body)
