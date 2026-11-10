#!/usr/bin/env bash
# Forge VM check: one PASS or FAIL line per check; exits non-zero if any check fails.
#
#   sudo /opt/forge/forge-check.sh [--revoke-agent <agent>]
#
# It asks (hidden) for the API keys of claude-1 and hermes-1, to prove real keys work end to end.
# For unattended runs they can come from the environment instead: FORGE_CHECK_KEY_CLAUDE_1 and
# FORGE_CHECK_KEY_HERMES_1.
#
# --revoke-agent <agent> runs the revocation check: it issues a new key for that agent, proves it
# works, revokes it, and proves it stops within 30 seconds. Revoking is per agent, so EVERY key that
# agent holds is revoked: pick one whose key you are about to (re)issue, then issue it a new one
# with forge-keys.sh. Without the option that check FAILs rather than silently passing.
#
# Side effects: one test message from claude-1 to hermes-1 and one test task, both marked forge-check.
# Keys and secrets are never printed, never put on a command line, and never written to disk
# (the patterns file used to search the journal lives on /run, which is memory, and is removed).
set -uo pipefail
umask 077

FORGE_ROOT="${FORGE_ROOT:-}"
ENV_FILE="$FORGE_ROOT/etc/forge/forge.env"
VORTEX_DIR=/opt/forge/vortex
PYTHON="$VORTEX_DIR/.venv/bin/python"
FUNNEL_TARGET=http://127.0.0.1:8000/mcp
REVOKE_AGENT=""
FAILS=0
PATTERNS=""

pass() { printf 'PASS  %s\n' "$1"; }
fail() {
  printf 'FAIL  %s%s\n' "$1" "${2:+: $2}"
  FAILS=$((FAILS + 1))
}
# check LABEL COMMAND...: PASS if the command succeeds; otherwise FAIL with its last line of output.
# Only used with helpers below, none of which ever prints a key or a secret.
check() {
  local label="$1" detail
  shift
  if detail="$("$@" 2>&1)"; then pass "$label"; else fail "$label" "$(printf '%s\n' "$detail" | tail -n 1)"; fi
}
cleanup() {
  if [ -n "$PATTERNS" ]; then rm -f "$PATTERNS"; fi
}
trap cleanup EXIT

while [ $# -gt 0 ]; do
  case "$1" in
    --revoke-agent)
      [ $# -ge 2 ] || { echo "forge-check: --revoke-agent needs an agent ID" >&2; exit 2; }
      REVOKE_AGENT="$2"
      shift
      ;;
    -h | --help) sed -n '2,17p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) echo "forge-check: unknown argument: $1" >&2; exit 2 ;;
  esac
  shift
done

if [ ! -r "$ENV_FILE" ]; then
  fail "setup" "$ENV_FILE cannot be read: run sudo /opt/forge/forge-setup.sh first, and use sudo here"
  exit 1
fi
if [ "$(id -u)" -ne 0 ]; then
  fail "setup" "run it with sudo"
  exit 1
fi

# setting NAME: one value from forge.env (captured by the caller, never printed).
setting() { sed -n "s/^$1=//p" "$ENV_FILE" | head -n 1; }
# As user forge with the service's settings, the way the units run.
as_forge() {
  systemd-run --wait --pipe --quiet --collect -p User=forge -p "EnvironmentFile=$ENV_FILE" \
    -p "WorkingDirectory=$VORTEX_DIR" "$@"
}

MCP_URL="$(setting VORTEX_PUBLIC_URL)/mcp"
AGENT_IDS="$(setting VORTEX_AGENT_IDS)"

# read_key AGENT: an agent's API key into $REPLY, from the environment or a hidden prompt.
read_key() {
  local var
  var="FORGE_CHECK_KEY_$(printf '%s' "$1" | tr 'a-z-' 'A-Z_')"
  REPLY=${!var:-}
  if [ -z "$REPLY" ] && [ -t 0 ]; then
    printf '%s API key (input hidden; Enter to skip): ' "$1" >&2
    IFS= read -r -s REPLY || true
    printf '\n' >&2
  fi
  [[ "$REPLY" =~ ^vtx_[A-Za-z0-9_-]{43}$ ]] || REPLY=
}

# A minimal MCP client (standard library). Credentials arrive in the environment, never as arguments.
read -r -d '' MCP_PY <<'PY' || true
import json, os, sys, urllib.error, urllib.request

URL = os.environ["FORGE_MCP_URL"]
FIRST, SECOND = (os.environ.get(name, "") for name in ("FORGE_CRED_A", "FORGE_CRED_B"))


def post(body, credential):
    request = urllib.request.Request(
        URL,
        data=json.dumps(body).encode(),
        method="POST",
        headers={
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
            "Authorization": "Bearer " + credential,
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=25) as response:
            return response.status, response.read().decode()
    except urllib.error.HTTPError as error:
        return error.code, ""
    except OSError as error:
        raise SystemExit("cannot reach the public address: " + type(error).__name__)


def call(name, arguments, credential):
    body = {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": name, "arguments": arguments}}
    status, raw = post(body, credential)
    if status != 200:
        raise SystemExit(f"{name}: HTTP {status}")
    raw = raw.strip()
    if raw.startswith("event:"):
        raw = next(line[6:] for line in raw.splitlines() if line.startswith("data: "))
    result = json.loads(raw).get("result")
    if not result or result.get("isError"):
        raise SystemExit(f"{name}: the tool reported an error")
    found = []

    def collect(value):
        if isinstance(value, dict):
            found.append(value)
            for inner in value.values():
                collect(inner)
        elif isinstance(value, list):
            for inner in value:
                collect(inner)

    collect(result.get("structuredContent"))
    for part in result.get("content", []):
        try:
            collect(json.loads(part.get("text", "")))
        except ValueError:
            pass
    return found


def nonce():
    return "forge-check " + os.urandom(8).hex()


command, args = sys.argv[1], sys.argv[2:]
if command == "accepted":
    if not any(item.get("agent_id") == args[0] for item in call("vortex_register_agent", {}, FIRST)):
        raise SystemExit("the key did not resolve to " + args[0])
elif command == "refused":
    body = {"jsonrpc": "2.0", "id": 1, "method": "tools/call",
            "params": {"name": "vortex_register_agent", "arguments": {}}}
    status, _ = post(body, FIRST)
    if status != 401:
        raise SystemExit(f"expected HTTP 401, got {status}")
elif command == "roundtrip":
    text = nonce()
    call("vortex_send_message", {"recipient_id": args[0], "body": text}, FIRST)
    inbox = call("vortex_get_inbox", {"limit": 50}, SECOND)
    message = next((item for item in inbox if item.get("body") == text), None)
    if message is None:
        raise SystemExit("the message did not arrive in the recipient's inbox")
    if message.get("message_id"):
        call("vortex_ack_message", {"message_id": message["message_id"]}, SECOND)
elif command == "task":
    call("vortex_create_task", {"goal": nonce()}, FIRST)
else:
    raise SystemExit("unknown command")
PY
# mcp COMMAND ARGS...: the caller sets FORGE_CRED_A (and FORGE_CRED_B) in the environment.
mcp() { FORGE_MCP_URL="$MCP_URL" python3 -c "$MCP_PY" "$@"; }

# Waits up to 60 s for the relay to have sent every outbox row. Runs with Vortex's own interpreter.
read -r -d '' OUTBOX_PY <<'PY' || true
import os, re, sys, time
import psycopg

schema = os.environ.get("VORTEX_SCHEMA", "forge")
if not re.fullmatch(r"[a-z_][a-z0-9_]{0,62}", schema):
    raise SystemExit("bad schema name")
query = (
    "select count(*) filter (where sent_at is null), count(*) filter (where sent_at is not null) "
    f'from "{schema}".outbox'
)
deadline = time.monotonic() + 60
try:
    with psycopg.connect(os.environ["VORTEX_STORE_URL"], autocommit=True) as connection:
        while True:
            unsent, sent = connection.execute(query).fetchone()
            if unsent == 0 and sent > 0:
                sys.exit(0)
            if time.monotonic() > deadline:
                raise SystemExit(f"{unsent} unsent and {sent} sent after 60 s")
            time.sleep(3)
except psycopg.Error as error:
    # The class only: a database error message must not be trusted to leave the connection string out.
    raise SystemExit("database error: " + type(error).__name__)
PY

# --- checks ---------------------------------------------------------------------------------------

vortex_healthy() {
  systemctl is-active --quiet vortex.service || { echo "vortex.service is not active"; return 1; }
  local code
  code="$(curl -s -o /dev/null -w '%{http_code}' --max-time 5 "$FUNNEL_TARGET" || true)"
  [ -n "$code" ] && [ "$code" != 000 ] || { echo "nothing answers on $FUNNEL_TARGET"; return 1; }
}
check "vortex healthy" vortex_healthy

relay_running() {
  systemctl is-active --quiet vortex-relay.service || { echo "vortex-relay.service is not active"; return 1; }
}
check "relay running" relay_running

funnel_only_mcp() {
  tailscale funnel status --json 2>/dev/null | python3 -c '
import json, sys
try:
    data = json.load(sys.stdin) or {}
except ValueError:
    data = {}
handlers = {}
for site in (data.get("Web") or {}).values():
    for path, handler in ((site or {}).get("Handlers") or {}).items():
        handlers[path] = (handler or {}).get("Proxy")
public = any((data.get("AllowFunnel") or {}).values())
ports = set(data.get("TCP") or {})
if handlers == {"/mcp": sys.argv[1]} and public and ports <= {"443"}:
    sys.exit(0)
print("published: " + (", ".join(sorted(handlers)) or "nothing") + ("" if public else " (funnel is off)"))
sys.exit(1)
' "$FUNNEL_TARGET"
}
check "funnel publishes only /mcp" funnel_only_mcp

read_key claude-1
cred_claude=$REPLY
read_key hermes-1
cred_hermes=$REPLY
REPLY=

if [ -n "$cred_claude" ]; then
  FORGE_CRED_A=$cred_claude check "claude-1 key accepted" mcp accepted claude-1
else
  fail "claude-1 key accepted" "no valid key was supplied"
fi
if [ -n "$cred_hermes" ]; then
  FORGE_CRED_A=$cred_hermes check "hermes-1 key accepted" mcp accepted hermes-1
else
  fail "hermes-1 key accepted" "no valid key was supplied"
fi

if [ -n "$cred_claude" ] && [ -n "$cred_hermes" ]; then
  FORGE_CRED_A=$cred_claude FORGE_CRED_B=$cred_hermes check "round trip claude-1 -> hermes-1" mcp roundtrip hermes-1
else
  fail "round trip claude-1 -> hermes-1" "needs both keys"
fi

outbox_relayed() {
  FORGE_CRED_A=$cred_claude mcp task || return 1
  as_forge "$PYTHON" -c "$OUTBOX_PY"
}
if [ -n "$cred_claude" ]; then
  check "outbox relayed to ledger" outbox_relayed
else
  fail "outbox relayed to ledger" "needs the claude-1 key to create a test task"
fi

# A second server on the same schema must refuse to start (Vortex holds a Postgres advisory lock).
second_vortex_refused() {
  as_forge /usr/bin/env VORTEX_PORT=8001 timeout 20 "$PYTHON" -m vortex >/dev/null 2>&1
  local status=$?
  if [ "$status" -eq 124 ]; then echo "a second server stayed up for 20 s"; return 1; fi
  if [ "$status" -eq 0 ]; then echo "a second server exited cleanly instead of refusing"; return 1; fi
}
check "second vortex refused" second_vortex_refused

revoked_key_refused() {
  local output issued started elapsed
  issued=
  case ",$AGENT_IDS," in *",$REVOKE_AGENT,"*) ;; *) echo "'$REVOKE_AGENT' is not a Forge agent"; return 1 ;; esac
  output="$(as_forge "$PYTHON" -m vortex keys add "$REVOKE_AGENT" 2>/dev/null)" || { echo "could not issue a key"; return 1; }
  if [[ "$output" =~ (vtx_[A-Za-z0-9_-]{43}) ]]; then issued=${BASH_REMATCH[1]}; fi
  output=
  [ -n "$issued" ] || { echo "no key came back from keys add"; return 1; }
  FORGE_CRED_A=$issued mcp accepted "$REVOKE_AGENT" || return 1
  as_forge "$PYTHON" -m vortex keys revoke "$REVOKE_AGENT" >/dev/null 2>&1 || { echo "could not revoke"; return 1; }
  started=$SECONDS
  while :; do
    if FORGE_CRED_A=$issued mcp refused >/dev/null 2>&1; then
      elapsed=$((SECONDS - started))
      [ "$elapsed" -le 30 ] || { echo "refused only after $elapsed s"; return 1; }
      return 0
    fi
    [ $((SECONDS - started)) -lt 40 ] || { echo "still accepted 40 s after revocation"; return 1; }
    sleep 2
  done
}
if [ -n "$REVOKE_AGENT" ]; then
  check "revoked key refused within 30 s" revoked_key_refused
else
  fail "revoked key refused within 30 s" "not run: pass --revoke-agent <agent> (every key of that agent is revoked)"
fi

# Every secret this VM holds, and the keys just used, must be absent from the whole journal.
# The patterns file is made here, not in the check, so the exit trap above always removes it.
PATTERNS="$(mktemp /run/forge-check.XXXXXX 2>/dev/null)" || PATTERNS=""
no_secrets_in_journal() {
  local name value
  [ -n "$PATTERNS" ] || { echo "cannot create a file on /run"; return 1; }
  for name in FORGE_PG_PASSWORD UNISYNC_VORTEX_TOKEN VORTEX_STORE_URL; do
    value="$(setting "$name")"
    if [ -n "$value" ]; then printf '%s\n' "$value" >>"$PATTERNS"; fi
  done
  for value in "$cred_claude" "$cred_hermes"; do
    if [ -n "$value" ]; then printf '%s\n' "$value" >>"$PATTERNS"; fi
  done
  value=
  [ -s "$PATTERNS" ] || { echo "found no secrets to look for; is setup complete?"; return 1; }
  if journalctl --no-pager -o cat 2>/dev/null | grep -qF -f "$PATTERNS"; then
    echo "a secret appears in the journal (journalctl); rotate it"
    return 1
  fi
}
check "no secrets in journal" no_secrets_in_journal
cleanup
PATTERNS=""

swap_active() {
  swapon --show --noheadings | grep -q . || { echo "no swap is active"; return 1; }
}
check "swap active" swap_active

if [ "$FAILS" -gt 0 ]; then
  printf '\n%s check(s) FAILED\n' "$FAILS"
  exit 1
fi
printf '\nall checks passed\n'
