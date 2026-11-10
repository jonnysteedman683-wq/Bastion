#!/usr/bin/env bash
# Agent API keys, issued on the Forge VM. Run by the owner:
#
#   sudo /opt/forge/forge-keys.sh <agent>           issue a new key for one agent (shown once)
#   sudo /opt/forge/forge-keys.sh --revoke <agent>  revoke every key that agent holds
#   sudo /opt/forge/forge-keys.sh --list            who holds keys (never the keys themselves)
#
# Vortex stores only a hash of each key. This script never stores, logs or copies a key: Vortex
# prints it straight to this terminal, once.
set -euo pipefail

FORGE_ROOT="${FORGE_ROOT:-}"
ENV_FILE="$FORGE_ROOT/etc/forge/forge.env"
VORTEX_DIR=/opt/forge/vortex

die() {
  printf 'forge-keys: %s\n' "$*" >&2
  exit 2
}

action=add
case "${1:-}" in
  --list) action=list; shift ;;
  --revoke) action=revoke; shift ;;
esac
if [ "$action" = list ]; then
  [ $# -eq 0 ] || die "usage: forge-keys.sh --list"
else
  [ $# -eq 1 ] || die "usage: forge-keys.sh <agent> | --revoke <agent> | --list"
fi
[ -r "$ENV_FILE" ] || die "$ENV_FILE cannot be read: run sudo /opt/forge/forge-setup.sh first, and use sudo here"

agent="${1:-}"
if [ "$action" != list ]; then
  [[ "$agent" =~ ^[a-z][a-z0-9]*(-[a-z0-9]+)*$ ]] || die "'$agent' is not an agent ID"
  agent_ids="$(sed -n 's/^VORTEX_AGENT_IDS=//p' "$ENV_FILE" | head -n 1)"
  case ",$agent_ids," in
    *",$agent,"*) ;;
    *) die "'$agent' is not one of the Forge agents ($agent_ids)" ;;
  esac
fi

# Vortex runs as user forge, with the same settings as the service, and writes to this terminal.
vortex() {
  systemd-run --wait --pipe --quiet --collect -p User=forge -p "EnvironmentFile=$ENV_FILE" \
    -p "WorkingDirectory=$VORTEX_DIR" "$VORTEX_DIR/.venv/bin/python" -m vortex "$@"
}

case "$action" in
  list) vortex keys list ;;
  revoke)
    echo "revoking every key held by $agent; it stops working within 30 seconds"
    vortex keys revoke "$agent"
    ;;
  add)
    echo "new key for $agent: store this in that agent's secret store now."
    echo "It is shown once and cannot be shown again. Do not paste it into a chat or a repository."
    vortex keys add "$agent"
    ;;
esac
