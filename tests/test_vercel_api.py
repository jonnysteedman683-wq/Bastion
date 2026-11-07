"""Run with: python -m unittest discover -s tests -t ."""

from __future__ import annotations

import json
import os
import unittest
from pathlib import Path
from typing import Any
from unittest import mock

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


class PublicDashboardTest(unittest.TestCase):
    def test_api_serves_data_without_authorization(self) -> None:
        request = object.__new__(data.handler)
        request.headers = {}
        sent: list[tuple[int, object]] = []
        request._send = lambda status, payload: sent.append((status, payload))

        with (
            mock.patch.dict(os.environ, {"LEDGER_DSN": "postgresql://example.invalid/db"}, clear=True),
            mock.patch.object(data, "connect_from_dsn"),
            mock.patch.object(data, "load", return_value={"stats": {"status": "ok", "data": []}}),
        ):
            request.do_GET()

        self.assertEqual(sent, [(200, {"stats": {"status": "ok", "data": []}})])

    def test_static_page_has_no_passphrase_gate(self) -> None:
        page = (Path(__file__).parents[1] / "public" / "index.html").read_text(encoding="utf-8")
        self.assertNotIn("BASTION_DASH_TOKEN", page)
        self.assertNotIn('id="passphrase"', page)


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
