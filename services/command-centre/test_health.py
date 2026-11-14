"""Health view sources: Healthchecks.io (G3c). Run with: python -m unittest discover -s services/command-centre"""

from __future__ import annotations

import json
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

import app
from test_app import FakeAuth, FixedLedger

KEY = "hc-read-only-key-123"


class FakeFetch:
    """Answers GETs from a {url: (status, body)} table and records every call."""

    def __init__(self, routes: dict[str, tuple[int, object]] | None = None) -> None:
        self.routes = routes or {}
        self.calls: list[tuple[str, dict[str, str]]] = []

    def __call__(self, url: str, headers: dict[str, str]) -> tuple[int, bytes]:
        self.calls.append((url, headers))
        status, body = self.routes.get(url, (404, b""))
        return status, body if isinstance(body, bytes) else json.dumps(body).encode()


def hc_check(name: str, status: str, **extra: object) -> dict[str, object]:
    return {
        "name": name,
        "slug": name,
        "status": status,
        "last_ping": "2026-11-14T09:00:00+00:00",
        "next_ping": "2026-11-14T10:00:00+00:00",
        "grace": 3600,
        "tags": "fleet",
        "unique_key": "abc",
        **extra,
    }


class ChecksReportTest(unittest.TestCase):
    def test_not_configured_without_a_key_and_nothing_fetched(self) -> None:
        fetch = FakeFetch()
        report = app.checks_report({}, fetch)

        self.assertEqual(report, {"configured": False, "checks": [], "counts": {}})
        self.assertEqual(fetch.calls, [])

    def test_lists_checks_worst_first_with_counts(self) -> None:
        body = {"checks": [hc_check("b-up", "up"), hc_check("a-down", "down"), hc_check("c-late", "grace")]}
        fetch = FakeFetch({app.HEALTHCHECKS_API: (200, body)})
        report = app.checks_report({"HEALTHCHECKS_API_KEY": KEY}, fetch)

        self.assertTrue(report["configured"])
        self.assertEqual([c["slug"] for c in report["checks"]], ["a-down", "c-late", "b-up"])
        self.assertEqual(report["counts"], {"down": 1, "grace": 1, "up": 1})
        url, headers = fetch.calls[0]
        self.assertEqual(url, app.HEALTHCHECKS_API)
        self.assertEqual(headers["X-Api-Key"], KEY)

    def test_only_display_fields_pass_and_unknown_status_is_unknown(self) -> None:
        body = {"checks": [hc_check("x", "exploded", ping_url="https://hc-ping.com/secret-uuid", desc="internal")]}
        report = app.checks_report({"HEALTHCHECKS_API_KEY": KEY}, FakeFetch({app.HEALTHCHECKS_API: (200, body)}))

        (check,) = report["checks"]
        self.assertEqual(set(check), {"name", "slug", "status", "last_ping", "next_ping", "grace_seconds", "tags"})
        self.assertEqual(check["status"], "unknown")
        self.assertNotIn("secret-uuid", json.dumps(report))

    def test_errors_are_reported_without_the_key(self) -> None:
        for fetch in (
            FakeFetch({app.HEALTHCHECKS_API: (401, b"")}),
            FakeFetch({app.HEALTHCHECKS_API: (200, b"not json")}),
            FakeFetch({app.HEALTHCHECKS_API: (200, {"checks": "nope"})}),
        ):
            with self.subTest(fetch=fetch.routes):
                report = app.checks_report({"HEALTHCHECKS_API_KEY": KEY}, fetch)
                self.assertTrue(report["configured"])
                self.assertEqual(report["checks"], [])
                self.assertIn("error", report)
                self.assertNotIn(KEY, json.dumps(report))

    def test_network_failure_is_an_error_not_a_crash(self) -> None:
        def down(url: str, headers: dict[str, str]) -> tuple[int, bytes]:
            raise OSError("unreachable")

        report = app.checks_report({"HEALTHCHECKS_API_KEY": KEY}, down)
        self.assertEqual(report["error"], "Healthchecks.io unreachable")


class HealthSourcesCacheTest(unittest.TestCase):
    def test_results_are_cached_for_a_minute(self) -> None:
        fetch = FakeFetch({app.HEALTHCHECKS_API: (200, {"checks": []})})
        clock = [0.0]
        sources = app.HealthSources({"HEALTHCHECKS_API_KEY": KEY}, fetch, clock=lambda: clock[0])

        sources.checks()
        sources.checks()
        self.assertEqual(len(fetch.calls), 1)
        clock[0] = app.HEALTH_CACHE_SECONDS + 1
        sources.checks()
        self.assertEqual(len(fetch.calls), 2)


class HealthRoutesTest(unittest.TestCase):
    server: Any

    @classmethod
    def setUpClass(cls) -> None:
        cls.tmp = tempfile.TemporaryDirectory()
        static = Path(cls.tmp.name)
        (static / "index.html").write_text("<!doctype html>", encoding="utf-8")
        body = {"checks": [hc_check("bastion-steward", "up")]}
        cls.health = app.HealthSources({"HEALTHCHECKS_API_KEY": KEY}, FakeFetch({app.HEALTHCHECKS_API: (200, body)}))
        cls.server = app.build_server(
            0, "t", host="127.0.0.1", ledger=FixedLedger([]), static_dir=static, auth=FakeAuth(), health=cls.health
        )
        cls.base = f"http://127.0.0.1:{cls.server.server_address[1]}"
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls) -> None:
        cls.server.shutdown()
        cls.server.server_close()
        cls.tmp.cleanup()

    def get(self, path: str, authenticated: bool = True) -> tuple[int, dict[str, Any]]:
        headers = {"Cookie": "bastion_session=valid-token"} if authenticated else {}
        try:
            with urllib.request.urlopen(urllib.request.Request(self.base + path, headers=headers), timeout=5) as r:
                return r.status, json.loads(r.read())
        except urllib.error.HTTPError as err:
            return err.code, json.loads(err.read() or b"{}")

    def test_checks_route(self) -> None:
        status, body = self.get("/api/checks")
        self.assertEqual(status, 200)
        self.assertEqual(body["checks"][0]["slug"], "bastion-steward")

    def test_checks_route_requires_a_session(self) -> None:
        self.assertEqual(self.get("/api/checks", authenticated=False)[0], 401)


if __name__ == "__main__":
    unittest.main()
