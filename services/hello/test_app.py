"""Run with: python -m unittest discover -s services/hello"""

from __future__ import annotations

import json
import threading
import unittest
import urllib.error
import urllib.request

import app


class HelloServiceTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.server = app.build_server(0, "abc1234", host="127.0.0.1")
        cls.base = f"http://127.0.0.1:{cls.server.server_address[1]}"
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls) -> None:
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join(timeout=5)

    def get(self, path: str) -> tuple[int, dict[str, str], bytes]:
        try:
            with urllib.request.urlopen(self.base + path, timeout=5) as resp:
                return resp.status, dict(resp.headers), resp.read()
        except urllib.error.HTTPError as err:
            return err.code, dict(err.headers), err.read()

    def test_health_reports_ok_with_version(self) -> None:
        status, headers, body = self.get("/health")
        self.assertEqual(status, 200)
        self.assertEqual(headers["Content-Type"], "application/json")
        self.assertEqual(json.loads(body), {"status": "ok", "service": "hello", "version": "abc1234"})

    def test_health_ignores_query_string(self) -> None:
        status, _, _ = self.get("/health?probe=1")
        self.assertEqual(status, 200)

    def test_root_greets(self) -> None:
        status, _, body = self.get("/")
        self.assertEqual(status, 200)
        self.assertEqual(body, b"Bastion stands.\n")

    def test_unknown_path_is_404(self) -> None:
        status, _, body = self.get("/nope")
        self.assertEqual(status, 404)
        self.assertEqual(json.loads(body), {"error": "not found"})

    def test_security_headers(self) -> None:
        _, headers, _ = self.get("/")
        self.assertEqual(headers["X-Content-Type-Options"], "nosniff")
        self.assertEqual(headers["Cache-Control"], "no-store")
        self.assertNotIn("Python", headers.get("Server", ""))


if __name__ == "__main__":
    unittest.main()
