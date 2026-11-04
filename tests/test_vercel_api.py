"""Run with: python -m unittest discover -s tests -t ."""

from __future__ import annotations

import json
import unittest
from typing import Any

from api import data


class FakeConn:
    def __init__(self, fail: set[str] | None = None) -> None:
        self.sql: list[str] = []
        self.fail = fail or set()
        self.closed = False

    def run(self, sql: str) -> list[list[Any]]:
        self.sql.append(sql)
        for name in self.fail:
            if name in sql:
                raise RuntimeError("boom")
        return [[1, "x"]]

    @property
    def columns(self) -> list[dict[str, str]]:
        return [{"name": "a"}, {"name": "b"}]

    def close(self) -> None:
        self.closed = True


class AuthTest(unittest.TestCase):
    def test_fails_closed_without_a_configured_token(self) -> None:
        self.assertEqual(data.check_auth("Bearer anything", ""), (503, "dashboard token not configured"))

    def test_rejects_missing_or_wrong_token(self) -> None:
        self.assertEqual(data.check_auth("", "s3cret-passphrase")[0], 401)
        self.assertEqual(data.check_auth("Bearer nope", "s3cret-passphrase")[0], 401)
        self.assertEqual(data.check_auth("Basic s3cret-passphrase", "s3cret-passphrase")[0], 401)

    def test_accepts_the_right_token(self) -> None:
        self.assertEqual(data.check_auth("Bearer s3cret-passphrase", "s3cret-passphrase"), (200, ""))


class QueriesTest(unittest.TestCase):
    def test_fixed_read_only_queries_on_bastion_views_only(self) -> None:
        self.assertEqual(set(data.QUERIES), {"stats", "roster", "feed", "tasks", "control"})
        for sql in data.QUERIES.values():
            low = sql.lower()
            self.assertTrue(low.startswith("select "))
            self.assertNotIn("unisync_", low)  # never the raw tables
            for word in ("insert ", "update ", "delete ", "drop ", "alter ", "grant ", ";"):
                self.assertNotIn(word, low)

    def test_load_returns_rows_as_objects_and_closes(self) -> None:
        conn = FakeConn()
        out = data.load(lambda: conn)
        self.assertEqual(out["stats"], {"status": "ok", "data": [{"a": 1, "b": "x"}]})
        self.assertEqual(len(conn.sql), 5)
        self.assertTrue(conn.closed)

    def test_one_failing_query_does_not_hide_the_rest(self) -> None:
        conn = FakeConn(fail={"bastion.control"})
        out = data.load(lambda: conn)
        self.assertEqual(out["control"], {"status": "error", "data": [], "message": "query failed"})
        self.assertEqual(out["feed"]["status"], "ok")

    def test_output_is_json_safe(self) -> None:
        json.dumps(data.load(lambda: FakeConn()), default=str)


if __name__ == "__main__":
    unittest.main()
