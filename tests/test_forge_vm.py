"""Static and stubbed tests for the Forge VM bundle (deploy/forge-vm/) and its runbook.

Nothing here touches a network, a cloud or a real service: gcloud, tailscale, systemctl, git and uv are
replaced by stubs first on PATH, and forge-setup.sh runs against a throwaway FORGE_ROOT.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import importlib.util
import os
import re
import shutil
import stat
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).parents[1]
BUNDLE = ROOT / "deploy" / "forge-vm"
RUNBOOK = ROOT / "docs" / "FORGE_VM.md"

BUNDLE_FILES = (
    "cloud-init.yaml",
    "forge-bootstrap.sh",
    "forge-setup.sh",
    "forge-check.sh",
    "forge-keys.sh",
    "scram.py",
    "vortex.service",
    "vortex-relay.service",
    "forge.env.example",
    "create-vm.sh",
    "cloudshell-oneliner.md",
    "README.md",
)
SCRIPTS = ("forge-bootstrap.sh", "forge-setup.sh", "forge-check.sh", "forge-keys.sh", "create-vm.sh")
# Files the VM receives through instance metadata (key -> file).
METADATA_FILES = {
    "forge-bootstrap-sh": "forge-bootstrap.sh",
    "forge-setup-sh": "forge-setup.sh",
    "forge-check-sh": "forge-check.sh",
    "forge-keys-sh": "forge-keys.sh",
    "scram-py": "scram.py",
    "vortex-service": "vortex.service",
    "vortex-relay-service": "vortex-relay.service",
    "forge-env-example": "forge.env.example",
}
AGENT_IDS = "claude-1,hermes-1,hermes-2,sebastian-1,lestinxor-1,jules-1"
# Names whose values are secret: never given a value in any committed file.
SECRET_NAMES = ("TS_AUTHKEY", "VORTEX_STORE_URL", "UNISYNC_VORTEX_TOKEN", "FORGE_PG_PASSWORD", "GITHUB_TOKEN")
EXAMPLE_SECRETS = ("TS_AUTHKEY", "FORGE_PG_PASSWORD", "UNISYNC_VORTEX_TOKEN", "VORTEX_STORE_URL")
PROJECT_REF = "dcjqwnapxgocitvntzzm"
# Vortex-MCP main at the merge of apikey mode (2cc7fd2), in two halves so scanners do not read it as a key.
VORTEX_REF = "2cc7fd26474a0c5d3330" + "3655d217183a8e28a2f3"
EXAMPLE_PINS = {
    "VORTEX_REF": VORTEX_REF,
    "VORTEX_SCHEMA": "forge",
    "VORTEX_AUTH_MODE": "apikey",
    "VORTEX_AGENT_IDS": AGENT_IDS,
    "VORTEX_HOST": "127.0.0.1",
    "VORTEX_PORT": "8000",
    "FORGE_PG_USER": "vortex_app." + PROJECT_REF,
    "UNISYNC_GATEWAY_URL": "https://" + PROJECT_REF + ".supabase.co/functions/v1/unisync",
}
DNS_NAME = "forge-vm.tail0000.ts.net"
PG_HOST = "aws-0-us-east-1.pooler.supabase.com"

# Credential-shaped probes are assembled from parts so this file holds none itself.
CREDENTIAL_SHAPES = {
    "userinfo in a URL": re.compile(":" + "//" + r"[^/\s:@'\"]+:[^/\s@'\"]+" + "@"),
    "Tailscale key": re.compile("ts" + "key" + "-"),
    "Vortex API key": re.compile("vt" + "x_" + r"[A-Za-z0-9_-]{20,}"),
    "value assigned to a secret name": re.compile(
        r"^\s*(?:export\s+)?(?:" + "|".join(SECRET_NAMES) + r")[ \t]*[=:][ \t]*[\"']?[^\s\"'$<]", re.MULTILINE
    ),
}


def read(name: str) -> str:
    return (BUNDLE / name).read_text(encoding="utf-8")


def env_example() -> dict[str, str]:
    values: dict[str, str] = {}
    for line in read("forge.env.example").splitlines():
        if not line.strip() or line.startswith("#"):
            continue
        name, _, value = line.partition("=")
        values[name] = value
    return values


def write_stub(directory: Path, name: str, body: str) -> None:
    path = directory / name
    path.write_text("#!/bin/sh\n" + body, encoding="utf-8")
    path.chmod(0o755)


def run(cmd: list[str], *, env: dict[str, str], stdin: str = "") -> subprocess.CompletedProcess[str]:
    return subprocess.run(cmd, input=stdin, capture_output=True, text=True, env=env, timeout=120, check=False)


def tree(root: Path) -> dict[str, tuple[int, bytes]]:
    return {
        str(p.relative_to(root)): (p.stat().st_mode, p.read_bytes() if p.is_file() else b"")
        for p in sorted(root.rglob("*"))
    }


class BundleStaticTests(unittest.TestCase):
    def test_bundle_files_exist(self) -> None:
        for name in BUNDLE_FILES:
            self.assertTrue((BUNDLE / name).is_file(), name)
        for name in SCRIPTS:
            self.assertTrue(os.access(BUNDLE / name, os.X_OK), f"{name} is not executable")

    def test_env_example_has_names_not_values(self) -> None:
        text = read("forge.env.example")
        for line in text.splitlines():
            if line.strip() and not line.startswith("#"):
                self.assertRegex(line, r"^[A-Z][A-Z0-9_]*=\S*$")
        values = env_example()
        for name in EXAMPLE_SECRETS:
            self.assertEqual(values.get(name), "", f"{name} must be listed with no value")
        for name, value in EXAMPLE_PINS.items():
            self.assertEqual(values.get(name), value, name)
        self.assertEqual(set(values), set(EXAMPLE_SECRETS) | set(EXAMPLE_PINS))
        # The VM derives these from the Funnel name; a value here would be wrong for every tailnet.
        for name in ("VORTEX_PUBLIC_URL", "VORTEX_ALLOWED_HOSTS", "VORTEX_ALLOWED_ORIGINS"):
            self.assertNotIn(name, values)
        self.assertNotIn("must be bumped", text.lower())

    def test_no_credential_shaped_strings(self) -> None:
        for path in [*(BUNDLE / name for name in BUNDLE_FILES), RUNBOOK]:
            text = path.read_text(encoding="utf-8")
            for label, pattern in CREDENTIAL_SHAPES.items():
                self.assertIsNone(pattern.search(text), f"{path.name}: {label}")

    def test_services_are_hardened(self) -> None:
        for name, memory, command in (
            ("vortex.service", "600M", "-m vortex"),
            ("vortex-relay.service", "200M", "-m vortex relay"),
        ):
            lines = read(name).splitlines()
            for setting in (
                "User=forge",
                "EnvironmentFile=/etc/forge/forge.env",
                "Restart=always",
                "RestartSec=5",
                f"MemoryMax={memory}",
                "NoNewPrivileges=yes",
                "ProtectSystem=strict",
                "WantedBy=multi-user.target",
            ):
                self.assertIn(setting, lines, f"{name}: {setting}")
            exec_start = [line for line in lines if line.startswith("ExecStart=")]
            self.assertEqual(len(exec_start), 1, name)
            self.assertTrue(exec_start[0].endswith(command), f"{name}: {exec_start[0]}")
            self.assertNotIn("sudo", read(name))

    def test_only_mcp_is_published(self) -> None:
        setup = read("forge-setup.sh")
        self.assertIn("FUNNEL_TARGET=http://127.0.0.1:8000/mcp", setup)
        paths = []
        for name in BUNDLE_FILES:
            paths += re.findall(r"--set-path[= ](\S+)", read(name))
        self.assertTrue(paths, "no funnel path is published at all")
        self.assertEqual(set(paths), {"/mcp"})
        for name in SCRIPTS:
            for line in read(name).splitlines():
                if re.search(r"\btailscale (funnel|serve)\b", line) and not line.lstrip().startswith("#"):
                    self.assertRegex(line, r"funnel (status|reset|--bg --set-path /mcp )", f"{name}: {line}")
        self.assertIn("VORTEX_HOST=127.0.0.1", read("forge.env.example"))

    def test_swap_is_configured(self) -> None:
        cloud_init = read("cloud-init.yaml")
        self.assertTrue(cloud_init.startswith("#cloud-config\n"))
        self.assertRegex(cloud_init, r"(?m)^swap:\n  filename: /swapfile\n  size: 1G\n  maxsize: 1G$")
        self.assertIn("swap active", read("forge-check.sh"))

    def test_cloud_init_prepares_but_does_not_start(self) -> None:
        cloud_init = read("cloud-init.yaml")
        bootstrap = read("forge-bootstrap.sh")
        self.assertRegex(cloud_init, r"(?m)^  - name: forge$")
        self.assertNotIn("sudo:", cloud_init)
        self.assertIn("forge-bootstrap-sh", cloud_init)
        self.assertIn("pkgs.tailscale.com/stable/ubuntu/noble", bootstrap)
        self.assertIn("astral.sh/uv/install.sh", bootstrap)
        for text in (cloud_init, bootstrap):
            self.assertIsNone(re.search(r"systemctl\s+(start|enable|restart)\s+\S*vortex", text))
            self.assertNotIn("tailscale up", text)

    def test_metadata_keys_match_between_create_and_bootstrap(self) -> None:
        create = read("create-vm.sh")
        bootstrap = read("forge-bootstrap.sh")
        for key, name in METADATA_FILES.items():
            self.assertIn(f"{key}=", create, key)
            self.assertIn(name, create, name)
            if key != "forge-bootstrap-sh":
                self.assertRegex(bootstrap, rf"(?m)^\s*{re.escape(key)}:{re.escape(name)}:", key)

    def test_scripts_parse(self) -> None:
        for name in SCRIPTS:
            result = subprocess.run(["bash", "-n", str(BUNDLE / name)], capture_output=True, text=True, check=False)
            self.assertEqual(result.returncode, 0, f"{name}: {result.stderr}")
            text = read(name)
            self.assertTrue(text.startswith("#!/usr/bin/env bash\n"), name)
            self.assertIsNone(re.search(r"(?m)^\s*set -[a-z]*x", text), f"{name} must never trace commands")

    def test_secrets_are_read_hidden_and_never_printed(self) -> None:
        setup = read("forge-setup.sh")
        self.assertTrue("read -r -s" in setup, "secrets must be read without echo")
        self.assertTrue("--auth-key=file:" in setup, "the Tailscale key must reach tailscale through a file")
        printing = re.compile(r"\b(echo|printf|say|plan|die|warn)\b")
        secret_refs = re.compile(r"ENVV\[(?:" + "|".join(SECRET_NAMES) + r")\]|\$\{?secret\b")
        for name in ("forge-setup.sh", "forge-keys.sh", "forge-check.sh"):
            for line in read(name).splitlines():
                if line.lstrip().startswith("#") or not secret_refs.search(line) or not printing.search(line):
                    continue
                # The only way a secret may leave a printf: into a file, or into a helper's stdin.
                self.assertRegex(line, r"printf '%s(\\n)?' .*(\| *python3 \"\$SCRAM\"|>\"\$)", f"{name}: {line}")

    def test_check_script_has_every_check_and_fails_closed(self) -> None:
        check = read("forge-check.sh")
        for label in (
            "vortex healthy",
            "relay running",
            "funnel publishes only /mcp",
            "claude-1 key accepted",
            "hermes-1 key accepted",
            "round trip claude-1 -> hermes-1",
            "outbox relayed to ledger",
            "second vortex refused",
            "revoked key refused within 30 s",
            "no secrets in journal",
            "swap active",
        ):
            self.assertIn(label, check, label)
        with tempfile.TemporaryDirectory() as tmp:
            result = run(["bash", str(BUNDLE / "forge-check.sh")], env={**os.environ, "FORGE_ROOT": tmp})
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("FAIL", result.stdout + result.stderr)

    def test_keys_script_refuses_unknown_agents(self) -> None:
        keys = read("forge-keys.sh")
        self.assertIn("keys add", keys)
        self.assertIn("store this in that agent's secret store now", keys)
        with tempfile.TemporaryDirectory() as tmp:
            env_dir = Path(tmp) / "etc" / "forge"
            env_dir.mkdir(parents=True)
            (env_dir / "forge.env").write_text(f"VORTEX_AGENT_IDS={AGENT_IDS}\n", encoding="utf-8")
            bin_dir = Path(tmp) / "bin"
            bin_dir.mkdir()
            write_stub(bin_dir, "systemd-run", 'echo "systemd-run $*" >>"$STUB_LOG"\nexit 99\n')
            env = {
                **os.environ,
                "FORGE_ROOT": tmp,
                "STUB_LOG": str(Path(tmp) / "log"),
                "PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}",
            }
            for bad in ([], ["mallory-1"], ["claude-1", "extra"], ["-h1"]):
                result = run(["bash", str(BUNDLE / "forge-keys.sh"), *bad], env=env)
                self.assertNotEqual(result.returncode, 0, bad)
            self.assertFalse((Path(tmp) / "log").exists(), "an unknown agent reached systemd-run")
            known = run(["bash", str(BUNDLE / "forge-keys.sh"), "hermes-1"], env=env)
            self.assertEqual(known.returncode, 99, "a known agent must reach the key command")
            self.assertIn("keys add hermes-1", (Path(tmp) / "log").read_text(encoding="utf-8"))


def joined(*parts: str) -> str:
    """Public RFC 7677 test values, written in pieces so secret scanners do not mistake them for keys."""
    return "".join(parts)


class ScramTests(unittest.TestCase):
    """deploy/forge-vm/scram.py against the RFC 7677 example exchange (user 'user', password 'pencil')."""

    SALT = joined("W22ZaJ0S", "NY7soEsU", "Ejb6gQ==")
    CLIENT_NONCE = joined("rOprNGfw", "EbeRWgbN", "EkqO")
    SERVER_NONCE = CLIENT_NONCE + joined("%hvYDpWU", "a2RaTCAf", "uxFIlj)h", "NlF$k0")
    AUTH_MESSAGE = f"n=user,r={CLIENT_NONCE},r={SERVER_NONCE},s={SALT},i=4096,c=biws,r={SERVER_NONCE}".encode()
    CLIENT_PROOF = joined("dHzbZapW", "Ik4jUhN+", "Ute9ytag", "9zjfMHgs", "qmmiz7An", "dVQ=")
    SERVER_SIGNATURE = joined("6rriTRBi", "23WpRR/w", "tup+mMhU", "ZUn/dB5n", "LTJRsjl9", "5G4=")
    FORMAT = re.compile(r"^SCRAM-SHA-256\$4096:([A-Za-z0-9+/]{22}==)\$([A-Za-z0-9+/]{43}=):([A-Za-z0-9+/]{43}=)$")

    @classmethod
    def setUpClass(cls) -> None:
        spec = importlib.util.spec_from_file_location("forge_scram", BUNDLE / "scram.py")
        assert spec and spec.loader
        cls.scram = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.scram)

    def test_verifier_matches_the_rfc_7677_exchange(self) -> None:
        verifier = self.scram.scram_verifier("pencil", salt=base64.b64decode(self.SALT))
        match = self.FORMAT.match(verifier)
        self.assertIsNotNone(match, verifier)
        salt, stored_key, server_key = (base64.b64decode(part) for part in match.groups())
        self.assertEqual(salt, base64.b64decode(self.SALT))
        # ServerSignature = HMAC(ServerKey, AuthMessage)
        signature = hmac.new(server_key, self.AUTH_MESSAGE, hashlib.sha256).digest()
        self.assertEqual(base64.b64encode(signature).decode(), self.SERVER_SIGNATURE)
        # ClientKey = ClientProof XOR HMAC(StoredKey, AuthMessage); StoredKey = SHA-256(ClientKey)
        client_signature = hmac.new(stored_key, self.AUTH_MESSAGE, hashlib.sha256).digest()
        client_key = bytes(a ^ b for a, b in zip(base64.b64decode(self.CLIENT_PROOF), client_signature))
        self.assertEqual(hashlib.sha256(client_key).digest(), stored_key)

    def test_default_salt_is_random_and_16_bytes(self) -> None:
        first = self.scram.scram_verifier("pencil")
        second = self.scram.scram_verifier("pencil")
        self.assertNotEqual(first, second)
        for verifier in (first, second):
            match = self.FORMAT.match(verifier)
            self.assertIsNotNone(match, verifier)
            self.assertEqual(len(base64.b64decode(match.group(1))), 16)

    def test_passwords_saslprep_would_change_are_refused(self) -> None:
        # Postgres applies SASLprep; refusing anything but printable ASCII avoids a verifier that never matches.
        for bad in ("", "passé", "tab\tbed"):
            with self.assertRaises(ValueError):
                self.scram.scram_verifier(bad)

    def test_cli_reads_stdin_and_never_takes_the_secret_as_an_argument(self) -> None:
        script = str(BUNDLE / "scram.py")
        env = dict(os.environ)
        verifier = run(["python3", script, "verifier"], env=env, stdin="pencil")
        self.assertEqual(verifier.returncode, 0, verifier.stderr)
        self.assertRegex(verifier.stdout.strip(), self.FORMAT)
        digest = run(["python3", script, "sha256"], env=env, stdin="pencil")
        self.assertEqual(digest.stdout.strip(), hashlib.sha256(b"pencil").hexdigest())
        generated = {run(["python3", script, "generate"], env=env).stdout.strip() for _ in range(2)}
        self.assertEqual(len(generated), 2)
        for value in generated:
            self.assertRegex(value, r"^[A-Za-z0-9_-]{43}$")
        refused = run(["python3", script, "verifier", "pencil"], env=env)
        self.assertNotEqual(refused.returncode, 0)
        self.assertNotIn("pencil", refused.stdout + refused.stderr)


TAILSCALE_STUB = r"""
case "$1 $2" in
  "status --json")
    if [ -f "$STUB_STATE/ts-up" ]; then
      echo '{"BackendState":"Running","Self":{"DNSName":"forge-vm.tail0000.ts.net."}}'
    else
      echo '{"BackendState":"NeedsLogin","Self":{"DNSName":""}}'
    fi ;;
  "funnel status")
    if [ -f "$STUB_STATE/funnel" ]; then
      echo '{"Web":{"forge-vm.tail0000.ts.net:443":{"Handlers":{"/mcp":{"Proxy":"http://127.0.0.1:8000/mcp"}}}},"AllowFunnel":{"forge-vm.tail0000.ts.net:443":true}}'
    else
      echo '{}'
    fi ;;
  "up "*)
    echo "tailscale $*" >>"$STUB_LOG"
    for arg in "$@"; do
      case "$arg" in --auth-key=file:*) cp "${arg#--auth-key=file:}" "$STUB_STATE/ts-key-seen" ;; esac
    done
    touch "$STUB_STATE/ts-up" ;;
  "funnel --bg") echo "tailscale $*" >>"$STUB_LOG"; touch "$STUB_STATE/funnel" ;;
  *) echo "tailscale $*" >>"$STUB_LOG" ;;
esac
"""
SYSTEMCTL_STUB = r"""
case "$1" in
  is-enabled|is-active)
    for unit in "$@"; do case "$unit" in *.service) [ -f "$STUB_STATE/unit-$unit" ] || exit 1 ;; esac; done ;;
  enable)
    echo "systemctl $*" >>"$STUB_LOG"
    for unit in "$@"; do case "$unit" in *.service) touch "$STUB_STATE/unit-$unit" ;; esac; done ;;
  *) echo "systemctl $*" >>"$STUB_LOG" ;;
esac
"""
GIT_STUB = r"""
if [ "$1" = "-C" ]; then dir="$2"; shift 2; else dir=""; fi
case "$1" in
  rev-parse) cat "$dir/.git/HEAD-stub" 2>/dev/null || exit 1 ;;
  clone)
    echo "git $*" >>"$STUB_LOG"
    if [ -n "${STUB_GIT_PRIVATE:-}" ]; then
      [ -n "${GIT_ASKPASS:-}" ] || exit 128
      "$GIT_ASKPASS" "Username for 'https://github.com': " >"$STUB_STATE/git-user-seen"
      "$GIT_ASKPASS" "Password for 'https://github.com': " >"$STUB_STATE/git-token-seen"
    fi
    for last in "$@"; do :; done
    mkdir -p "$last/.git" ;;
  checkout) echo "git $*" >>"$STUB_LOG"; for last in "$@"; do :; done; echo "$last" >"$dir/.git/HEAD-stub" ;;
  *) echo "git $*" >>"$STUB_LOG" ;;
esac
"""
UV_STUB = r"""
echo "uv $*" >>"$STUB_LOG"
[ "$1" = "--directory" ] && mkdir -p "$2/.venv/bin" && touch "$2/.venv/bin/python"
exit 0
"""


class SetupTests(unittest.TestCase):
    """forge-setup.sh against a throwaway FORGE_ROOT, with every external command stubbed."""

    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="forge-vm-test-"))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.root = self.tmp / "root"
        bundle_dir = self.root / "opt" / "forge"
        bundle_dir.mkdir(parents=True)
        for name in METADATA_FILES.values():
            shutil.copy2(BUNDLE / name, bundle_dir / name)
        self.bin = self.tmp / "bin"
        self.bin.mkdir()
        self.state = self.tmp / "state"
        self.state.mkdir()
        self.log = self.tmp / "mutations.log"
        write_stub(self.bin, "tailscale", TAILSCALE_STUB)
        write_stub(self.bin, "systemctl", SYSTEMCTL_STUB)
        write_stub(self.bin, "git", GIT_STUB)
        write_stub(self.bin, "uv", UV_STUB)
        self.env = {
            **os.environ,
            "FORGE_ROOT": str(self.root),
            "FORGE_UV": str(self.bin / "uv"),
            "STUB_STATE": str(self.state),
            "STUB_LOG": str(self.log),
            "PATH": f"{self.bin}{os.pathsep}{os.environ['PATH']}",
        }
        # Built at run time; shaped like the real thing only as far as the script validates.
        self.ts_key = "-".join(["ts" + "key", "auth", "kTESTONLY", "A" * 24])
        self.answers = f"{PG_HOST}\n{self.ts_key}\n\n"

    def setup(self, *args: str, stdin: str = "") -> subprocess.CompletedProcess[str]:
        return run(["bash", str(self.root / "opt" / "forge" / "forge-setup.sh"), *args], env=self.env, stdin=stdin)

    def mutations(self) -> list[str]:
        return self.log.read_text(encoding="utf-8").splitlines() if self.log.exists() else []

    def env_file(self) -> dict[str, str]:
        text = (self.root / "etc" / "forge" / "forge.env").read_text(encoding="utf-8")
        return dict(line.split("=", 1) for line in text.splitlines())

    def test_setup_dry_run_touches_nothing(self) -> None:
        before = tree(self.root)
        result = self.setup("--dry-run")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(tree(self.root), before)
        self.assertEqual(self.mutations(), [])
        self.assertEqual(list(self.state.iterdir()), [])
        self.assertIn("dry run", result.stdout)
        for planned in ("generate", "tailscale up", "funnel", "vortex.service", "vortex-relay.service"):
            self.assertIn(planned, result.stdout, planned)

    def test_first_run_generates_secrets_and_prints_only_hashes(self) -> None:
        result = self.setup(stdin=self.answers)
        output = result.stdout + result.stderr
        self.assertEqual(result.returncode, 0, output)
        env_path = self.root / "etc" / "forge" / "forge.env"
        self.assertEqual(stat.S_IMODE(env_path.stat().st_mode), 0o600)
        self.assertEqual(stat.S_IMODE(env_path.parent.stat().st_mode), 0o700)
        values = self.env_file()

        password = values["FORGE_PG_PASSWORD"]
        token = values["UNISYNC_VORTEX_TOKEN"]
        store_url = values["VORTEX_STORE_URL"]
        for generated in (password, token):
            self.assertRegex(generated, r"^[A-Za-z0-9_-]{43}$")
        self.assertNotEqual(password, token)
        user = "vortex_app." + PROJECT_REF
        expected = "postgresql" + "://" + user + ":" + password + "@" + PG_HOST + ":5432/postgres?sslmode=require"
        self.assertEqual(store_url, expected)
        for secret in (password, token, store_url, self.ts_key):
            self.assertNotIn(secret, output)
        self.assertNotIn("TS_AUTHKEY", values, "the one-off Tailscale key must not be kept")
        self.assertNotIn("GITHUB_TOKEN", values)

        # Only the two hashes are shown, and they verify against what was stored.
        self.assertIn("paste these two hashes to Claude; they are safe to share", output)
        self.assertIn(hashlib.sha256(token.encode()).hexdigest(), output)
        verifier = re.search(r"SCRAM-SHA-256\$4096:(\S+)\$(\S+):(\S+)", output)
        self.assertIsNotNone(verifier, output)
        salt = base64.b64decode(verifier.group(1))
        self.assertEqual(len(salt), 16)
        salted = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, 4096)
        server_key = hmac.new(salted, b"Server Key", hashlib.sha256).digest()
        self.assertEqual(base64.b64decode(verifier.group(3)), server_key)
        self.assertIn("press Enter once Claude confirms they are registered", output)

        self.assertEqual(values["VORTEX_PUBLIC_URL"], "https://" + DNS_NAME)
        self.assertEqual(values["VORTEX_ALLOWED_ORIGINS"], "https://" + DNS_NAME)
        self.assertIn(DNS_NAME, values["VORTEX_ALLOWED_HOSTS"].split(","))
        self.assertEqual(values["UNISYNC_GATEWAY_URL"], EXAMPLE_PINS["UNISYNC_GATEWAY_URL"])
        self.assertEqual(values["VORTEX_AUTH_MODE"], "apikey")
        self.assertEqual(values["FORGE_PG_HOST"], PG_HOST)

        log = "\n".join(self.mutations())
        self.assertNotIn(self.ts_key, log, "the Tailscale key was passed on a command line")
        self.assertIn("--auth-key=file:", log)
        self.assertEqual((self.state / "ts-key-seen").read_text(encoding="utf-8").strip(), self.ts_key)
        self.assertIn("funnel --bg --set-path /mcp http://127.0.0.1:8000/mcp", log)
        self.assertIn("checkout --detach " + EXAMPLE_PINS["VORTEX_REF"], log)
        self.assertRegex(log, r"systemctl enable --now vortex\.service")
        self.assertRegex(log, r"systemctl enable --now vortex-relay\.service")
        for name in ("vortex.service", "vortex-relay.service"):
            self.assertEqual((self.root / "etc/systemd/system" / name).read_bytes(), (BUNDLE / name).read_bytes())
        # Nothing secret is left behind outside the env file.
        leftovers = [
            p
            for p in self.root.rglob("*")
            if p.is_file() and p != env_path and self.ts_key in p.read_text(errors="ignore")
        ]
        self.assertEqual(leftovers, [])

    def test_private_repository_token_is_used_once_and_not_kept(self) -> None:
        self.env["STUB_GIT_PRIVATE"] = "1"
        token = "_".join(["github", "pat", "TESTONLY", "C" * 20])
        result = self.setup(stdin=f"{PG_HOST}\n{token}\n{self.ts_key}\n\n")
        output = result.stdout + result.stderr
        self.assertEqual(result.returncode, 0, output)
        self.assertEqual((self.state / "git-token-seen").read_text(encoding="utf-8").strip(), token)
        self.assertEqual((self.state / "git-user-seen").read_text(encoding="utf-8").strip(), "x-access-token")
        self.assertNotIn(token, output)
        self.assertNotIn(token, "\n".join(self.mutations()), "the token was passed on a command line")
        kept = [p for p in self.root.rglob("*") if p.is_file() and token in p.read_text(errors="ignore")]
        self.assertEqual(kept, [])

    def test_services_do_not_start_before_registration_is_confirmed(self) -> None:
        # Host and Tailscale key supplied, but the owner never presses Enter.
        result = self.setup(stdin=f"{PG_HOST}\n{self.ts_key}\n")
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(any("systemctl enable" in line for line in self.mutations()))
        self.assertNotIn("FORGE_REGISTERED", self.env_file())
        # A later run shows the hashes again for the same password, and then starts.
        password = self.env_file()["FORGE_PG_PASSWORD"]
        again = self.setup(stdin="\n")
        self.assertEqual(again.returncode, 0, again.stderr)
        self.assertEqual(self.env_file()["FORGE_PG_PASSWORD"], password)
        self.assertIn("SCRAM-SHA-256$4096:", again.stdout + again.stderr)
        self.assertTrue(any("systemctl enable --now vortex.service" in line for line in self.mutations()))

    def test_rerun_changes_nothing(self) -> None:
        first = self.setup(stdin=self.answers)
        self.assertEqual(first.returncode, 0, first.stderr)
        before = tree(self.root)
        self.log.unlink()
        for args in ((), ("--dry-run",)):
            # No stdin: any prompt would fail the run.
            again = self.setup(*args)
            self.assertEqual(again.returncode, 0, again.stdout + again.stderr)
            self.assertIn("nothing to change", again.stdout)
            self.assertNotIn("SCRAM-SHA-256", again.stdout + again.stderr)
            self.assertEqual(self.mutations(), [])
            self.assertEqual(tree(self.root), before)

    def test_bad_input_is_refused_without_echoing_it(self) -> None:
        bad_host = "evil.example.com/" + "x" * 8
        result = self.setup(stdin=f"{bad_host}\n")
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse((self.root / "etc" / "forge" / "forge.env").exists())
        bad_key = "has space " + "B" * 12
        result = self.setup(stdin=f"{PG_HOST}\n{bad_key}\n\n")
        self.assertNotEqual(result.returncode, 0)
        self.assertNotIn(bad_key, result.stdout + result.stderr)
        self.assertFalse(any(line.startswith("tailscale up") for line in self.mutations()))

    def test_unknown_arguments_are_refused(self) -> None:
        self.assertNotEqual(self.setup("--force").returncode, 0)
        self.assertEqual(self.mutations(), [])


GCLOUD_STUB = r"""
echo "gcloud $*" >>"$STUB_LOG"
case "$*" in
  *"instances describe"*) [ -f "$STUB_STATE/exists" ] && { echo forge-vm; exit 0; }; exit 1 ;;
  *"instances create"*) exit 0 ;;
esac
exit 99
"""


class CreateVmTests(unittest.TestCase):
    FREE_TIER = (
        "--machine-type=e2-micro",
        "--image-family=ubuntu-2404-lts-amd64",
        "--image-project=ubuntu-os-cloud",
        "--boot-disk-size=30GB",
        "--boot-disk-type=pd-standard",
        "--labels=app=forge",
    )

    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="forge-vm-create-"))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.bin = self.tmp / "bin"
        self.bin.mkdir()
        self.state = self.tmp / "state"
        self.state.mkdir()
        self.log = self.tmp / "gcloud.log"
        write_stub(self.bin, "gcloud", GCLOUD_STUB)
        self.env = {
            **{k: v for k, v in os.environ.items() if k not in ("GCP_PROJECT", "FORGE_ZONE", "FORGE_VM_NAME")},
            "GCP_PROJECT": "example-project",
            "STUB_STATE": str(self.state),
            "STUB_LOG": str(self.log),
            "PATH": f"{self.bin}{os.pathsep}{os.environ['PATH']}",
        }

    def create(self, *args: str, env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
        return run(["bash", str(BUNDLE / "create-vm.sh"), *args], env=env or self.env)

    def calls(self) -> list[str]:
        return self.log.read_text(encoding="utf-8").splitlines() if self.log.exists() else []

    def assert_free_tier(self, command: str, *, zone: str = "us-central1-a", name: str = "forge-vm") -> None:
        self.assertIn(f"compute instances create {name} ", command)
        for flag in (*self.FREE_TIER, "--project=example-project", f"--zone={zone}"):
            self.assertIn(flag + " ", command + " ", flag)
        self.assertIn("--metadata-from-file=user-data=" + str(BUNDLE / "cloud-init.yaml"), command)
        for key, filename in METADATA_FILES.items():
            self.assertIn(f"{key}={BUNDLE / filename}", command)
        for forbidden in ("--tags", "http-server", "https-server", "--scopes", "--service-account"):
            self.assertNotIn(forbidden, command)

    def test_dry_run_prints_the_command_and_runs_no_gcloud(self) -> None:
        result = self.create("--dry-run")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.calls(), [], "dry run invoked gcloud")
        self.assert_free_tier(" ".join(result.stdout.split()))

    def test_creates_with_exactly_the_free_tier_values(self) -> None:
        result = self.create()
        self.assertEqual(result.returncode, 0, result.stderr)
        calls = self.calls()
        self.assertEqual(len(calls), 2, calls)
        self.assertIn("compute instances describe forge-vm", calls[0])
        self.assert_free_tier(calls[1])

    def test_existing_instance_is_left_alone(self) -> None:
        (self.state / "exists").touch()
        result = self.create()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("already exists", result.stdout)
        self.assertFalse(any("instances create" in call for call in self.calls()))

    def test_zone_and_name_can_be_chosen_within_the_free_tier(self) -> None:
        result = self.create("--dry-run", "--zone", "us-west1-b", "--name", "forge-vm-2")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assert_free_tier(" ".join(result.stdout.split()), zone="us-west1-b", name="forge-vm-2")

    def test_refuses_zones_outside_the_free_tier(self) -> None:
        for zone in ("europe-west1-b", "us-east4-a", "us-east5-a", "us-west2-a", "us-central1", "us-central1-a;id", ""):
            for extra in ((), ("--dry-run",)):
                result = self.create("--zone", zone, *extra)
                self.assertNotEqual(result.returncode, 0, zone)
        self.assertEqual(self.calls(), [])

    def test_refuses_to_run_without_a_project(self) -> None:
        for project in (None, ""):
            env = {k: v for k, v in self.env.items() if k != "GCP_PROJECT"}
            if project is not None:
                env["GCP_PROJECT"] = project
            for extra in ((), ("--dry-run",)):
                result = self.create(*extra, env=env)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("GCP_PROJECT", result.stderr)
        self.assertEqual(self.calls(), [])

    def test_refuses_unknown_arguments_and_bad_names(self) -> None:
        self.assertNotEqual(self.create("--machine-type", "e2-standard-4").returncode, 0)
        self.assertNotEqual(self.create("--name", "Bad_Name").returncode, 0)
        self.assertEqual(self.calls(), [])

    def test_cloud_shell_block_is_one_command_for_the_owner(self) -> None:
        text = read("cloudshell-oneliner.md")
        blocks = re.findall(r"```bash\n(.*?)```", text, re.DOTALL)
        self.assertEqual(len(blocks), 1, "exactly one block to paste")
        block = blocks[0]
        self.assertIn("git clone", block)
        self.assertIn("GCP_PROJECT=bastion-471203", block)
        self.assertIn("deploy/forge-vm/create-vm.sh", block)
        self.assertNotIn("gcloud auth", block)


class RunbookTests(unittest.TestCase):
    STEPS = ("Tailscale", "Supabase", "Google Cloud", "On the VM", "Agent keys", "Close public SSH")

    def test_runbook_covers_every_owner_step(self) -> None:
        text = RUNBOOK.read_text(encoding="utf-8")
        sections = re.split(r"(?m)^## ", text)[1:]
        numbered = [s for s in sections if re.match(r"\d+\. ", s)]
        numbers = [re.match(r"\d+", s).group() for s in numbered]
        self.assertEqual(numbers, [str(i) for i in range(1, len(self.STEPS) + 1)])
        for section, step in zip(numbered, self.STEPS):
            heading = section.splitlines()[0]
            self.assertIn(step, heading)
            last = section.strip().split("\n\n")[-1]
            self.assertRegex(last, r"(?i)you'll see", f"step '{heading}' must end with what the owner sees")
        for needle in (
            "deploy/forge-vm/cloudshell-oneliner.md",
            "create-vm.sh",
            "Cloud Shell",
            "sudo /opt/forge/forge-setup.sh",
            "sudo /opt/forge/forge-keys.sh",
            "sudo /opt/forge/forge-check.sh",
            "paste these two hashes to Claude",
            "Supabase connector",
            "python -m vortex migrate",
            "vortex_app",
            "memory.write",
            "tailscale ssh",
            "e2-micro",
            "VORTEX_REF",
        ):
            self.assertIn(needle, text, needle)
        self.assertNotRegex(text, r"(?i)claude runs (deploy/forge-vm/)?create-vm|claude runs gcloud")

    def test_readme_lists_the_bundle(self) -> None:
        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        self.assertIn("`deploy/forge-vm/`", readme)
        self.assertIn("docs/FORGE_VM.md", readme)


if __name__ == "__main__":
    unittest.main()
