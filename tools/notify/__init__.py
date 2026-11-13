"""notify: push a message to Jonny's phone through ntfy (backlog G1d).

    python -m tools.notify send --message "Sebastian is over its daily cap" --title "Cap breach" \
        --priority urgent --tags warning

Configuration comes from the environment, never from arguments or files in this repository:

    NTFY_TOPIC   the topic the phone app subscribes to. On public ntfy.sh the topic name is the only
                 secret, so use a long random one. Never printed.
    NTFY_URL     the ntfy server (default https://ntfy.sh). Must be https; plain http only for loopback.
    NTFY_TOKEN   optional access token for a protected topic or a self-hosted server. Never printed.

Exit codes: 0 sent, 1 the server or network failed, 2 bad configuration or arguments (nothing sent).

Standard library only.
"""

from __future__ import annotations

import argparse
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable, Mapping, Sequence
from typing import TextIO

DEFAULT_URL = "https://ntfy.sh"
LOOPBACK_HOSTS = frozenset({"localhost", "127.0.0.1", "::1"})
PRIORITIES = ("min", "low", "default", "high", "urgent")
MAX_MESSAGE_BYTES = 4096  # ntfy's message limit
MAX_HEADER = 250
TIMEOUT_SECONDS = 8.0
TOPIC = re.compile(r"[A-Za-z0-9_-]{1,64}")
TAGS = re.compile(r"[A-Za-z0-9_+-]+(,[A-Za-z0-9_+-]+)*")

Post = Callable[[str, bytes, dict[str, str]], int]


class ConfigError(ValueError):
    """Bad configuration or arguments. The message never contains the topic or token."""


def http_post(url: str, body: bytes, headers: dict[str, str]) -> int:
    """POST the body. Returns the HTTP status; HTTP errors are returned, not raised."""
    req = urllib.request.Request(url, data=body, headers=headers, method="POST")  # noqa: S310 - scheme checked by caller
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT_SECONDS) as resp:  # noqa: S310 - scheme checked by caller
            return int(resp.status)
    except urllib.error.HTTPError as exc:
        return int(exc.code)


def header_text(value: str) -> str:
    """One line of latin-1 text: HTTP headers cannot carry newlines or wider characters."""
    flat = " ".join(value.split())
    return flat.encode("latin-1", "replace").decode("latin-1")[:MAX_HEADER]


def server_url(env: Mapping[str, str]) -> str:
    raw = (env.get("NTFY_URL") or DEFAULT_URL).strip()
    parts = urllib.parse.urlsplit(raw)
    if parts.scheme == "https" or (parts.scheme == "http" and parts.hostname in LOOPBACK_HOSTS):
        if parts.hostname and not parts.username and not parts.password and not parts.query and not parts.fragment:
            return raw.rstrip("/")
    raise ConfigError("NTFY_URL must be an https server URL (plain http only for localhost) with no credentials")


def build(args: argparse.Namespace, env: Mapping[str, str]) -> tuple[str, bytes, dict[str, str]]:
    topic = (env.get("NTFY_TOPIC") or "").strip()
    if not topic:
        raise ConfigError("NTFY_TOPIC is not set")
    if not TOPIC.fullmatch(topic):
        raise ConfigError("NTFY_TOPIC must be 1-64 letters, digits, '-' or '_'")
    url = f"{server_url(env)}/{topic}"
    if args.priority not in PRIORITIES:
        raise ConfigError(f"--priority must be one of {', '.join(PRIORITIES)}")
    headers = {"Priority": args.priority, "Content-Type": "text/plain; charset=utf-8"}
    if args.title:
        headers["Title"] = header_text(args.title)
    if args.tags:
        if not TAGS.fullmatch(args.tags):
            raise ConfigError("--tags must be comma-separated words")
        headers["Tags"] = args.tags
    if args.click:
        click = urllib.parse.urlsplit(args.click)
        if click.scheme != "https" or not click.hostname or len(args.click) > 500:
            raise ConfigError("--click must be an https link")
        headers["Click"] = args.click
    token = (env.get("NTFY_TOKEN") or "").strip()
    if token:
        headers["Authorization"] = "Bearer " + header_text(token)
    body = args.message.encode("utf-8")[:MAX_MESSAGE_BYTES]
    return url, body, headers


def main(
    argv: Sequence[str] | None = None,
    *,
    env: Mapping[str, str] | None = None,
    post: Post = http_post,
    out: TextIO = sys.stdout,
) -> int:
    parser = argparse.ArgumentParser(prog="python -m tools.notify", description=(__doc__ or "").splitlines()[0])
    sub = parser.add_subparsers(dest="cmd", required=True)
    send = sub.add_parser("send", help="push one message to the phone")
    send.add_argument("--message", required=True)
    send.add_argument("--title", default="")
    send.add_argument("--priority", default="default")
    send.add_argument("--tags", default="")
    send.add_argument("--click", default="", help="https link opened when the notification is tapped")
    args = parser.parse_args(argv)
    env = os.environ if env is None else env

    try:
        url, body, headers = build(args, env)
    except ConfigError as exc:
        print(f"::error::{exc}", file=out)
        return 2
    try:
        status = post(url, body, headers)
    except (OSError, ValueError):
        print("::error::could not reach the ntfy server", file=out)  # the error text could echo the topic URL
        return 1
    if not 200 <= status < 300:
        print(f"::error::ntfy answered HTTP {status}", file=out)
        return 1
    print("sent", file=out)
    return 0
