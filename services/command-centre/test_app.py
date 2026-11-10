"""Run with: python -m unittest discover -s services/command-centre"""

from __future__ import annotations

import json
import os
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import ClassVar

import app

NOW = datetime(2026, 11, 4, 12, 0, tzinfo=UTC)


class FixedLedger:
    source = "ledger"

    def __init__(self, events: list[app.Event], fail: bool = False) -> None:
        self._events = sorted(events, key=lambda e: e.id, reverse=True)
        self.fail = fail

    def events(self, after: int, limit: int) -> list[app.Event]:
        if self.fail:
            raise ConnectionError("down")
        return [e for e in self._events if e.id > after][:limit]

    def roster(self) -> list[dict[str, str]]:
        return app.load_roster()

    def operations(self) -> dict[str, object]:
        return {
            "generated_at": "2026-11-04T12:00:00Z",
            "attention": [{"kind": "awaiting_approval", "title": "Deploy hello", "detail": "Owner approval required"}],
            "tasks": [
                {
                    "task_id": "demo-1",
                    "title": "Run demo",
                    "status": "done",
                    "agent": "Sebastian",
                    "updated_ts": "2026-11-04T11:00:00Z",
                }
            ],
            "pr_report": None,
            "completions": [
                {"agent": "Sebastian", "kind": "task.result", "summary": "Demo finished", "ts": "2026-11-04T11:00:00Z"}
            ],
            "control": {"halted": False, "reason": None, "since": None},
        }

    def spend(self, since: str) -> list[dict[str, object]]:
        return [row for row in SPEND_ROWS if str(row["day"]) >= since]

    def approvals(self) -> list[dict[str, str]]:
        base = {"requested_by": "Lestinxor", "agent": "Sebastian", "mode": "demo", "run_url": "", "detail": ""}
        return [
            {
                **base,
                "task_id": "t-1",
                "title": "Deploy hello",
                "requested_ts": "2026-11-04T09:00:00Z",
                "updated_ts": "2026-11-04T09:00:00Z",
            },
            {
                **base,
                "task_id": "x; rm -rf ~",
                "title": "Hostile id",
                "requested_ts": "2026-11-04T10:00:00Z",
                "updated_ts": "2026-11-04T10:00:00Z",
            },
        ]


SPEND_ROWS = [
    {
        "day": "2026-11-04",
        "agent": "Sebastian",
        "runs": 3,
        "input_tokens": 9000,
        "output_tokens": 1000,
        "cost_usd": 1.5,
    },
    {"day": "2026-11-04", "agent": "Claude", "runs": 1, "input_tokens": 500, "output_tokens": 500, "cost_usd": 0.25},
    {"day": "2026-11-01", "agent": "Sebastian", "runs": 2, "input_tokens": 100, "output_tokens": 100, "cost_usd": 0.5},
    {"day": "2026-10-20", "agent": "Claude", "runs": 1, "input_tokens": 10, "output_tokens": 10, "cost_usd": 4.0},
]


class FakeAuth:
    """Controlled Supabase Auth boundary for HTTP-server tests."""

    configured = True

    def __init__(self) -> None:
        self.sent: list[tuple[str, str]] = []
        self.verified: list[tuple[str, str]] = []

    def current_user(self, access_token: str) -> dict[str, object]:
        if access_token != "valid-token":
            raise app.AuthError("invalid session")
        return {"id": "user-1", "email": "jonny@example.com"}

    def send_magic_link(self, email: str, callback_url: str) -> None:
        self.sent.append((email, callback_url))

    def verify_magic_link(self, token_hash: str, token_type: str) -> str:
        self.verified.append((token_hash, token_type))
        if token_hash != "valid-hash" or token_type != "email":
            raise app.AuthError("invalid link")
        return "valid-token"


def ev(i: int, agent: str = "Sebastian", ts: str = "2026-11-04T11:30:00Z", kind: str = "task.request") -> app.Event:
    return app.Event(i, ts, agent, kind, f"event {i}")


class ServerTest(unittest.TestCase):
    ledger: FixedLedger

    @classmethod
    def setUpClass(cls) -> None:
        cls.tmp = tempfile.TemporaryDirectory()
        cls.previous_ingest_db = app.INGEST_DB_PATH
        app.INGEST_DB_PATH = Path(cls.tmp.name) / "ingest.db"
        static = Path(cls.tmp.name)
        (static / "assets").mkdir()
        (static / "index.html").write_text("<!doctype html><title>Bastion</title>", encoding="utf-8")
        (static / "assets" / "app-abc.js").write_text("console.log(1)", encoding="utf-8")
        cls.ledger = FixedLedger([ev(1), ev(2, "Claude"), ev(3, "Quartermaster", kind="deploy.record")])
        cls.auth = FakeAuth()
        cls.server = app.build_server(
            0, "abc1234", host="127.0.0.1", ledger=cls.ledger, static_dir=static, auth=cls.auth
        )
        cls.base = f"http://127.0.0.1:{cls.server.server_address[1]}"
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls) -> None:
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join(timeout=5)
        app.INGEST_DB_PATH = cls.previous_ingest_db
        cls.tmp.cleanup()

    def setUp(self) -> None:
        self.previous_ingest_keys = os.environ.get("INGEST_AGENT_KEYS")
        self.previous_public_base = os.environ.get("BASTION_PUBLIC_URL")
        os.environ.pop("INGEST_AGENT_KEYS", None)
        os.environ.pop("BASTION_PUBLIC_URL", None)
        app.INGEST_DB_PATH.unlink(missing_ok=True)

    def tearDown(self) -> None:
        if self.previous_ingest_keys is None:
            os.environ.pop("INGEST_AGENT_KEYS", None)
        else:
            os.environ["INGEST_AGENT_KEYS"] = self.previous_ingest_keys
        if self.previous_public_base is None:
            os.environ.pop("BASTION_PUBLIC_URL", None)
        else:
            os.environ["BASTION_PUBLIC_URL"] = self.previous_public_base

    def get(
        self, path: str, headers: dict[str, str] | None = None, authenticated: bool = True
    ) -> tuple[int, dict[str, str], bytes]:
        try:
            request_headers = {"Cookie": "bastion_session=valid-token"} if authenticated else {}
            request_headers.update(headers or {})

            class NoRedirect(urllib.request.HTTPRedirectHandler):
                def redirect_request(
                    self, req: object, fp: object, code: int, msg: str, headers: object, newurl: str
                ) -> None:
                    return None

            with urllib.request.build_opener(NoRedirect()).open(
                urllib.request.Request(self.base + path, headers=request_headers), timeout=5
            ) as resp:
                return resp.status, dict(resp.headers), resp.read()
        except urllib.error.HTTPError as err:
            return err.code, dict(err.headers), err.read()

    def post(
        self, path: str, payload: dict[str, object], headers: dict[str, str] | None = None
    ) -> tuple[int, dict[str, str], bytes]:
        return self.post_bytes(path, json.dumps(payload).encode("utf-8"), headers)

    def post_bytes(
        self, path: str, body: bytes, headers: dict[str, str] | None = None
    ) -> tuple[int, dict[str, str], bytes]:
        request_headers = {"Content-Type": "application/json"}
        request_headers.update(headers or {})
        request = urllib.request.Request(
            self.base + path,
            data=body,
            headers=request_headers,
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=5) as resp:
                return resp.status, dict(resp.headers), resp.read()
        except urllib.error.HTTPError as err:
            return err.code, dict(err.headers), err.read()

    def test_ingest_assigns_agent_from_matching_key(self) -> None:
        """Removing server-side identity derivation must reject this request's assertion."""
        os.environ["INGEST_AGENT_KEYS"] = json.dumps(
            {"researcher-01": "researcher-secret", "writer-02": "writer-secret"}
        )
        status, _, body = self.post(
            "/api/ingest",
            {"agent": "writer-02", "source": "laptop", "kind": "task.completed", "summary": "Indexed 24 documents"},
            {"Authorization": "Bearer researcher-secret"},
        )
        self.assertEqual(status, 201)
        event = app.ingest_events()[0]
        self.assertEqual(event.agent, "researcher-01")
        self.assertEqual(event.summary, "Indexed 24 documents")
        self.assertEqual(set(json.loads(body)), {"id", "ts"})

    def test_ingest_rejects_missing_malformed_and_unknown_agent_keys(self) -> None:
        """Removing credential validation must make at least one request create an event."""
        os.environ["INGEST_AGENT_KEYS"] = json.dumps({"researcher-01": "researcher-secret"})
        payload = {"source": "laptop", "kind": "task.completed", "summary": "do not store"}
        for headers in ({}, {"Authorization": "Basic researcher-secret"}, {"Authorization": "Bearer unknown-secret"}):
            status, _, _ = self.post("/api/ingest", payload, headers)
            self.assertEqual(status, 401)

    def test_ingest_rejects_invalid_or_ambiguous_key_registries(self) -> None:
        """Accepting a malformed or duplicate-secret registry would weaken revocation boundaries."""
        payload = {"summary": "do not store"}
        for registry in ("not-json", json.dumps({"researcher-01": "shared", "writer-02": "shared"})):
            os.environ["INGEST_AGENT_KEYS"] = registry
            status, _, _ = self.post("/api/ingest", payload, {"Authorization": "Bearer shared"})
            self.assertEqual(status, 401)

    def test_ingest_rejects_invalid_json_and_missing_summary(self) -> None:
        """Removing payload validation would persist invalid agent reports."""
        os.environ["INGEST_AGENT_KEYS"] = json.dumps({"researcher-01": "researcher-secret"})
        headers = {"Authorization": "Bearer researcher-secret"}
        status, _, _ = self.post_bytes("/api/ingest", b"{", headers)
        self.assertEqual(status, 400)
        status, _, _ = self.post("/api/ingest", {"kind": "task.completed"}, headers)
        self.assertEqual(status, 400)
        status, _, _ = self.post("/api/ingest", {"summary": "x" * 5000}, headers)
        self.assertEqual(status, 400)

    def test_ingest_rejects_too_large_request(self) -> None:
        os.environ["INGEST_AGENT_KEYS"] = json.dumps({"researcher-01": "researcher-secret"})
        status, _, _ = self.post_bytes(
            "/api/ingest",
            (json.dumps({"summary": "x" * 70_000}).encode("utf-8")),
            {"Authorization": "Bearer researcher-secret"},
        )
        self.assertEqual(status, 413)

    def test_ingested_event_appears_in_feed(self) -> None:
        """Removing the local-ledger merger must hide accepted agent reports from the dashboard."""
        os.environ["INGEST_AGENT_KEYS"] = json.dumps({"Sebastian": "sebastian-secret"})
        status, _, _ = self.post(
            "/api/ingest",
            {
                "source": "desktop",
                "kind": "task.completed",
                "summary": "Connected to Bastion",
                "ts": "2026-11-07T13:30:00Z",
            },
            {"Authorization": "Bearer sebastian-secret"},
        )
        self.assertEqual(status, 201)
        status, _, body = self.get("/api/feed?limit=4")
        self.assertEqual(status, 200)
        events = json.loads(body)["events"]
        self.assertIn(
            {
                "id": 10_000_001,
                "ts": "2026-11-07T13:30:00Z",
                "agent": "Sebastian",
                "kind": "task.completed",
                "summary": "Connected to Bastion",
            },
            events,
        )
        self.assertEqual(len({event["id"] for event in events}), len(events))

    def test_health(self) -> None:
        status, headers, body = self.get("/health")
        self.assertEqual(status, 200)
        self.assertEqual(headers["Content-Type"], "application/json")
        self.assertEqual(json.loads(body), {"status": "ok", "service": "command-centre", "version": "abc1234"})

    def test_api_requires_a_verified_supabase_session(self) -> None:
        self.assertEqual(self.get("/api/feed", authenticated=False)[0], 401)
        status, _, body = self.get("/api/feed", {"Cookie": "bastion_session=valid-token"})
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body)["source"], "ledger")

    def test_magic_link_and_callback_create_a_secure_session(self) -> None:
        status, _, _ = self.post("/auth/magic-link", {"email": "jonny@example.com"})
        self.assertEqual(status, 202)
        self.assertEqual(
            self.auth.sent,
            [
                (
                    "jonny@example.com",
                    f"https://{self.server.server_address[0]}:{self.server.server_address[1]}/auth/callback",
                )
            ],
        )
        status, headers, _ = self.get("/auth/callback?token_hash=valid-hash&type=email", authenticated=False)
        self.assertEqual(status, 303)
        self.assertEqual(headers["Location"], "/")
        self.assertIn("HttpOnly", headers["Set-Cookie"])
        self.assertIn("Secure", headers["Set-Cookie"])
        self.assertIn("SameSite=Lax", headers["Set-Cookie"])
        self.assertEqual(headers["Cache-Control"], "no-store")

    def test_magic_link_uses_configured_public_origin(self) -> None:
        os.environ["BASTION_PUBLIC_URL"] = "https://dashboard.example.com"
        status, _, _ = self.post("/auth/magic-link", {"email": "jonny@example.com"})
        self.assertEqual(status, 202)
        self.assertEqual(self.auth.sent[-1][1], "https://dashboard.example.com/auth/callback")

    def test_invalid_callback_never_sets_a_session_cookie(self) -> None:
        status, headers, _ = self.get("/auth/callback?token_hash=wrong&type=email", authenticated=False)
        self.assertEqual(status, 303)
        self.assertEqual(headers["Location"], "/login?error=link")
        self.assertNotIn("Set-Cookie", headers)

    def test_feed_newest_first(self) -> None:
        status, _, body = self.get("/api/feed")
        data = json.loads(body)
        self.assertEqual(status, 200)
        self.assertEqual(data["source"], "ledger")
        self.assertEqual([e["id"] for e in data["events"]], [3, 2, 1])
        self.assertEqual(set(data["events"][0]), {"id", "ts", "agent", "kind", "summary"})

    def test_feed_after_and_limit(self) -> None:
        self.assertEqual([e["id"] for e in json.loads(self.get("/api/feed?after=1")[2])["events"]], [3, 2])
        self.assertEqual([e["id"] for e in json.loads(self.get("/api/feed?limit=1")[2])["events"]], [3])

    def test_feed_ignores_bad_params(self) -> None:
        status, _, body = self.get("/api/feed?after=abc&limit=-5")
        self.assertEqual(status, 200)
        self.assertEqual(len(json.loads(body)["events"]), 1)  # limit clamps to at least 1

    def test_roster_lists_every_agent(self) -> None:
        status, _, body = self.get("/api/roster")
        agents = json.loads(body)["agents"]
        self.assertEqual(status, 200)
        self.assertEqual([a["name"] for a in agents], [a["name"] for a in app.load_roster()])
        self.assertEqual(set(agents[0]), {"name", "role", "runs_on", "last_seen", "status"})

    def test_operations_is_bounded_and_never_exposes_output(self) -> None:
        status, _, body = self.get("/api/operations")
        data = json.loads(body)
        self.assertEqual(status, 200)
        self.assertEqual(data["source"], "ledger")
        self.assertEqual(data["attention"][0]["kind"], "awaiting_approval")
        self.assertNotIn("output", data["tasks"][0])

    def test_approvals_lists_pending_tasks_with_safe_linx_commands(self) -> None:
        status, _, body = self.get("/api/approvals")
        data = json.loads(body)
        self.assertEqual(status, 200)
        self.assertEqual(data["source"], "ledger")
        first, hostile = data["approvals"]
        self.assertEqual(first["task_id"], "t-1")
        self.assertEqual(first["approve_command"], "linx approve t-1")
        self.assertEqual(first["reject_command"], 'linx reject t-1 --reason "<why>"')
        self.assertIsNone(hostile["approve_command"])
        self.assertIsNone(hostile["reject_command"])

    def test_approvals_requires_a_session(self) -> None:
        self.assertEqual(self.get("/api/approvals", authenticated=False)[0], 401)

    def test_spend_summarises_the_ledger(self) -> None:
        status, _, body = self.get("/api/spend")
        data = json.loads(body)
        self.assertEqual(status, 200)
        self.assertEqual(data["source"], "ledger")
        self.assertEqual(len(data["daily"]), app.SPEND_DAYS)
        self.assertEqual(
            set(data),
            {
                "source",
                "generated_at",
                "currency",
                "reporting",
                "caps",
                "today",
                "last_7d",
                "last_30d",
                "daily",
                "agents",
            },
        )

    def test_spend_requires_a_session(self) -> None:
        self.assertEqual(self.get("/api/spend", authenticated=False)[0], 401)

    def test_ledger_failure_is_502_not_a_crash(self) -> None:
        self.ledger.fail = True
        try:
            status, _, body = self.get("/api/feed")
        finally:
            self.ledger.fail = False
        self.assertEqual(status, 502)
        self.assertEqual(json.loads(body), {"error": "ledger unavailable"})

    def test_unknown_api_path_is_404(self) -> None:
        self.assertEqual(self.get("/api/nope")[0], 404)

    def test_serves_app_shell_and_client_routes(self) -> None:
        for path in ("/", "/roster"):
            status, headers, body = self.get(path)
            self.assertEqual(status, 200, path)
            self.assertTrue(headers["Content-Type"].startswith("text/html"))
            self.assertIn(b"Bastion", body)

    def test_hashed_assets_are_cached(self) -> None:
        status, headers, _ = self.get("/assets/app-abc.js")
        self.assertEqual(status, 200)
        self.assertIn("immutable", headers["Cache-Control"])

    def test_missing_file_is_404(self) -> None:
        self.assertEqual(self.get("/assets/missing.js")[0], 404)

    def test_no_path_traversal(self) -> None:
        for path in ("/../secret.txt", "/%2e%2e/secret.txt", "/assets/%2e%2e/%2e%2e/secret.txt"):
            status, _, body = self.get(path)
            self.assertNotEqual(body, b"", path)
            self.assertIn(status, (200, 404), path)
            self.assertNotIn(b"secret", body.lower(), path)

    def test_security_headers(self) -> None:
        _, headers, _ = self.get("/")
        self.assertEqual(headers["X-Content-Type-Options"], "nosniff")
        self.assertIn("default-src 'self'", headers["Content-Security-Policy"])
        self.assertIn("frame-ancestors 'none'", headers["Content-Security-Policy"])
        self.assertNotIn("Python", headers.get("Server", ""))


class RosterStatusTest(unittest.TestCase):
    def test_status_from_last_event(self) -> None:
        roster = [{"name": n, "role": "r", "runs_on": "x"} for n in ("A", "B", "C", "D")]
        events = [
            ev(4, "A", ts=(NOW - timedelta(minutes=5)).strftime("%Y-%m-%dT%H:%M:%SZ")),
            ev(3, "B", ts=(NOW - timedelta(hours=5)).strftime("%Y-%m-%dT%H:%M:%SZ")),
            ev(2, "C", ts=(NOW - timedelta(days=3)).strftime("%Y-%m-%dT%H:%M:%SZ")),
            ev(1, "A", ts=(NOW - timedelta(days=9)).strftime("%Y-%m-%dT%H:%M:%SZ")),
        ]
        got = {a["name"]: (a["status"], a["last_seen"]) for a in app.roster_with_status(roster, events, NOW)}
        self.assertEqual(got["A"], ("active", events[0].ts))  # newest sighting wins
        self.assertEqual(got["B"][0], "idle")
        self.assertEqual(got["C"][0], "silent")
        self.assertEqual(got["D"], ("silent", None))


class DemoLedgerTest(unittest.TestCase):
    def test_newest_first_and_moves_with_the_clock(self) -> None:
        clock = [NOW]
        demo = app.DemoLedger(lambda: clock[0])
        first = demo.events(0, 5)
        self.assertEqual(len(first), 5)
        self.assertEqual([e.id for e in first], sorted((e.id for e in first), reverse=True))
        self.assertEqual(demo.events(first[0].id, 50), [])
        clock[0] = NOW + app.DEMO_STEP * 2
        self.assertEqual([e.id for e in demo.events(first[0].id, 50)], [first[0].id + 2, first[0].id + 1])

    def test_demo_spend_covers_the_window(self) -> None:
        rows = app.DemoLedger(lambda: NOW).spend("2026-10-06")
        self.assertTrue(rows)
        self.assertTrue(all(str(r["day"]) >= "2026-10-06" and str(r["day"]) <= "2026-11-04" for r in rows))

    def test_demo_has_a_pending_approval(self) -> None:
        items = app.DemoLedger(lambda: NOW).approvals()
        self.assertEqual([i["task_id"] for i in items], ["demo-approval"])

    def test_agents_are_on_the_roster(self) -> None:
        names = {a["name"] for a in app.load_roster()}
        self.assertTrue({agent for agent, _, _ in app._DEMO_SCRIPT} <= names)


class FakeConn:
    def __init__(self, rows: list[list[object]]) -> None:
        self.rows = rows
        self.calls: list[tuple[str, dict[str, object]]] = []
        self.closed = False

    def run(self, sql: str, **params: object) -> list[list[object]]:
        self.calls.append((sql, params))
        return self.rows

    def close(self) -> None:
        self.closed = True


class SpendSummaryTest(unittest.TestCase):
    caps: ClassVar[dict[str, object]] = {
        "daily_usd": 2.0,
        "default_agent_daily_usd": 1.0,
        "agent_daily_usd": {"Claude": 5.0},
    }
    today = datetime(2026, 11, 4, tzinfo=UTC).date()

    def summary(self, rows: list[dict[str, object]] | None = None) -> dict[str, object]:
        return app.summarise_spend(SPEND_ROWS if rows is None else rows, self.caps, self.today)

    def test_windows_are_today_7_and_30_days(self) -> None:
        s = self.summary()
        self.assertEqual(s["today"]["cost_usd"], 1.75)
        self.assertEqual(s["today"]["runs"], 4)
        self.assertEqual(s["today"]["input_tokens"], 9500)
        self.assertEqual(s["last_7d"]["cost_usd"], 2.25)
        self.assertEqual(s["last_30d"]["cost_usd"], 6.25)

    def test_daily_series_is_zero_filled_oldest_first(self) -> None:
        daily = self.summary()["daily"]
        self.assertEqual(len(daily), app.SPEND_DAYS)
        self.assertEqual(daily[0]["day"], "2026-10-06")
        self.assertEqual(daily[-1]["day"], "2026-11-04")
        self.assertEqual(daily[-1]["cost_usd"], 1.75)
        self.assertEqual(daily[-2]["cost_usd"], 0)

    def test_caps_status(self) -> None:
        s = self.summary()
        self.assertEqual((s["today"]["cap_usd"], s["today"]["cap_status"]), (2.0, "near"))
        agents = {a["agent"]: a for a in s["agents"]}
        self.assertEqual((agents["Sebastian"]["cap_usd"], agents["Sebastian"]["cap_status"]), (1.0, "over"))
        self.assertEqual((agents["Claude"]["cap_usd"], agents["Claude"]["cap_status"]), (5.0, "ok"))
        self.assertEqual([a["agent"] for a in s["agents"]], ["Claude", "Sebastian"])  # by 30-day spend
        no_caps = app.summarise_spend(SPEND_ROWS, {}, self.today)
        self.assertEqual((no_caps["today"]["cap_usd"], no_caps["today"]["cap_status"]), (None, "none"))

    def test_rows_outside_the_window_and_bad_values_are_ignored(self) -> None:
        rows = [
            {"day": "2026-09-01", "agent": "Old", "runs": 1, "input_tokens": 1, "output_tokens": 1, "cost_usd": 9},
            {
                "day": "2026-11-04",
                "agent": "Odd",
                "runs": None,
                "input_tokens": "x",
                "output_tokens": -5,
                "cost_usd": "nan",
            },
        ]
        s = self.summary(rows)
        self.assertEqual(s["last_30d"]["cost_usd"], 0)
        self.assertEqual(s["today"]["output_tokens"], 0)
        self.assertEqual([a["agent"] for a in s["agents"]], ["Odd"])

    def test_reporting_flag(self) -> None:
        self.assertTrue(self.summary()["reporting"])
        self.assertFalse(self.summary([])["reporting"])

    def test_missing_caps_file_means_no_caps(self) -> None:
        caps = app.load_caps(Path("/nonexistent/caps.json"))
        self.assertEqual(caps, {"daily_usd": None, "default_agent_daily_usd": None, "agent_daily_usd": {}})

    def test_caps_file_is_valid(self) -> None:
        caps = app.load_caps()
        self.assertGreater(caps["daily_usd"], 0)
        self.assertGreater(caps["default_agent_daily_usd"], 0)


class LinxCommandTest(unittest.TestCase):
    def test_only_plain_task_ids_get_a_command(self) -> None:
        for tid in ("t-1", "o0.audit:2026-11-04", "A_b"):
            self.assertEqual(app.linx_commands(tid)["approve_command"], f"linx approve {tid}")
        for tid in ("", "-h", "--reason", "a b", "a;b", "a$(x)", "a`x`", "a\nb", "x" * 81, "\u00e9t\u00e9"):
            self.assertEqual(app.linx_commands(tid), {"approve_command": None, "reject_command": None}, tid)


class PostgresLedgerTest(unittest.TestCase):
    def test_approvals_query_reads_only_pending_tasks_oldest_first(self) -> None:
        row = [
            "t-1",
            "Deploy hello",
            "Lestinxor",
            "Sebastian",
            "demo",
            None,
            "2026-11-04T09:00:00Z",
            "2026-11-04T09:00:00Z",
            None,
        ]
        conn = FakeConn([row])
        items = app.PostgresLedger(lambda: conn).approvals()
        sql = conn.calls[0][0]
        self.assertIn("FROM bastion.dashboard_tasks", sql)
        self.assertIn("status = 'awaiting_approval'", sql)
        self.assertIn("ORDER BY requested_ts ASC", sql)
        self.assertEqual(items[0]["task_id"], "t-1")
        self.assertEqual(items[0]["run_url"], "")
        self.assertNotIn("output", items[0])
        self.assertIn(f"SELECT {', '.join(app.APPROVAL_FIELDS)} FROM", sql)

    def test_operations_task_query_excludes_output(self) -> None:
        conn = FakeConn(
            [
                [
                    "task-1",
                    "Ship overview",
                    "Jonny",
                    "Sebastian",
                    "done",
                    "demo",
                    "https://github.com/example/run",
                    "2026-11-04T11:00:00Z",
                    "2026-11-04T11:01:00Z",
                    "2026-11-04T11:02:00Z",
                    "2026-11-04T11:02:00Z",
                    "3 checks passed",
                ]
            ]
        )
        tasks = app.PostgresLedger(lambda: conn).dashboard_tasks()
        self.assertEqual(tasks[0]["status"], "done")
        self.assertNotIn("output", tasks[0])
        self.assertIn("FROM bastion.dashboard_tasks", conn.calls[0][0])

    def test_events_query_and_mapping(self) -> None:
        conn = FakeConn([[9, "2026-11-04T11:00:00Z", "Sebastian", "task.request", "Run demo"]])
        events = app.PostgresLedger(lambda: conn).events(after=7, limit=20)
        self.assertEqual(events, [app.Event(9, "2026-11-04T11:00:00Z", "Sebastian", "task.request", "Run demo")])
        sql, params = conn.calls[0]
        self.assertIn("FROM bastion.feed", sql)
        self.assertNotIn("unisync_", sql)  # never the raw tables
        self.assertEqual(params, {"after": 7, "limit": 20})
        self.assertTrue(conn.closed)

    def test_roster_mapping(self) -> None:
        conn = FakeConn([["Claude", "worker", ["Bastion", "other"]], ["Jonny", "owner", []]])
        roster = app.PostgresLedger(lambda: conn).roster()
        self.assertEqual(
            roster,
            [
                {"name": "Claude", "role": "worker", "runs_on": "Bastion, other"},
                {"name": "Jonny", "role": "owner", "runs_on": "-"},
            ],
        )
        self.assertIn("FROM bastion.roster", conn.calls[0][0])

    def test_connection_closed_on_error(self) -> None:
        conn = FakeConn([])

        def boom(sql: str, **params: object) -> list[list[object]]:
            raise ConnectionError("down")

        conn.run = boom  # type: ignore[method-assign]
        with self.assertRaises(ConnectionError):
            app.PostgresLedger(lambda: conn).events(0, 1)
        self.assertTrue(conn.closed)


class PostgresSpendTest(unittest.TestCase):
    def test_spend_query_and_mapping(self) -> None:
        from decimal import Decimal

        conn = FakeConn([["2026-11-04", "Sebastian", 3, 9000, 1000, Decimal("1.500000")]])
        rows = app.PostgresLedger(lambda: conn).spend("2026-10-06")
        self.assertIn("FROM bastion.spend_daily WHERE day >= :since", conn.calls[0][0])
        self.assertEqual(conn.calls[0][1], {"since": "2026-10-06"})
        self.assertEqual(rows[0]["cost_usd"], 1.5)
        self.assertEqual(rows[0]["agent"], "Sebastian")


class LedgerConfigTest(unittest.TestCase):
    def test_demo_without_config(self) -> None:
        self.assertIsInstance(app.ledger_from_env({}), app.DemoLedger)

    def test_postgres_with_dsn(self) -> None:
        ledger = app.ledger_from_env({"LEDGER_DSN": "postgresql://bastion_reader.ref:pw@host.example:5432/postgres"})
        self.assertIsInstance(ledger, app.PostgresLedger)

    def test_rejects_non_postgres_dsn(self) -> None:
        with self.assertRaises(ValueError):
            app.ledger_from_env({"LEDGER_DSN": "https://x.supabase.co"})


if __name__ == "__main__":
    unittest.main()

