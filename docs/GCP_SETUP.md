# Google Cloud setup, the easy way

About 10 minutes, all in your web browser. You will click, and paste one line. Nothing to install.

**One-click start:** [Open in Cloud Shell](https://shell.cloud.google.com/cloudshell/editor?cloudshell_git_repo=https://github.com/jonnysteedman683-wq/Bastion&cloudshell_git_branch=main&cloudshell_tutorial=docs/GCP_SETUP.md)
(do steps 1 and 2 first, then use this link in step 3).

---

## Step 1 · Make a project (2 min)

1. Open <https://console.cloud.google.com/projectcreate> and sign in with your Google account.
2. **Project name:** type `bastion`. Leave everything else as it is.
3. Click **Create**.
4. Under the name box there is a grey line, **Project ID**, e.g. `bastion-471203`.
   **Write it down.** You need it in step 3.

> Stuck on "accept terms" or a country picker? Accept and continue; that is normal for a new account.

## Step 2 · Turn on billing (3 min)

Google needs a card on file even for free-tier use. Bastion is capped to stay at or near $0.

1. Open <https://console.cloud.google.com/billing/linkedaccount>.
2. Pick your `bastion` project at the top if asked.
3. Click **Link a billing account** → **Create billing account**, then enter your card.
4. Safety net: open <https://console.cloud.google.com/billing/budgets> → **Create budget** →
   amount `5` → **Finish**. You get an email if spending ever approaches $5.

## Step 3 · Run the setup (3 min)

1. Click the **[Open in Cloud Shell](https://shell.cloud.google.com/cloudshell/editor?cloudshell_git_repo=https://github.com/jonnysteedman683-wq/Bastion&cloudshell_git_branch=main&cloudshell_tutorial=docs/GCP_SETUP.md)** link.
2. If it asks **"Trust this repo?"**, tick the box and click **Confirm**.
   If it asks to **Authorize**, click **Authorize**.
3. A black terminal panel opens at the bottom. Click inside it, then paste this one line and press
   **Enter** (replace `YOUR-PROJECT-ID` with the ID from step 1):

   ```bash
   PROJECT_ID=YOUR-PROJECT-ID bash infra/gcp/bootstrap.sh
   ```

4. Wait 1-3 minutes. Lines starting with `==>` show progress.
   It ends with **"Done. Set these repository variables:"** and five lines. Leave the panel open.

> Error saying billing is not enabled? Step 2 did not finish. Do it, then paste the same line again.
> Running it twice is always safe.

## Step 4 · Copy five values into GitHub (2 min)

1. Open <https://github.com/jonnysteedman683-wq/Bastion/settings/variables/actions>.
2. Click **New repository variable**. For each of the five lines the script printed, the left
   word is the **Name**, the right value is the **Value**:

   | Name | Value looks like |
   | --- | --- |
   | `GCP_PROJECT_ID` | `bastion-471203` |
   | `GCP_REGION` | `us-central1` |
   | `GCP_WIF_PROVIDER` | `projects/1234…/locations/global/workloadIdentityPools/github/providers/bastion` |
   | `GCP_DEPLOY_SA` | `quartermaster@bastion-471203.iam.gserviceaccount.com` |
   | `GCP_RUNTIME_SA` | `bastion-runtime@bastion-471203.iam.gserviceaccount.com` |

   These are not secrets; they are safe to paste anywhere.

## Step 5 · Add the approval button (1 min)

1. Open <https://github.com/jonnysteedman683-wq/Bastion/settings/environments> → **New environment**.
2. Name it `production` → **Configure environment**.
3. Tick **Required reviewers**, add yourself, click **Save protection rules**.

Every deploy now waits for you to click **Approve**.

## Done

Tell your agent "cloud setup done". It takes it from there (backlog item Q2: first deploy).

Optional hardening for later, also click-only: protect `main` at
[branch rules](https://github.com/jonnysteedman683-wq/Bastion/settings/rules) and turn on secret
scanning at [security](https://github.com/jonnysteedman683-wq/Bastion/settings/security_analysis).
