"""vortex: check that Claude (or any agent) can reach the Forge's Vortex MCP server with its own key.

    python -m tools.vortex check

Configuration comes from the environment, the same two variables `.mcp.json` reads:

    VORTEX_MCP_URL   the Vortex address, https://forge-vm.<tailnet>.ts.net/mcp (plain http only for loopback)
    VORTEX_API_KEY   this agent's key from `forge-keys.sh` (vtx_...). Never printed.

`check` initializes an MCP session, lists Vortex's tools and asks Vortex which agent the key belongs to.

Exit codes: 0 connected, 1 the server or network failed, 2 bad configuration (nothing sent).

Standard library only.
"""

from __future__ import annotations

import argparse
import contextlib
import ipaddress
import json
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable, Mapping, Sequence
from typing import Any, TextIO

KEY = re.compile(r"vtx_[A-Za-z0-9_-]{43}")
TIMEOUT_SECONDS = 25.0
PROTOCOL_VERSION = "2025-06-18"

# (url, body, headers) -> (status, response headers, body text)
Post = Callable[[str, bytes, dict[str, str]], tuple[int, dict[str, str], str]]


class ConfigError(ValueError):
    """Bad configuration. The message never contains the key."""


class ServerError(RuntimeError):
    """Vortex or the network failed. The message never contains the key."""


class NoRedirect(urllib.request.HTTPRedirectHandler):
    """Never follow a redirect: it could carry the bearer key to another host."""

    def redirect_request(self, *args: object, **kwargs: object) -> None:
        return None


OPENER = urllib.request.build_opener(NoRedirect)


def http_post(url: str, body: bytes, headers: dict[str, str]) -> tuple[int, dict[str, str], str]:
    req = urllib.request.Request(url, data=body, headers=headers, method="POST")  # noqa: S310 - scheme checked
    try:
        with OPENER.open(req, timeout=TIMEOUT_SECONDS) as resp:
            return int(resp.status), {k.lower(): v for k, v in resp.headers.items()}, resp.read().decode()
    except urllib.error.HTTPError as exc:
        return int(exc.code), {}, ""
    except OSError as exc:
        raise ServerError(f"cannot reach Vortex: {type(exc).__name__}") from None


def is_loopback(host: str | None) -> bool:
    if host == "localhost":
        return True
    try:
        return ipaddress.ip_address(host or "").is_loopback
    except ValueError:
        return False


def config(env: Mapping[str, str]) -> tuple[str, str]:
    raw = (env.get("VORTEX_MCP_URL") or "").strip()
    if not raw:
        raise ConfigError("VORTEX_MCP_URL is not set")
    try:
        parts = urllib.parse.urlsplit(raw)
    except ValueError:
        raise ConfigError("VORTEX_MCP_URL is not a valid URL") from None
    scheme_ok = parts.scheme == "https" or (parts.scheme == "http" and is_loopback(parts.hostname))
    clean = parts.hostname and not parts.username and not parts.password and not parts.query and not parts.fragment
    if not (scheme_ok and clean and parts.path.rstrip("/").endswith("/mcp")):
        raise ConfigError(
            "VORTEX_MCP_URL must be https://<host>/mcp (plain http only for localhost), with no credentials or query"
        )
    key = (env.get("VORTEX_API_KEY") or "").strip()
    if not key:
        raise ConfigError("VORTEX_API_KEY is not set")
    if not KEY.fullmatch(key):
        raise ConfigError("VORTEX_API_KEY is not a Vortex key (vtx_ followed by 43 characters)")
    return raw.rstrip("/"), key


def parse(text: str) -> dict[str, Any]:
    """A JSON-RPC reply, sent either as plain JSON or as the last `data:` line of a server-sent event stream."""
    text = text.strip()
    if text.startswith(("event:", "data:")):
        lines = [line[5:].strip() for line in text.splitlines() if line.startswith("data:")]
        text = lines[-1] if lines else ""
    try:
        reply = json.loads(text)
    except ValueError:
        raise ServerError("Vortex sent a reply that is not JSON") from None
    if not isinstance(reply, dict):
        raise ServerError("Vortex sent an unexpected reply")
    if "error" in reply:
        raise ServerError("Vortex returned an error")
    return reply


class Client:
    def __init__(self, url: str, key: str, post: Post) -> None:
        self.url, self.key, self.post, self.session, self.next_id = url, key, post, "", 1

    def headers(self) -> dict[str, str]:
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
            "Authorization": "Bearer " + self.key,
            "MCP-Protocol-Version": PROTOCOL_VERSION,
        }
        if self.session:
            headers["Mcp-Session-Id"] = self.session
        return headers

    def send(self, method: str, params: dict[str, Any] | None = None, notify: bool = False) -> dict[str, Any]:
        body: dict[str, Any] = {"jsonrpc": "2.0", "method": method}
        if params is not None:
            body["params"] = params
        if not notify:
            body["id"] = self.next_id
            self.next_id += 1
        status, headers, text = self.post(self.url, json.dumps(body).encode(), self.headers())
        if status == 401:
            raise ServerError("Vortex refused the key (HTTP 401): issue a new one with forge-keys.sh")
        if notify:
            if status not in (200, 202, 204):
                raise ServerError(f"{method}: HTTP {status}")
            return {}
        if status != 200:
            raise ServerError(f"{method}: HTTP {status}")
        self.session = headers.get("mcp-session-id", self.session)
        return parse(text).get("result") or {}


def agent_id(result: Mapping[str, Any]) -> str:
    """The agent_id Vortex reports, from structured content or a JSON text part."""
    candidates: list[Any] = [result.get("structuredContent")]
    for part in result.get("content") or []:
        if isinstance(part, dict):
            with contextlib.suppress(ValueError):
                candidates.append(json.loads(part.get("text", "")))
    for value in candidates:
        if isinstance(value, dict):
            found = value.get("agent_id") or (value.get("agent") or {}).get("agent_id")
            if isinstance(found, str):
                return found
    return ""


def check(env: Mapping[str, str], post: Post, out: TextIO) -> None:
    url, key = config(env)
    client = Client(url, key, post)
    init = client.send(
        "initialize",
        {"protocolVersion": PROTOCOL_VERSION, "capabilities": {}, "clientInfo": {"name": "bastion", "version": "0"}},
    )
    client.send("notifications/initialized", notify=True)
    tools = [t.get("name", "") for t in client.send("tools/list").get("tools", []) if isinstance(t, dict)]
    registered = client.send("tools/call", {"name": "vortex_register_agent", "arguments": {}})
    if registered.get("isError"):
        raise ServerError("vortex_register_agent reported an error")
    who = agent_id(registered)
    if not who:
        raise ServerError("Vortex did not say which agent the key belongs to")
    server = (init.get("serverInfo") or {}).get("name", "Vortex")
    out.write(f"connected to {server} as {who}; {len(tools)} tools: {', '.join(sorted(tools))}\n")


def main(
    argv: Sequence[str] | None = None,
    env: Mapping[str, str] | None = None,
    post: Post = http_post,
    out: TextIO = sys.stdout,
    err: TextIO = sys.stderr,
) -> int:
    parser = argparse.ArgumentParser(prog="python -m tools.vortex", description="Check the Vortex MCP connection.")
    parser.add_argument("command", choices=["check"])
    parser.parse_args(argv)
    try:
        check(os.environ if env is None else env, post, out)
    except ConfigError as exc:
        err.write(f"vortex: {exc}\n")
        return 2
    except ServerError as exc:
        err.write(f"vortex: {exc}\n")
        return 1
    return 0
