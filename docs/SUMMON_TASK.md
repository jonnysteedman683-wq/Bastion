# Summon a task (S4)

Give Sebastian a job in plain words on one of your repositories. It works on a fresh checkout and opens a **draft
PR** for you to review. Start it from **Actions → Summon task → Run workflow**.

## What it does

1. `python -m tools.taskpr check` refuses an empty task, a task over 2000 characters, a task starting with `---`, or
   a repository that isn't on the list.
2. `python -m tools.caps gate` refuses when Sebastian or the fleet is over today's spend cap ([CAPS.md](CAPS.md)).
3. The task appears in the command centre (`task.request`, then `task.claim`).
4. `sebastian run "<task>"` runs with **file tools only**:
   - no shell commands and no network tools;
   - `--unattended`, so every gated action is denied;
   - no memory carried between runs.
5. `python -m tools.taskpr publish` handles the result:
   - It commits any changes to `sebastian/<task id>`, pushes, and opens a draft PR against the default branch.
   - It refuses changes under `.github/`.
   - If nothing changed, it reports "No changes" and opens no PR.
6. The outcome posts to the ledger. The PR link is the result's summary, with token usage and a Langfuse trace if
   that's set up.

The publishing token is read only in step 5, after Sebastian has exited. It goes to git through the environment, so
the agent never sees it and it never appears in a command line or log.

## Repositories

Sebastian, Bastion, LESTINXOR-AGENT, O0, Omniordial and Sentinel. To add one, edit `REPOS` in `tools/taskpr` and the
`repo` choice list in `summon-task.yml` (owner-only).

## Model

GitHub Models was retired on 30 July 2026, so bring any OpenAI-compatible endpoint. These are the free options:

| Provider | `SEBASTIAN_BASE_URL` | Key from |
| --- | --- | --- |
| Google Gemini (Flash / Flash-Lite free tier) | `https://generativelanguage.googleapis.com/v1beta/openai` | [AI Studio](https://aistudio.google.com/apikey) |
| Groq (free tier) | `https://api.groq.com/openai/v1` | [Groq console](https://console.groq.com/keys) |

Free tiers are rate-limited. A run makes one model call per step, so keep `max_steps` modest. Check the model name
on the provider's page.

## One-time setup (owner)

In **Settings → Secrets and variables → Actions**:

| Kind | Name | Value |
| --- | --- | --- |
| Variable | `SEBASTIAN_BASE_URL` | From the table above |
| Variable | `SEBASTIAN_MODEL` | The provider's model id |
| Secret | `SEBASTIAN_MODEL_KEY` | The provider's API key |
| Secret | `TASKPR_TOKEN` | A fine-grained token (below) |

For `TASKPR_TOKEN`:

- **Repository access:** only the listed repositories.
- **Permissions:** Contents read and write, Pull requests read and write.
- **Do not grant Workflows.** Without it, GitHub itself refuses a push that edits workflows.

The UniSync tokens are the ones `summon.yml` already uses. `LEDGER_DSN` and the Langfuse keys are optional.
