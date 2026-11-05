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

- [ ] S1 Command centre v0: shadcn-admin shell, roster, live ledger feed (read-only Supabase role)
- [ ] S2 🔒 Cloudflare Access in front of the command centre
- [x] S3 Summon: post a `task.request`, dispatch `sebastian demo` on GitHub Actions, show the result

## M2 · Safety
Exit: a deliberate cap breach pauses the agent and alerts Jonny.

- [ ] G1 Cost loop: per-run spend to the ledger, Langfuse traces, per-agent and daily caps
- [ ] G2 Trust ladder R0-R4 with automatic demotion
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

## Proposed
