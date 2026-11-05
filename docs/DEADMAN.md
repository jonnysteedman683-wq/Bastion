# Deadman: dead-man checks for scheduled jobs

Every scheduled GitHub Actions job in the fleet has a Healthchecks.io check. If a job stops running
or fails, Healthchecks.io alerts Jonny once the check's grace period has passed.

| Piece | What it does |
| --- | --- |
| `deploy/healthchecks.json` | The registry: one entry per scheduled job (slug, repo, workflow, cron, grace) |
| `python -m tools.deadman validate` | Checks the registry. No network |
| `python -m tools.deadman plan` | Prints the exact API payloads. No network, no key |
| `python -m tools.deadman sync` | Creates or updates every check (`unique: ["slug"]`). Never deletes |
| Ping step in each job (G3b 🔒) | Hits `https://hc-ping.com/$HC_PING_KEY/<slug>` with `/start`, then success or `/fail` |

## One-time setup (owner)

1. Create a Healthchecks.io project named **Bastion fleet**. Add your alert integrations (email, and Telegram or Discord if you want them).
2. In **Settings → API Access**, create a read-write API key and a ping key. Create a read-only key too (it's for G3c).
3. Run the sync once from your own machine. Don't commit the key:
   ```bash
   HC_API_KEY=... python -m tools.deadman sync
   ```
   It should print `9/9 checks in sync`.
4. For G3b, add the repository secret `HC_PING_KEY` to AIH, Sebastian, HORIZONTAL and neurocore.

## Adding a scheduled job

1. Add an entry to `deploy/healthchecks.json`. Copy the cron exactly from the workflow's `on.schedule`.
2. Pick a grace period that covers GitHub's start delay: at least 40 minutes, and 2–12 hours for daily and weekly jobs.
3. Run `python -m tools.deadman validate`, open a PR, and run `sync` once it's merged.
4. Add the ping step to that job (a guarded change in most repos).

A registry entry with no ping step goes DOWN after its first period. That's deliberate: it means a job isn't wired up yet.

## Grace periods

- GitHub can start a scheduled run 10–30 minutes late, and can skip runs under load.
- GitHub disables a public repository's schedules after 60 days with no repository activity. A dead-man check is how you find out.
- The free plan allows 20 checks; the fleet uses 9.
