#!/usr/bin/env bash
# Forge VM bootstrap: run once by cloud-init, as root, on first boot. Safe to run again.
# Fetches the bundle from instance metadata, installs Tailscale and uv, and installs the systemd units.
# It starts nothing and handles no secret: forge-setup.sh does the rest, with the owner present.
set -euo pipefail

BUNDLE_DIR=/opt/forge
METADATA=http://metadata.google.internal/computeMetadata/v1/instance/attributes

# metadata key : file name : mode
FILES="
  forge-setup-sh:forge-setup.sh:0755
  forge-check-sh:forge-check.sh:0755
  forge-keys-sh:forge-keys.sh:0755
  scram-py:scram.py:0644
  vortex-service:vortex.service:0644
  vortex-relay-service:vortex-relay.service:0644
  forge-env-example:forge.env.example:0644
"

install -d -m 0755 -o root -g root "$BUNDLE_DIR"
for entry in $FILES; do
  IFS=: read -r key name mode <<<"$entry"
  curl -fsS --retry 5 -H 'Metadata-Flavor: Google' -o "$BUNDLE_DIR/$name.new" "$METADATA/$key"
  install -m "$mode" -o root -g root "$BUNDLE_DIR/$name.new" "$BUNDLE_DIR/$name"
  rm -f "$BUNDLE_DIR/$name.new"
done

# Tailscale, from its own apt repository for Ubuntu 24.04 (noble).
curl -fsSL https://pkgs.tailscale.com/stable/ubuntu/noble.noarmor.gpg \
  -o /usr/share/keyrings/tailscale-archive-keyring.gpg
curl -fsSL https://pkgs.tailscale.com/stable/ubuntu/noble.tailscale-keyring.list \
  -o /etc/apt/sources.list.d/tailscale.list
apt-get update
DEBIAN_FRONTEND=noninteractive apt-get install -y tailscale

# uv, for the unprivileged forge user only.
if [ ! -x /home/forge/.local/bin/uv ]; then
  runuser -u forge -- sh -c 'curl -LsSf https://astral.sh/uv/install.sh | sh'
fi

# Where forge-setup.sh checks Vortex-MCP out (it is a private repository, so that needs the owner).
install -d -m 0755 -o forge -g forge "$BUNDLE_DIR/vortex"
# Secrets will live here and nowhere else.
install -d -m 0700 -o root -g root /etc/forge

# Units are installed but neither enabled nor started.
install -m 0644 -o root -g root "$BUNDLE_DIR/vortex.service" "$BUNDLE_DIR/vortex-relay.service" /etc/systemd/system/
systemctl daemon-reload

echo "forge-bootstrap: done. Next: sudo $BUNDLE_DIR/forge-setup.sh"
