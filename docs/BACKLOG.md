# Backlog

Milestones follow the Bastion master plan. A milestone is done only when its exit test passes.
🔒 = touches a guarded path; owner only.

## M0 · Foundations
Exit: `hello` deploys, and a failed health check leaves the previous revision serving.

- [x] Q0 🔒 Quartermaster reusable workflow, manifest policy, `hello` service, keyless bootstrap
- [x] Q1 Owner runs `infra/gcp/bootstrap.sh` and sets the repository variables (docs/QUARTERMASTER.md)
- [x] Q2 First deploy of `hello`; then a deliberately broken `/health` proves traffic does not move
- [ ] Q3 🔒 Pin the `python:3.13-slim` base image by digest and add a weekly refresh PR
- [ ] Q4 🔒 Write each deploy record to the UniSync ledger (needs the Supabase project)

## M1 · First summon
Exit: summon to live result on the dashboard with no API key, at $0.

- [x] S1 Command centre v0: shadcn-admin shell, roster, live ledger feed (read-only Supabase role)
- [ ] S2 🔒 Cloudflare Access in front of the command centre
- [x] S3 Summon: post a `task.request`, dispatch `sebastian demo` on GitHub Actions, show the result

## M2 · Safety
Exit: a deliberate cap breach pauses the agent and alerts Jonny.

- G1 Cost loop: per-run spend to the ledger, Langfuse traces, per-agent and daily caps (split into G1a-G1d)
  - [x] G1a Usage contract (`usage` on any ledger event; `ledger/bastion_spend.sql`) and the command centre's Spend view: today, 7 and 30 days, a 30-day chart, per-agent spend against display-only caps in `caps.json`. *Done when* a `task.result` carrying `usage` shows in today's total within a minute.
  - G1b Agents report usage (split into G1b-1..G1b-3). *Done when* a summoned Sebastian run's tokens appear in the Spend view.
    - [x] G1b-1 `python -m tools.summon finish --usage FILE` attaches the agent's usage to `task.result`/`task.failed`: contract fields only, wrong types/negatives dropped, values clamped, `cost_usd` sent as integer `cost_micro_usd` (the gateway rejects non-integer numbers, so a float would lose the whole outcome); `bastion.spend_daily` counts `cost_micro_usd`. A bad file is a warning and the outcome still posts. *Done when* tests show a usage file riding on both outcomes with integers only, and a bad file never blocks the report.
    - [ ] G1b-2 Sebastian writes the file: `sebastian demo/run/resume --usage-out FILE` from `RunResult.usage` in `cli.py` (no change to guarded `agent.py`). Lands in the Sebastian repo. *Done when* `sebastian demo --usage-out f` writes the model and token counts.
    - [ ] G1b-3 🔒 Wire it: `summon.yml` passes `--usage-out` to `sebastian demo` and `--usage` to `summon finish`. *Done when* a summoned demo run shows in the Spend view.
  - [ ] G1c Langfuse traces per run, linked from the run's ledger event.
  - [ ] G1d Enforce caps: an agent over its daily cap is halted and Jonny is alerted (ntfy via `linx notify`). *Done when* a deliberate breach pauses the agent and alerts.
- [ ] G2 Trust ladder R0-R4 with automatic demotion
- [x] G4 🔒 Guard check: `.github/workflows/guard.yml` fails a PR touching a guarded path (AGENTS.md, CODEOWNERS) unless the owner adds `owner-approved`; `tests/test_guard.py` keeps the three lists in sync. *Done when* a PR changing `.github/` is red without the label and green with it.
- G3 Healthchecks.io dead-man checks for every scheduled job (split into G3a-G3c; see [DEADMAN.md](DEADMAN.md))
  - [x] G3a Check registry `deploy/healthchecks.json` and `python -m tools.deadman validate|plan|sync`
  - [ ] G3b 🔒 Ping `/start`, success and `/fail` from every listed job (edits `.github/` in 5 repos); *Done when* pausing AIH `analytics-archive` raises an alert within an hour and re-enabling it sends a recovery
  - [ ] G3c Command centre shows each check's status (read-only Healthchecks API key)

## M3 · Self-improvement
Exit: a run uses yesterday's memory, and a past failure is now a test.

- [ ] I1 Memory loop: nightly consolidation of `memory.write` entries
- [ ] I2 Failure loop: failed runs become scar eval cases across agents

## M4 · Showcase
Exit: the public page shows live counts and no private fields.

- [ ] P1 Sanitized snapshot Action → Cloudflare KV
- [ ] P2 Custom Cloudflare Workers public page

## M5 · Product
Exit: a fresh account deploys the whole stack with one command.

- [ ] R1 Infrastructure template, roles UI, docs

## Agents

- [x] A1 PR steward: `python -m tools.steward scan` triages every open PR, read-only, and posts a heartbeat and escalations ([STEWARD.md](STEWARD.md))
- [x] A2 🔒 Schedule the steward: `.github/workflows/steward.yml`, twice daily and on demand
- [ ] A3 Steward at R1: merge `merge-ready` dependency bumps itself once G2's trust ladder exists

## M7 · Owner controls
Exit: Jonny can see and decide everything waiting on him from the command centre, and the owner key never leaves his PC.

- [x] O1 Approvals view: `GET /api/approvals` lists every task awaiting approval, oldest first, with a copyable `linx approve`/`linx reject` command (none for task ids unsafe to paste into a shell); flags anything waiting over a day. *Done when* a pending task on the ledger shows in the view with a working copy button.
- [ ] O2 🔒 One-click decisions: the dashboard posts an unsigned `approval.intent` (new write-only role) and a LinX relay on the owner's PC signs it with the owner key. Needs S2 (Cloudflare Access) first. *Done when* clicking Approve lands a signed `approval.decision` on the ledger and the task leaves the view.
- [x] O3 Kill switch panel: show `bastion.control` (halted, reason, since) as a banner and a copyable `linx halt`/`linx resume`. *Done when* a halt on the ledger shows the banner within 5 seconds.

## M6 · AI Hub
Exit: any local AI can push events to Bastion, and the dashboard shows a unified mixed feed from all connected sources.

- [x] H1 Ingest endpoint: `POST /api/ingest` with API-key auth, flexible JSON payload, stored in a local SQLite buffer, merged into the feed. *Done when* a curl POST from a local AI shows up in the dashboard within 5 seconds.
- [ ] H2 Source registry: JSON config of named sources (type, endpoint, auth, poll interval) loaded at startup. *Done when* adding a source to config and restarting shows it in the roster.
- [ ] H3 Adapters: Ollama (`/api/tags`, `/api/ps`) and OpenAI-compatible (`/v1/models`) auto-discovery. *Done when* an Ollama instance on the LAN appears in the roster with its models listed.
- [ ] H4 Hermes reporter: pushes session/cron/memory events to `/api/ingest` (or directly to Supabase). *Done when* a Hermes session shows up in the Bastion feed.
- [ ] H5 Multi-source aggregation: aggregate events/roster across N Supabase DSNs + local buffer. *Done when* events from two different Supabase projects appear in one feed.
