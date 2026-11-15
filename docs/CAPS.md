# Spend caps (G1d)

The caps in [`services/command-centre/caps.json`](../services/command-centre/caps.json) are enforced, not just shown.

| Cap | Key | Default |
| --- | --- | --- |
| Whole fleet, per UTC day | `daily_usd` | $5.00 |
| Any agent, per UTC day | `default_agent_daily_usd` | $2.00 |
| One agent, per UTC day | `agent_daily_usd.<name>` | none set |

An agent is **over** once its spend today reaches its cap. The fleet is over once the total reaches `daily_usd`.

## What happens

| Piece | When | What it does |
| --- | --- | --- |
| `python -m tools.caps gate --agent NAME` | First step of every summon (`summon.yml`) | Refuses the summon (exit 3) if the agent or the fleet is over. The agent stays paused until 00:00 UTC (10:00 Brisbane). |
| `python -m tools.caps check --notify` | Every hour (`caps.yml`) | Sends an urgent phone alert through ntfy for each breach, once per breach per day, and writes a table to the run summary. |

Both read spend with the command centre's own code (`spend_report` in `app.py`), so the Spend view and enforcement
always agree. The fleet-wide kill switch (`linx halt`) stays with the owner key on your PC; the caps never sign
anything.

Exit codes: 0 under cap or check done, 1 ledger or ntfy failed, 2 bad configuration, 3 gate refused.

## One-time setup (owner)

Add these repository secrets in **Settings → Secrets and variables → Actions**. Add names only to this repository,
never the values.

| Secret | Value |
| --- | --- |
| `LEDGER_DSN` | The read-only `bastion_reader` `postgresql://` URL. It's the same value as the command centre's `LEDGER_DSN` in Secret Manager. |
| `NTFY_TOPIC` | Your phone's ntfy topic ([NOTIFY.md](NOTIFY.md)). |
| `NTFY_TOKEN` | Optional. Only for a protected topic. |

Until `LEDGER_DSN` is set, summons run with a warning that caps are not enforced, and the hourly check fails.

## Test it (the G1d-2 *Done when*)

1. Set the agent's cap below what it has spent today, for example `"agent_daily_usd": {"sebastian": 0.000001}`.
   Merge that change.
2. Run **Actions → Spend caps → Run workflow**. Your phone buzzes once. Run it again: no second buzz.
3. Run **Actions → Summon → Run workflow**. It stops at *Check the spend caps* with the reason.
4. Put the cap back.
