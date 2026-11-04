#!/usr/bin/env bash
# One-time, idempotent Google Cloud setup for Quartermaster.
#
# Run it in Cloud Shell (https://shell.cloud.google.com) after creating the project
# and linking billing:
#
#   bash infra/gcp/bootstrap.sh your-project-id
#   (or: PROJECT_ID=your-project-id bash infra/gcp/bootstrap.sh)
#
# It creates, or leaves alone if present:
#   - the APIs Quartermaster needs
#   - Artifact Registry repo "bastion" with a cleanup policy (stays inside the free 0.5 GB)
#   - service accounts: quartermaster (deploys) and bastion-runtime (runs services, no roles yet)
#   - a Workload Identity pool "github" and provider "bastion" that trust ONE thing:
#     the Quartermaster workflow, on main, in this exact repository (matched by
#     immutable repository id and owner id, so a renamed or recreated repo cannot use it)
#   - the narrowest IAM bindings that make deploys work
# No service-account keys are created, ever.
set -euo pipefail

# Project: $PROJECT_ID, else the first argument (bash bootstrap.sh my-project-id).
PROJECT_ID="${PROJECT_ID:-${1:-}}"
PROJECT_ID="${PROJECT_ID:?Pass the project id: bash infra/gcp/bootstrap.sh my-project-id}"
REGION="${REGION:-us-central1}"
GITHUB_REPO="${GITHUB_REPO:-jonnysteedman683-wq/Bastion}"
GITHUB_REPO_ID="${GITHUB_REPO_ID:-1444910871}"
GITHUB_OWNER_ID="${GITHUB_OWNER_ID:-292220759}"

AR_REPO="bastion"
POOL="github"
PROVIDER="bastion"
DEPLOYER="quartermaster"
RUNTIME="bastion-runtime"

log() { printf '\n==> %s\n' "$*"; }
gc() { gcloud --project "$PROJECT_ID" --quiet "$@"; }

PROJECT_NUMBER="$(gcloud projects describe "$PROJECT_ID" --format='value(projectNumber)')"
DEPLOYER_EMAIL="${DEPLOYER}@${PROJECT_ID}.iam.gserviceaccount.com"
RUNTIME_EMAIL="${RUNTIME}@${PROJECT_ID}.iam.gserviceaccount.com"
POOL_ID="projects/${PROJECT_NUMBER}/locations/global/workloadIdentityPools/${POOL}"

log "Enabling APIs"
gc services enable \
  run.googleapis.com \
  artifactregistry.googleapis.com \
  iam.googleapis.com \
  iamcredentials.googleapis.com \
  sts.googleapis.com \
  cloudresourcemanager.googleapis.com \
  secretmanager.googleapis.com

log "Artifact Registry repository ${AR_REPO} (${REGION})"
if ! gc artifacts repositories describe "$AR_REPO" --location "$REGION" >/dev/null 2>&1; then
  gc artifacts repositories create "$AR_REPO" --location "$REGION" \
    --repository-format docker --description "Bastion service images (Quartermaster)"
fi
cleanup_policy="$(mktemp)"
trap 'rm -f "$cleanup_policy"' EXIT
cat >"$cleanup_policy" <<'JSON'
[
  {"name": "keep-recent", "action": {"type": "Keep"}, "mostRecentVersions": {"keepCount": 5}},
  {"name": "delete-old", "action": {"type": "Delete"}, "condition": {"tagState": "any", "olderThan": "30d"}}
]
JSON
gc artifacts repositories set-cleanup-policies "$AR_REPO" --location "$REGION" \
  --policy "$cleanup_policy" --no-dry-run

log "Service accounts"
for sa in "$DEPLOYER" "$RUNTIME"; do
  if ! gc iam service-accounts describe "${sa}@${PROJECT_ID}.iam.gserviceaccount.com" >/dev/null 2>&1; then
    gc iam service-accounts create "$sa" --display-name "Bastion ${sa}"
  fi
done

log "Workload Identity pool ${POOL} and provider ${PROVIDER}"
if ! gc iam workload-identity-pools describe "$POOL" --location global >/dev/null 2>&1; then
  gc iam workload-identity-pools create "$POOL" --location global --display-name "GitHub Actions"
fi
mapping="google.subject=assertion.sub,attribute.repository_id=assertion.repository_id"
mapping+=",attribute.repository_owner_id=assertion.repository_owner_id,attribute.ref=assertion.ref"
mapping+=",attribute.job_workflow_ref=assertion.job_workflow_ref"
condition="assertion.repository_id == '${GITHUB_REPO_ID}'"
condition+=" && assertion.repository_owner_id == '${GITHUB_OWNER_ID}'"
condition+=" && assertion.ref == 'refs/heads/main'"
condition+=" && assertion.job_workflow_ref == '${GITHUB_REPO}/.github/workflows/quartermaster.yml@refs/heads/main'"
provider_args=(--location global --workload-identity-pool "$POOL"
  --issuer-uri "https://token.actions.githubusercontent.com"
  --attribute-mapping "$mapping" --attribute-condition "$condition")
if gc iam workload-identity-pools providers describe "$PROVIDER" --location global \
    --workload-identity-pool "$POOL" >/dev/null 2>&1; then
  gc iam workload-identity-pools providers update-oidc "$PROVIDER" "${provider_args[@]}"
else
  gc iam workload-identity-pools providers create-oidc "$PROVIDER" "${provider_args[@]}" \
    --display-name "Bastion Quartermaster"
fi

log "IAM bindings (least privilege)"
# The GitHub identity may act as the deployer account, and nothing else.
gc iam service-accounts add-iam-policy-binding "$DEPLOYER_EMAIL" \
  --role roles/iam.workloadIdentityUser \
  --member "principalSet://iam.googleapis.com/${POOL_ID}/attribute.repository_id/${GITHUB_REPO_ID}" >/dev/null
# Deploy Cloud Run services, and call them for the post-deploy health check.
for role in roles/run.developer roles/run.invoker; do
  gc projects add-iam-policy-binding "$PROJECT_ID" --role "$role" \
    --member "serviceAccount:${DEPLOYER_EMAIL}" --condition=None >/dev/null
done
# Push images to the one repository only.
gc artifacts repositories add-iam-policy-binding "$AR_REPO" --location "$REGION" \
  --role roles/artifactregistry.writer --member "serviceAccount:${DEPLOYER_EMAIL}" >/dev/null
# Attach the runtime account to services, and no other account.
gc iam service-accounts add-iam-policy-binding "$RUNTIME_EMAIL" \
  --role roles/iam.serviceAccountUser --member "serviceAccount:${DEPLOYER_EMAIL}" >/dev/null

log "Done. Set these repository variables:"
cat <<EOF
  https://github.com/${GITHUB_REPO}/settings/variables/actions

  GCP_PROJECT_ID    ${PROJECT_ID}
  GCP_REGION        ${REGION}
  GCP_WIF_PROVIDER  ${POOL_ID}/providers/${PROVIDER}
  GCP_DEPLOY_SA     ${DEPLOYER_EMAIL}
  GCP_RUNTIME_SA    ${RUNTIME_EMAIL}
EOF
