"""Deadman registry and Healthchecks.io sync. Run with: python -m unittest discover -s tests -t ."""

from __future__ import annotations

import io
import json
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Any

from tools import deadman

GOOD: dict[str, Any] = {
    "slug": "aih-analytics-archive",
    "repo": "jonnysteedman683-wq/AIH",
    "workflow": "analytics-archive.yml",
    "schedule": "*/20 * * * *",
    "grace": 2400,
}


def errors_for(**overrides: Any) -> list[str]:
    return deadman.validate({"checks": [{**GOOD, **overrides}]})[1]


class FakePost:
    def __init__(self, statuses: list[int]) -> None:
        self.statuses = list(statuses)
        self.calls: list[tuple[str, dict[str, Any], str]] = []

    def __call__(self, url: str, body: dict[str, Any], api_key: str) -> tuple[int, str]:
        self.calls.append((url, body, api_key))
        return self.statuses.pop(0), '{"detail": "nope"}'


def run(*argv: str, env: dict[str, str] | None = None, post: Any = None) -> tuple[int, str]:
    out = io.StringIO()
    kwargs: dict[str, Any] = {"env": env or {}, "out": out}
    if post is not None:
        kwargs["post"] = post
    return deadman.main(list(argv), **kwargs), out.getvalue()


class RegistryTest(unittest.TestCase):
    def test_repo_registry_is_valid_and_covers_every_scheduled_job(self) -> None:
        checks, errors = deadman.load()
        self.assertEqual(errors, [])
        self.assertEqual(len(checks), 8)
        self.assertEqual(len({c.slug for c in checks}), 8)

    def test_good_check_parses_with_utc_default(self) -> None:
        checks, errors = deadman.validate({"checks": [GOOD]})
        self.assertEqual(errors, [])
        self.assertEqual(checks[0].tz, "UTC")

    def test_rejects_bad_fields(self) -> None:
        cases = {
            "slug": ("Bad Slug", "slug must be"),
            "repo": ("no-owner", "repo must be owner/name"),
            "workflow": ("../evil.sh", "workflow must be"),
            "schedule": ("@daily", "schedule must be"),
            "tz": ("Mars/Olympus", "tz must be"),
        }
        for field, (value, message) in cases.items():
            with self.subTest(field=field):
                errs = errors_for(**{field: value})
                self.assertEqual(len(errs), 1, errs)
                self.assertIn(message, errs[0])

    def test_grace_bounds_and_types(self) -> None:
        for grace in (59, 31_536_001, "600", True, 60.5):
            with self.subTest(grace=grace):
                self.assertIn("grace must be", " ".join(errors_for(grace=grace)))
        self.assertEqual(errors_for(grace=60), [])

    def test_rejects_duplicates_unknown_fields_and_bad_shape(self) -> None:
        _, errs = deadman.validate({"checks": [GOOD, GOOD]})
        self.assertIn("duplicate slug", " ".join(errs))
        self.assertIn("unknown field(s) owner", " ".join(errors_for(owner="x")))
        self.assertEqual(deadman.validate([])[1], ['registry must be an object with a "checks" list'])
        self.assertEqual(deadman.validate({"checks": ["x"]})[1], ["checks[0]: must be an object"])

    def test_unreadable_registry_is_an_error(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "h.json"
            path.write_text("{not json", encoding="utf-8")
            _, errs = deadman.load(path)
        self.assertEqual(len(errs), 1)


class PayloadTest(unittest.TestCase):
    def test_payload_upserts_by_slug_with_cron_schedule(self) -> None:
        checks, _ = deadman.validate({"checks": [GOOD]})
        self.assertEqual(
            checks[0].payload(),
            {
                "name": "aih-analytics-archive",
                "slug": "aih-analytics-archive",
                "tags": "bastion aih",
                "desc": "jonnysteedman683-wq/AIH .github/workflows/analytics-archive.yml (*/20 * * * * UTC)",
                "schedule": "*/20 * * * *",
                "tz": "UTC",
                "grace": 2400,
                "unique": ["slug"],
            },
        )


class CliTest(unittest.TestCase):
    def test_validate_needs_no_key(self) -> None:
        code, out = run("validate")
        self.assertEqual(code, 0)
        self.assertIn("8 checks valid", out)

    def test_plan_prints_payloads_and_never_the_key(self) -> None:
        code, out = run("plan", env={"HC_API_KEY": "secret-key"})
        self.assertEqual(code, 0)
        self.assertEqual(len(json.loads(out)), 8)
        self.assertNotIn("secret-key", out)

    def test_sync_without_key_fails_before_any_request(self) -> None:
        post = FakePost([])
        code, out = run("sync", post=post)
        self.assertEqual(code, 2)
        self.assertIn("Set HC_API_KEY", out)
        self.assertEqual(post.calls, [])

    def test_sync_upserts_every_check(self) -> None:
        post = FakePost([201] + [200] * 7)
        code, out = run("sync", env={"HC_API_KEY": "k"}, post=post)
        self.assertEqual(code, 0)
        self.assertEqual(len(post.calls), 8)
        self.assertTrue(all(url == "https://healthchecks.io/api/v3/checks/" for url, _, _ in post.calls))
        self.assertTrue(all(key == "k" for _, _, key in post.calls))
        self.assertIn("created aih-analytics-archive", out)
        self.assertIn("8/8 checks in sync", out)

    def test_sync_reports_failures_and_keeps_going(self) -> None:
        post = FakePost([403, 401] + [200] * 6)
        code, out = run("sync", env={"HC_API_KEY": "k", "HC_API_URL": "https://hc.example/api/v3"}, post=post)
        self.assertEqual(code, 1)
        self.assertEqual(len(post.calls), 8)
        self.assertEqual(post.calls[0][0], "https://hc.example/api/v3/checks/")
        self.assertIn("HTTP 403: check limit reached", out)
        self.assertIn("HTTP 401: API key missing or invalid", out)
        self.assertIn("6/8 checks in sync", out)

    def test_invalid_registry_stops_before_sync(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "h.json"
            path.write_text(json.dumps({"checks": [{**GOOD, "grace": 1}]}), encoding="utf-8")
            post = FakePost([])
            code, out = run("--registry", str(path), "sync", env={"HC_API_KEY": "k"}, post=post)
        self.assertEqual(code, 1)
        self.assertIn("grace must be", out)
        self.assertEqual(post.calls, [])


class HttpPostTest(unittest.TestCase):
    def setUp(self) -> None:
        self.seen: list[tuple[str, str, dict[str, Any]]] = []
        seen = self.seen

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self) -> None:
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                seen.append((self.path, self.headers["X-Api-Key"], body))
                code = 201 if self.headers["X-Api-Key"] == "good" else 401
                self.send_response(code)
                self.end_headers()
                self.wfile.write(b'{"ok": true}')

            def log_message(self, *args: Any) -> None:
                pass

        self.server = HTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.url = f"http://127.0.0.1:{self.server.server_port}/api/v3/checks/"

    def tearDown(self) -> None:
        self.server.shutdown()
        self.server.server_close()

    def test_sends_key_header_and_json(self) -> None:
        status, _ = deadman.http_post(self.url, {"slug": "x"}, "good")
        self.assertEqual(status, 201)
        self.assertEqual(self.seen, [("/api/v3/checks/", "good", {"slug": "x"})])

    def test_http_error_is_returned_not_raised(self) -> None:
        status, _ = deadman.http_post(self.url, {"slug": "x"}, "bad")
        self.assertEqual(status, 401)

    def test_refuses_plain_http(self) -> None:
        with self.assertRaises(ValueError):
            deadman.http_post("http://healthchecks.io/api/v3/checks/", {}, "k")


if __name__ == "__main__":
    unittest.main()
