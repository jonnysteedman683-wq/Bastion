"""Hashes for secrets generated on the Forge VM. Standard library only.

The secrets themselves are read from standard input, never from the command line, so they do not
appear in `ps` or in shell history. Only the hashes are printed; those are safe to share.

    python3 scram.py generate   # a new random secret (43 URL-safe characters)
    python3 scram.py verifier   # stdin: password -> Postgres SCRAM-SHA-256 verifier
    python3 scram.py sha256     # stdin: token    -> SHA-256 hex
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
import sys

ITERATIONS = 4096
SALT_BYTES = 16


def generate() -> str:
    return secrets.token_urlsafe(32)


def scram_verifier(password: str, *, salt: bytes | None = None, iterations: int = ITERATIONS) -> str:
    """What Postgres stores for a SCRAM-SHA-256 password (RFC 5802 / RFC 7677):

    SCRAM-SHA-256$<iterations>:<salt>$<StoredKey>:<ServerKey>, each part base64.
    `ALTER ROLE ... PASSWORD '<verifier>'` accepts it as-is, so the password never has to be sent.
    """
    # Postgres runs SASLprep on the password. Printable ASCII is unchanged by it, so only that is accepted.
    if not password or any(not 0x21 <= ord(ch) <= 0x7E for ch in password):
        raise ValueError("password must be non-empty printable ASCII without spaces")
    if salt is None:
        salt = secrets.token_bytes(SALT_BYTES)
    salted = hashlib.pbkdf2_hmac("sha256", password.encode("ascii"), salt, iterations)
    client_key = hmac.new(salted, b"Client Key", hashlib.sha256).digest()
    stored_key = hashlib.sha256(client_key).digest()
    server_key = hmac.new(salted, b"Server Key", hashlib.sha256).digest()

    def b64(raw: bytes) -> str:
        return base64.b64encode(raw).decode("ascii")

    return f"SCRAM-SHA-256${iterations}:{b64(salt)}${b64(stored_key)}:{b64(server_key)}"


def sha256_hex(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def main(argv: list[str]) -> int:
    if len(argv) != 1 or argv[0] not in ("generate", "verifier", "sha256"):
        print("usage: scram.py generate | verifier | sha256   (secrets are read from standard input)", file=sys.stderr)
        return 2
    if argv[0] == "generate":
        print(generate())
        return 0
    value = sys.stdin.read().rstrip("\n")
    try:
        print(scram_verifier(value) if argv[0] == "verifier" else sha256_hex(value))
    except ValueError as exc:
        print(f"scram.py: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
