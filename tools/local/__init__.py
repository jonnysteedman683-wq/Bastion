"""local: run the command centre on this machine, for agents that report in locally.

    python -m tools.local

Starts services/command-centre on http://localhost:8080, reachable from this machine only, and prints a
one-time sign-in link. Agents on the same machine report to it with `python -m tools.report`.

Settings come from the environment, then from a `.env.local` file in the repository root (git-ignored;
`NAME=value` lines). The one that matters for ingest:

    INGEST_AGENT_KEYS   {"<agent name>": "<that agent's key>", ...}

Reported events are kept in ~/.bastion/ingest.db unless INGEST_DB says otherwise.

Sign-in: with SUPABASE_URL and SUPABASE_PUBLISHABLE_KEY set, the normal magic-link sign-in is used.
Without them, the owner signs in with the link printed at startup. That link holds a secret made fresh on
every start, works only for this process, and the server never listens on anything but 127.0.0.1.

Never use this to serve the command centre to other machines: deploys go through Quartermaster.
"""

from __future__ import annotations

import hmac
import importlib
import os
import secrets
import sys
from collections.abc import Mapping, MutableMapping
from pathlib import Path
from typing import Any, TextIO

REPO = Path(__file__).resolve().parents[2]
SERVICE_DIR = REPO / "services" / "command-centre"
ENV_FILE = REPO / ".env.local"
HOST = "127.0.0.1"  # never configurable: this launcher is for the owner's own machine only
DEFAULT_PORT = 8080
LOCAL_STATE = Path.home() / ".bastion"


def read_env_file(path: Path) -> dict[str, str]:
    """NAME=value lines; blank lines and # comments ignored. A missing file is no settings."""
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return {}
    values: dict[str, str] = {}
    for line in lines:
        name, sep, value = line.strip().partition("=")
        name = name.strip()
        if sep and name and not name.startswith("#"):
            values[name] = value.strip()
    return values


def apply_env_file(env: MutableMapping[str, str], path: Path = ENV_FILE) -> list[str]:
    """Add the file's settings that the environment does not already have. Returns the names added."""
    added = []
    for name, value in read_env_file(path).items():
        if name not in env:
            env[name] = value
            added.append(name)
    return added


class LocalAuth:
    """Sign-in for the owner at their own machine: one secret link per run, no accounts.

    Stands in for the Supabase client behind the command centre's existing sign-in flow.
    """

    configured = True

    def __init__(self, auth_error: type[Exception]) -> None:
        self._error = auth_error
        self._link_secret = secrets.token_urlsafe(32)
        self._session = secrets.token_urlsafe(32)

    def sign_in_path(self) -> str:
        return f"/auth/callback?token_hash={self._link_secret}&type=local"

    def verify_magic_link(self, token_hash: str, token_type: str) -> str:
        if token_type != "local" or not hmac.compare_digest(token_hash, self._link_secret):  # noqa: S105
            raise self._error("invalid link")
        return self._session

    def current_user(self, access_token: str) -> dict[str, object]:
        if not hmac.compare_digest(access_token, self._session):
            raise self._error("invalid session")
        return {"id": "local-owner", "email": "owner@localhost"}

    def send_magic_link(self, email: str, callback_url: str) -> None:
        raise self._error("use the sign-in link printed when the local server started")


def _load_app() -> Any:
    sys.path.insert(0, str(SERVICE_DIR))
    return importlib.import_module("app")


def main(env: MutableMapping[str, str] | None = None, out: TextIO | None = None, app: Any = None) -> int:
    env = os.environ if env is None else env
    out = sys.stderr if out is None else out
    added = apply_env_file(env)
    if "INGEST_DB" not in env:  # keep reported events out of the repository checkout
        LOCAL_STATE.mkdir(parents=True, exist_ok=True)
        env["INGEST_DB"] = str(LOCAL_STATE / "ingest.db")
    app = _load_app() if app is None else app
    port = int(env.get("PORT", str(DEFAULT_PORT)))
    auth = None if describe(env) == "supabase" else LocalAuth(app.AuthError)
    server = app.build_server(port, "local", host=HOST, auth=auth)
    base = f"http://localhost:{port}"
    agents = sorted(app.ingest_agent_keys(env))
    out.write(f"Bastion command centre (local) on {base}, this machine only.\n")
    if added:
        out.write(f"Settings read from {ENV_FILE.name}: {', '.join(sorted(added))}\n")
    out.write(f"Agents that can report in: {', '.join(agents) if agents else 'none (set INGEST_AGENT_KEYS)'}\n")
    if auth is None:
        out.write(f"Sign in at {base}/login with your Supabase magic link.\n")
    else:
        out.write(f"Open this link to sign in (valid until this server stops):\n  {base}{auth.sign_in_path()}\n")
    out.write("Press Ctrl+C to stop.\n")
    out.flush()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


def describe(env: Mapping[str, str]) -> str:
    """One line for logs and tests: which sign-in this environment selects."""
    return "supabase" if env.get("SUPABASE_URL") and env.get("SUPABASE_PUBLISHABLE_KEY") else "local-link"
