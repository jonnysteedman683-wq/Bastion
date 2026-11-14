"""vortex: Claude's connection to the Forge's Vortex MCP server. Run with: python -m unittest discover -s tests -t ."""

from __future__ import annotations

import io
import json
import re
import unittest
from pathlib import Path
from typing import Any

from tools import vortex

ROOT = Path(__file__).parents[1]
KEY = "vtx_" + "A" * 43
ENV = {"VORTEX_MCP_URL": "https://forge-vm.example.ts.net/mcp", "VORTEX_API_KEY": KEY}


class FakeVortex:
    """Answers the MCP calls `check` makes, like Vortex does (SSE replies, a session id)."""

    def __init__(self, status: int = 200, agent: str = "claude-1") -> None:
        self.status, self.agent = status, agent
        self.calls: list[tuple[str, dict[str, Any], dict[str, str]]] = []

    def __call__(self, url: str, body: bytes, headers: dict[str, str]) -> tuple[int, dict[str, str], str]:
        msg = json.loads(body)
        self.calls.append((url, msg, headers))
        if self.status != 200:
            return self.status, {}, ""
        method = msg["method"]
        if method == "notifications/initialized":
            return 202, {}, ""
        result: dict[str, Any] = {}
        if method == "initialize":
            result = {"protocolVersion": "2025-06-18", "serverInfo": {"name": "vortex"}, "capabilities": {}}
        elif method == "tools/list":
            result = {"tools": [{"name": "vortex_register_agent"}, {"name": "vortex_get_inbox"}]}
        elif method == "tools/call":
            result = {"content": [{"type": "text", "text": json.dumps({"agent_id": self.agent})}]}
        reply = json.dumps({"jsonrpc": "2.0", "id": msg["id"], "result": result})
        return 200, {"mcp-session-id": "s-1"}, f"event: message\ndata: {reply}\n\n"


def run(env: dict[str, str], post: Any) -> tuple[int, str, str]:
    out, err = io.StringIO(), io.StringIO()
    code = vortex.main(["check"], env=env, post=post, out=out, err=err)
    return code, out.getvalue(), err.getvalue()


class CheckTests(unittest.TestCase):
    def test_connects_and_reports_the_agent_behind_the_key(self) -> None:
        fake = FakeVortex()
        code, out, err = run(ENV, fake)

        self.assertEqual(code, 0, err)
        self.assertIn("as claude-1", out)
        self.assertIn("vortex_get_inbox", out)
        methods = [m["method"] for _, m, _ in fake.calls]
        self.assertEqual(methods, ["initialize", "notifications/initialized", "tools/list", "tools/call"])
        for url, _, headers in fake.calls:
            self.assertEqual(url, ENV["VORTEX_MCP_URL"])
            self.assertEqual(headers["Authorization"], "Bearer " + KEY)
        self.assertNotIn("Mcp-Session-Id", fake.calls[0][2])
        self.assertEqual(fake.calls[2][2]["Mcp-Session-Id"], "s-1")

    def test_refused_key_fails_without_printing_it(self) -> None:
        code, out, err = run(ENV, FakeVortex(status=401))

        self.assertEqual(code, 1)
        self.assertIn("401", err)
        self.assertNotIn(KEY, out + err)

    def test_bad_configuration_sends_nothing(self) -> None:
        cases = [
            {},
            {**ENV, "VORTEX_API_KEY": ""},
            {**ENV, "VORTEX_API_KEY": "not-a-key"},
            {**ENV, "VORTEX_MCP_URL": "http://forge-vm.example.ts.net/mcp"},
            {**ENV, "VORTEX_MCP_URL": "https://user:pw@forge-vm.example.ts.net/mcp"},
            {**ENV, "VORTEX_MCP_URL": "https://forge-vm.example.ts.net/mcp?x=1"},
            {**ENV, "VORTEX_MCP_URL": "https://forge-vm.example.ts.net/other"},
        ]
        for env in cases:
            with self.subTest(env=env):
                fake = FakeVortex()
                code, out, err = run(env, fake)
                self.assertEqual(code, 2)
                self.assertEqual(fake.calls, [])
                self.assertNotIn(KEY, out + err)

    def test_plain_http_allowed_for_loopback(self) -> None:
        code, _, err = run({**ENV, "VORTEX_MCP_URL": "http://127.0.0.1:8000/mcp"}, FakeVortex())
        self.assertEqual(code, 0, err)


class McpJsonTests(unittest.TestCase):
    def test_vortex_entry_reads_url_and_key_from_the_environment_only(self) -> None:
        config = json.loads((ROOT / ".mcp.json").read_text())
        server = config["mcpServers"]["vortex"]

        self.assertEqual(server["type"], "http")
        self.assertEqual(server["url"], "${VORTEX_MCP_URL}")
        self.assertEqual(server["headers"], {"Authorization": "Bearer ${VORTEX_API_KEY}"})

    def test_no_key_or_tailnet_name_is_committed(self) -> None:
        text = (ROOT / ".mcp.json").read_text()
        self.assertIsNone(re.search(r"vtx_[A-Za-z0-9_-]{8,}", text))
        self.assertNotIn(".ts.net", text)


if __name__ == "__main__":
    unittest.main()
