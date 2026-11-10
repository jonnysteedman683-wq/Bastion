#!/usr/bin/env bash
# Forge VM setup: run by the owner on the VM, after cloud-init has finished.
#
#   sudo /opt/forge/forge-setup.sh [--dry-run]
#
# What it does, in order. Each step is skipped when it is already done, so running it again on a
# working VM asks nothing and prints "nothing to change".
#   1. Writes the non-secret settings to /etc/forge/forge.env (mode 0600, owner root).
#   2. Generates the database password and the ledger relay token HERE, on the VM. They are never
#      shown and never leave the VM; only their hashes are printed, for Claude to register.
#   3. Checks out Vortex-MCP at the pinned commit and installs its dependencies, as user forge.
#   4. Joins Tailscale (asks for a one-off auth key, hidden) and publishes only /mcp through Funnel.
#   5. Waits until the owner confirms the hashes are registered, then starts Vortex and its relay.
#
# --dry-run prints what it would do and changes nothing.
# Rules this script keeps: it never prints a secret, never puts one on a command line (where `ps`
# would show it), and never traces its commands.
set -euo pipefail
umask 077

# FORGE_ROOT is for tests: every path below is created under it instead of under /.
FORGE_ROOT="${FORGE_ROOT:-}"
ENV_DIR="$FORGE_ROOT/etc/forge"
ENV_FILE="$ENV_DIR/forge.env"
BUNDLE_DIR="$FORGE_ROOT/opt/forge"
EXAMPLE="$BUNDLE_DIR/forge.env.example"
SCRAM="$BUNDLE_DIR/scram.py"
VORTEX_DIR="$BUNDLE_DIR/vortex"
UNIT_DIR="$FORGE_ROOT/etc/systemd/system"
RUN_DIR="$FORGE_ROOT/run"
UNITS=(vortex.service vortex-relay.service)
UV_BIN="${FORGE_UV:-/home/forge/.local/bin/uv}"
VORTEX_REPO=https://github.com/jonnysteedman683-wq/Vortex-MCP.git

# What Funnel publishes: this one path, to this one local address. Nothing else.
# Task 4 confirms the flags in funnel_on against `tailscale funnel --help` on the VM; adjust them there.
FUNNEL_TARGET=http://127.0.0.1:8000/mcp
funnel_on() {
  tailscale funnel --bg --set-path /mcp "$FUNNEL_TARGET"
}

DRY_RUN=0
CHANGES=0
ENV_DIRTY=0
RESTART=0
WORK=""
ASKPASS=""
secret=""
declare -A ENVV=()
ENV_ORDER=()

say() { printf '%s\n' "$*"; }
die() {
  printf 'forge-setup: %s\n' "$*" >&2
  exit 1
}
have() { command -v "$1" >/dev/null 2>&1; }

# step "what": counts a change. Returns 0 when the caller should do it, 1 in a dry run.
step() {
  CHANGES=$((CHANGES + 1))
  if [ "$DRY_RUN" -eq 1 ]; then
    say "would: $1"
    return 1
  fi
  say "$1"
}

cleanup() {
  secret=""
  if [ -n "$WORK" ]; then rm -rf "$WORK"; fi
}
trap cleanup EXIT

# A private scratch directory on /run (memory, not disk), made only when a secret must pass through a file.
work_dir() {
  if [ -z "$WORK" ]; then
    mkdir -p "$RUN_DIR"
    WORK="$(mktemp -d "$RUN_DIR/forge-setup.XXXXXX")"
  fi
}

# Run a command as the unprivileged forge user (as ourselves under FORGE_ROOT, for tests).
as_forge() {
  local -a pre=(env GIT_TERMINAL_PROMPT=0)
  if [ -n "$ASKPASS" ]; then pre+=("GIT_ASKPASS=$ASKPASS"); fi
  if [ -z "$FORGE_ROOT" ]; then
    runuser -u forge -- "${pre[@]}" HOME=/home/forge "$@"
  else
    "${pre[@]}" "$@"
  fi
}

# --- /etc/forge/forge.env -------------------------------------------------------------------------

load_env() {
  local line name
  [ -f "$ENV_FILE" ] || return 0
  while IFS= read -r line || [ -n "$line" ]; do
    case "$line" in '' | '#'*) continue ;; esac
    name="${line%%=*}"
    ENVV[$name]="${line#*=}"
    ENV_ORDER+=("$name")
  done <"$ENV_FILE"
}

set_env() {
  if [[ ! -v ENVV[$1] ]]; then ENV_ORDER+=("$1"); fi
  ENVV[$1]="$2"
  ENV_DIRTY=1
}

# need_env NAME VALUE: make NAME hold VALUE, as a counted step. The value is never printed.
need_env() {
  if [[ -v ENVV[$1] ]] && [ "${ENVV[$1]}" = "$2" ]; then return 0; fi
  if step "set $1 in $ENV_FILE"; then set_env "$1" "$2"; fi
}

write_env() {
  local tmp name
  [ "$ENV_DIRTY" -eq 1 ] || return 0
  install -d -m 0700 "$ENV_DIR"
  tmp="$(mktemp "$ENV_DIR/.forge.env.XXXXXX")"
  for name in "${ENV_ORDER[@]}"; do printf '%s=%s\n' "$name" "${ENVV[$name]}"; done >"$tmp"
  chmod 0600 "$tmp"
  if [ "$(id -u)" -eq 0 ]; then chown root:root "$tmp" "$ENV_DIR"; fi
  mv -f "$tmp" "$ENV_FILE"
  ENV_DIRTY=0
  RESTART=1
}

# --- input ----------------------------------------------------------------------------------------

# ask_secret NAME: read a secret without echo into $secret. The value is checked but never shown.
ask_secret() {
  secret=""
  printf '%s (input hidden): ' "$1" >&2
  IFS= read -r -s secret || true
  if [ -t 0 ]; then printf '\n' >&2; fi
  if [[ ! "$secret" =~ ^[A-Za-z0-9._~+/=-]+$ ]]; then
    secret=""
    die "$1: nothing entered, or it has a character that cannot be part of it. Nothing was changed for it."
  fi
}

# generate_into NAME: a new random secret, made on this VM, straight into the settings.
generate_into() {
  set_env "$1" "$(python3 "$SCRAM" generate)"
  [[ "${ENVV[$1]}" =~ ^[A-Za-z0-9_-]{43}$ ]] || die "could not generate $1"
}

show_hashes() {
  local verifier digest
  verifier="$(printf '%s' "${ENVV[FORGE_PG_PASSWORD]}" | python3 "$SCRAM" verifier)"
  digest="$(printf '%s' "${ENVV[UNISYNC_VORTEX_TOKEN]}" | python3 "$SCRAM" sha256)"
  say ""
  say "paste these two hashes to Claude; they are safe to share"
  say "  1. database password verifier, for role vortex_app:"
  say "     $verifier"
  say "  2. relay token sha256, for the ledger token 'vortex':"
  say "     $digest"
  say ""
}

# --- Vortex-MCP -----------------------------------------------------------------------------------

vortex_ready() {
  local head
  [ -e "$VORTEX_DIR/.venv/bin/python" ] || return 1
  head="$(as_forge git -C "$VORTEX_DIR" rev-parse HEAD 2>/dev/null)" || return 1
  [[ -n "$head" && "$head" == "$VORTEX_REF"* ]]
}

# git_remote ARGS...: a git command that talks to GitHub. Tries without credentials first; if that
# fails (Vortex-MCP is private), asks once for a read-only token and hands it to git through an
# askpass helper, so it is never on a command line, in the clone's config, or stored.
git_remote() {
  if as_forge git "$@" >/dev/null 2>&1; then return 0; fi
  say "Vortex-MCP could not be fetched without signing in (it is a private repository)."
  say "Enter a read-only GitHub token for it: fine-grained, that repository only, Contents: read."
  say "It is used for this fetch and not stored."
  ask_secret GITHUB_TOKEN
  work_dir
  printf '%s\n' "$secret" >"$WORK/github-token"
  secret=""
  ASKPASS="$WORK/askpass"
  {
    printf '#!/bin/sh\n'
    printf 'case "$1" in Username*) echo x-access-token ;; *) cat %q ;; esac\n' "$WORK/github-token"
  } >"$ASKPASS"
  chmod 0700 "$ASKPASS"
  if [ -z "$FORGE_ROOT" ]; then chown -R forge:forge "$WORK"; fi
  local status=0
  as_forge git "$@" || status=$?
  rm -f "$WORK/github-token" "$ASKPASS"
  ASKPASS=""
  [ "$status" -eq 0 ] || die "could not fetch Vortex-MCP, even with the token"
}

install_vortex() {
  if [ -d "$VORTEX_DIR/.git" ]; then
    git_remote -C "$VORTEX_DIR" fetch origin
  else
    git_remote clone "$VORTEX_REPO" "$VORTEX_DIR"
  fi
  as_forge git -C "$VORTEX_DIR" checkout --detach "$VORTEX_REF"
  as_forge "$UV_BIN" --directory "$VORTEX_DIR" sync --frozen --no-dev --extra unisync
  RESTART=1
}

# --- Tailscale ------------------------------------------------------------------------------------

ts_status() {
  if have tailscale; then tailscale status --json 2>/dev/null || true; fi
}

# ts_query state|name: read one field of `tailscale status --json` from standard input.
ts_query() {
  python3 -c '
import json, sys
try:
    data = json.load(sys.stdin)
except ValueError:
    data = {}
if sys.argv[1] == "state":
    print(data.get("BackendState") or "")
else:
    print(((data.get("Self") or {}).get("DNSName") or "").rstrip("."))
' "$1"
}

# funnel_state: ok (exactly /mcp is published), absent (nothing is), or other (something else is).
funnel_state() {
  {
    if have tailscale; then tailscale funnel status --json 2>/dev/null || true; fi
  } | python3 -c '
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
if not handlers and not ports:
    print("absent")
elif handlers == {"/mcp": sys.argv[1]} and public and ports <= {"443"}:
    print("ok")
else:
    print("other")
' "$FUNNEL_TARGET"
}

# --- main -----------------------------------------------------------------------------------------

for arg in "$@"; do
  case "$arg" in
    --dry-run) DRY_RUN=1 ;;
    -h | --help)
      sed -n '2,17p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'
      exit 0
      ;;
    *)
      printf 'forge-setup: unknown argument: %s (the only option is --dry-run)\n' "$arg" >&2
      exit 2
      ;;
  esac
done

[ -f "$EXAMPLE" ] || die "$EXAMPLE is missing: cloud-init has not finished (see /var/log/cloud-init-output.log)"
[ -f "$SCRAM" ] || die "$SCRAM is missing: cloud-init has not finished (see /var/log/cloud-init-output.log)"
if [ "$DRY_RUN" -eq 0 ] && [ -z "$FORGE_ROOT" ] && [ "$(id -u)" -ne 0 ]; then
  die "run it with sudo: sudo /opt/forge/forge-setup.sh"
fi
if [ "$DRY_RUN" -eq 1 ]; then say "dry run: nothing will be changed"; fi

if [ -e "$ENV_FILE" ] && [ ! -r "$ENV_FILE" ]; then die "$ENV_FILE is readable by root only: use sudo"; fi
load_env
if [ -f "$ENV_FILE" ] && [ "$(stat -c %a "$ENV_FILE")" != 600 ]; then
  if step "make $ENV_FILE readable by root only"; then chmod 0600 "$ENV_FILE"; fi
fi

# 1. Non-secret settings, from forge.env.example. A value already in forge.env is left alone.
VORTEX_REF=""
while IFS= read -r line || [ -n "$line" ]; do
  case "$line" in '' | '#'*) continue ;; esac
  name="${line%%=*}"
  value="${line#*=}"
  [ -n "$value" ] || continue
  if [ "$name" = VORTEX_REF ]; then
    VORTEX_REF="$value"
  elif [[ ! -v ENVV[$name] ]]; then
    need_env "$name" "$value"
  fi
done <"$EXAMPLE"
[[ "$VORTEX_REF" =~ ^[0-9a-f]{7,40}$ ]] || die "VORTEX_REF in $EXAMPLE is not a commit hash"

if [[ ! -v ENVV[FORGE_PG_HOST] ]]; then
  if step "ask for FORGE_PG_HOST (the Supabase \"Session pooler\" host; not a secret)"; then
    printf 'FORGE_PG_HOST, the Supabase "Session pooler" host (like aws-0-<region>.pooler.supabase.com): ' >&2
    IFS= read -r pg_host || true
    [[ "$pg_host" =~ ^[a-z0-9]([a-z0-9.-]*[a-z0-9])?\.pooler\.supabase\.com$ ]] \
      || die "that is not a Supabase pooler host name (only the host: no user, port or path)"
    set_env FORGE_PG_HOST "$pg_host"
  fi
fi

# 2. Secrets made on this VM. They go only into forge.env.
for name in FORGE_PG_PASSWORD UNISYNC_VORTEX_TOKEN; do
  if [[ ! -v ENVV[$name] ]]; then
    if step "generate $name on this VM (never shown, never leaves it)"; then generate_into "$name"; fi
  fi
done

# The connection string, in pieces: user, generated password, pooler host, port 5432, database postgres.
if [[ -v ENVV[FORGE_PG_USER] && -v ENVV[FORGE_PG_PASSWORD] && -v ENVV[FORGE_PG_HOST] ]]; then
  store_url="postgresql"
  store_url+="://${ENVV[FORGE_PG_USER]}"
  store_url+=":${ENVV[FORGE_PG_PASSWORD]}"
  store_url+="@${ENVV[FORGE_PG_HOST]}:5432/postgres?sslmode=require"
  need_env VORTEX_STORE_URL "$store_url"
  store_url=""
else
  step "set VORTEX_STORE_URL in $ENV_FILE" || true
fi
write_env

# 3. Vortex-MCP at the pinned commit.
if ! vortex_ready; then
  if step "check out Vortex-MCP at $VORTEX_REF and install it (uv sync --extra unisync), as user forge"; then
    install_vortex
  fi
fi

# 4. Tailscale, then Funnel for /mcp only.
ts_state="$(ts_status | ts_query state)"
if [ "$ts_state" != Running ]; then
  if step "ask for the one-off Tailscale auth key (TS_AUTHKEY, hidden) and run tailscale up"; then
    have tailscale || die "tailscale is not installed: cloud-init has not finished"
    ask_secret TS_AUTHKEY
    work_dir
    printf '%s\n' "$secret" >"$WORK/ts-authkey"
    secret=""
    # The key is read from the file by tailscale itself; --ssh lets the owner use `tailscale ssh` later.
    tailscale up "--auth-key=file:$WORK/ts-authkey" --ssh
    rm -f "$WORK/ts-authkey"
    ts_state="$(ts_status | ts_query state)"
    [ "$ts_state" = Running ] || die "tailscale did not come up (state: ${ts_state:-unknown})"
  fi
fi

ts_name=""
if [ "$ts_state" = Running ]; then
  ts_name="$(ts_status | ts_query name)"
  [[ "$ts_name" =~ ^[a-z0-9-]+(\.[a-z0-9-]+)+\.ts\.net$ ]] \
    || die "this machine has no ts.net name: turn on MagicDNS and HTTPS certificates in the Tailscale admin console"
fi

case "$(funnel_state)" in
  ok) ;;
  absent)
    if step "turn on funnel for /mcp only (-> $FUNNEL_TARGET)"; then funnel_on; fi
    ;;
  *)
    if step "reset funnel (something other than /mcp is published), then turn it on for /mcp only"; then
      tailscale funnel reset
      funnel_on
    fi
    ;;
esac

# The public address is the Funnel name; Vortex only answers requests addressed to it.
if [ -n "$ts_name" ]; then
  need_env VORTEX_PUBLIC_URL "https://$ts_name"
  need_env VORTEX_ALLOWED_HOSTS "$ts_name,$ts_name:443"
  need_env VORTEX_ALLOWED_ORIGINS "https://$ts_name"
else
  step "set VORTEX_PUBLIC_URL, VORTEX_ALLOWED_HOSTS and VORTEX_ALLOWED_ORIGINS from the Funnel name" || true
fi
write_env

# 5. Registration: Vortex cannot log in until Claude has registered the two hashes.
if [ "${ENVV[FORGE_REGISTERED]:-}" != yes ]; then
  if step "show the two hashes and wait until Claude confirms they are registered"; then
    show_hashes
    say "press Enter once Claude confirms they are registered"
    IFS= read -r _ || die "not confirmed, so nothing was started. Run this again once Claude has confirmed."
    set_env FORGE_REGISTERED yes
    write_env
  fi
fi

units_changed=0
for unit in "${UNITS[@]}"; do
  if ! cmp -s "$BUNDLE_DIR/$unit" "$UNIT_DIR/$unit"; then
    if step "install $unit"; then
      install -D -m 0644 "$BUNDLE_DIR/$unit" "$UNIT_DIR/$unit"
      units_changed=1
    fi
  fi
done
if [ "$units_changed" -eq 1 ]; then
  systemctl daemon-reload
  RESTART=1
fi
for unit in "${UNITS[@]}"; do
  if have systemctl && systemctl is-enabled --quiet "$unit" 2>/dev/null \
    && systemctl is-active --quiet "$unit" 2>/dev/null; then
    if [ "$RESTART" -eq 1 ]; then
      if step "restart $unit (its settings changed)"; then systemctl restart "$unit"; fi
    fi
  else
    if step "enable and start $unit"; then systemctl enable --now "$unit"; fi
  fi
done

if [ "$CHANGES" -eq 0 ]; then
  say "nothing to change"
elif [ "$DRY_RUN" -eq 1 ]; then
  say "dry run: $CHANGES step(s) planned, nothing was changed"
else
  say ""
  say "done. Vortex is at https://$ts_name/mcp"
  say "next: sudo /opt/forge/forge-keys.sh <agent> for each agent, then sudo /opt/forge/forge-check.sh"
fi
