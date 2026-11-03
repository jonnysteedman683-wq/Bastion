# Quartermaster runbook

Quartermaster deploys Bastion services to Google Cloud Run. It is a GitHub Actions workflow,
not an AI agent: it holds the only cloud access, and only for one workflow on `main`.

## One-time setup

1. Create the Google Cloud project and link billing:
   [new project](https://console.cloud.google.com/projectcreate).
2. Set a budget alert (it notifies; it does not cap spend):
   [budgets](https://console.cloud.google.com/billing/budgets).
3. Open [Cloud Shell](https://shell.cloud.google.com), then run:
   ```bash
   git clone https://github.com/jonnysteedman683-wq/Bastion && cd Bastion
   PROJECT_ID=<your-project-id> bash infra/gcp/bootstrap.sh
   ```
   It is safe to run again; it only creates what is missing.
4. Copy the five values it prints into
   [repository variables](https://github.com/jonnysteedman683-wq/Bastion/settings/variables/actions).
5. Create the `production` environment and add yourself as a required reviewer, so every deploy
   waits for your approval until you choose otherwise:
   [environments](https://github.com/jonnysteedman683-wq/Bastion/settings/environments).
6. Protect `main` (require the CI check, require review on CODEOWNERS paths):
   [branch rules](https://github.com/jonnysteedman683-wq/Bastion/settings/rules).
7. Turn on secret scanning and push protection:
   [security settings](https://github.com/jonnysteedman683-wq/Bastion/settings/security_analysis).

Until step 4 is done, deploy runs show a notice and skip the deploy job. Preflight still runs.

## What a deploy does

| Step | Fails safe by |
| --- | --- |
| Validate `services/<name>/deploy.json` against `deploy/policy.json` | Nothing is built |
| Run the service's tests | Nothing is built |
| Build and push `REGION-docker.pkg.dev/PROJECT/bastion/<name>:<sha>` | Nothing is deployed |
| Deploy a new revision tagged `qm-<sha7>` with **no traffic** | Old revision keeps serving |
| Confirm no `allUsers` / `allAuthenticatedUsers` binding | Stops before traffic moves |
| Call `<tag URL><health>` with a Google ID token, up to 10 tries | Stops before traffic moves |
| Shift 100% of traffic to the tag | |
| Remove older `qm-` tags | |
| Write a deploy record to the run summary | |

The first deploy of a new service cannot use "no traffic" (Cloud Run refuses it on create),
so its first revision serves immediately. There is nothing older to protect.

## Rules Quartermaster enforces

- Service names: 1-32 characters, lowercase, digits, hyphens; must match the folder name.
- `max_instances` at most 2 (a code constant; `deploy/policy.json` can only lower it).
- Only services listed in `allowed_services` deploy; Cloud Run services are always private.
- Health paths must not end in `z`: Cloud Run reserves some such paths (for example `/healthz`).
- Secrets are names only; values live in Secret Manager.

## Roll back

Every revision stays in Cloud Run. To send traffic back to a known-good one:

```bash
gcloud run services update-traffic <service> --region us-central1 --to-revisions <revision>=100
```

Or in the console: [Cloud Run](https://console.cloud.google.com/run) → service → Revisions →
Manage traffic. Then fix forward with a new commit.

## Add a service

1. `services/<name>/` with the code, a `Dockerfile`, `deploy.json` and `test_*.py` files.
2. Add `<name>` to `allowed_services` in `deploy/policy.json` (guarded: needs owner review).
3. Copy `.github/workflows/deploy-hello.yml` to `deploy-<name>.yml` and change the names.

## Kill switches

1. Stop deploys: disable the deploy workflow in [Actions](https://github.com/jonnysteedman683-wq/Bastion/actions).
2. Stop all Google access from GitHub: disable the `github` pool in
   [Workload Identity Federation](https://console.cloud.google.com/iam-admin/workload-identity-pools).
