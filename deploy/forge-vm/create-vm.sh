#!/usr/bin/env bash
# Create the Forge VM: one Google Cloud Always Free e2-micro. Holds and prints no secret.
#
# Run by the owner, in Google Cloud Shell (see cloudshell-oneliner.md). Agents do not run this:
# AGENTS.md forbids agents from calling gcloud.
#
#   GCP_PROJECT=<project id> deploy/forge-vm/create-vm.sh [--dry-run] [--zone ZONE] [--name NAME]
#
# Safe to run again: if the instance already exists it says so and stops.
# --dry-run prints the gcloud command and runs nothing.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DRY_RUN=0
ZONE="${FORGE_ZONE:-us-central1-a}"
NAME="${FORGE_VM_NAME:-forge-vm}"

usage() {
  sed -n '2,10p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'
}
die() {
  printf 'create-vm: %s\n' "$1" >&2
  exit 2
}

while [ $# -gt 0 ]; do
  case "$1" in
    --dry-run) DRY_RUN=1 ;;
    --zone) [ $# -ge 2 ] || die "--zone needs a value"; ZONE="$2"; shift ;;
    --name) [ $# -ge 2 ] || die "--name needs a value"; NAME="$2"; shift ;;
    -h|--help) usage; exit 0 ;;
    *) die "unknown argument: $1 (the machine type, image and disk are fixed to the free tier)" ;;
  esac
  shift
done

[ -n "${GCP_PROJECT:-}" ] || die "GCP_PROJECT is not set; refusing to guess a project"

# Always Free covers one e2-micro, only in these three regions.
[[ "$ZONE" =~ ^us-(west1|central1|east1)-[a-z]$ ]] \
  || die "zone '$ZONE' is outside the free tier (use a zone in us-west1, us-central1 or us-east1)"
[[ "$NAME" =~ ^[a-z]([-a-z0-9]{0,61}[a-z0-9])?$ ]] || die "instance name '$NAME' is not a valid name"

# user-data is cloud-init; the other keys are the bundle files forge-bootstrap.sh fetches on first boot.
METADATA="user-data=$HERE/cloud-init.yaml"
METADATA+=",forge-bootstrap-sh=$HERE/forge-bootstrap.sh"
METADATA+=",forge-setup-sh=$HERE/forge-setup.sh"
METADATA+=",forge-check-sh=$HERE/forge-check.sh"
METADATA+=",forge-keys-sh=$HERE/forge-keys.sh"
METADATA+=",scram-py=$HERE/scram.py"
METADATA+=",vortex-service=$HERE/vortex.service"
METADATA+=",vortex-relay-service=$HERE/vortex-relay.service"
METADATA+=",forge-env-example=$HERE/forge.env.example"
case "$HERE" in *[,=\ ]*) die "the checkout path must not contain a comma, an equals sign or a space" ;; esac
for file in cloud-init.yaml forge-bootstrap.sh forge-setup.sh forge-check.sh forge-keys.sh scram.py \
  vortex.service vortex-relay.service forge.env.example; do
  [ -f "$HERE/$file" ] || die "missing bundle file: $HERE/$file"
done

# No network tags: nothing is opened for HTTP or HTTPS. Vortex is reached through Tailscale Funnel only.
CREATE=(
  gcloud compute instances create "$NAME"
  "--project=$GCP_PROJECT"
  "--zone=$ZONE"
  --machine-type=e2-micro
  --image-family=ubuntu-2404-lts-amd64
  --image-project=ubuntu-os-cloud
  --boot-disk-size=30GB
  --boot-disk-type=pd-standard
  --labels=app=forge
  "--metadata-from-file=$METADATA"
)

if [ "$DRY_RUN" -eq 1 ]; then
  echo "dry run: would first check whether instance '$NAME' exists in $ZONE, then run:"
  printf '  %s\n' "${CREATE[*]}"
  exit 0
fi

if gcloud compute instances describe "$NAME" "--project=$GCP_PROJECT" "--zone=$ZONE" \
  --format='value(name)' >/dev/null 2>&1; then
  echo "instance '$NAME' already exists in $ZONE (project $GCP_PROJECT); nothing to do"
  exit 0
fi

"${CREATE[@]}"
echo "created '$NAME' in $ZONE. First boot takes about five minutes; then follow docs/FORGE_VM.md step 4."
