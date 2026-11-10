# Bastion

Bastion is the command centre for Jonny's agent fleet: one private place to see every agent,
summon them, approve their riskier work, and watch what it all costs. A sanitized public view
shows the fleet at work.

```
                 Control plane                         Data plane              Execution
  ┌───────────────────────────────────────┐     ┌──────────────────┐   ┌──────────────────────┐
  │ Command centre (Cloud Run, private,   │◄────│ UniSync ledger   │◄──│ GitHub Actions:      │
  │ behind Cloudflare Access)             │────►│ (Supabase):      │   │ agent runs,          │
  │ Public view (Cloudflare Workers,      │◄────│ append-only,     │◄──│ Quartermaster        │
  │ sanitized snapshot only)              │     │ hash-chained     │   │ PC + Oracle VM:      │
  └───────────────────────────────────────┘     └──────────────────┘   │ owner key, Vortex    │
                                                                       └──────────────────────┘
```

Nothing in the cloud calls into an agent; agents write to the ledger, dashboards read from it.

## What's here (M0)

| Path | What it is |
| --- | --- |
| `.github/workflows/quartermaster.yml` | The only path into Google Cloud: validate → test → build → deploy with no traffic → health check → shift traffic |
| `.github/workflows/deploy-hello.yml` | Calls Quartermaster for the `hello` service |
| `.github/workflows/ci.yml` | Policy check, unit tests, image build and smoke test on every PR |
| `deploy/policy.json` | Hard caps every service must respect (guarded file) |
| `tools/quartermaster/` | Manifest validator, standard library only |
| `services/hello/` | The smallest possible service, to prove the pipeline |
| `infra/gcp/bootstrap.sh` | One-time keyless Google Cloud setup |
| `.github/workflows/summon.yml` + `tools/summon/` | Summon: Lestinxor posts a task, Sebastian claims and runs it, the result lands on the UniSync ledger ([how](docs/SUMMON.md)) |
| `deploy/healthchecks.json` + `tools/deadman/` | Dead-man checks: every scheduled job in the fleet, synced to Healthchecks.io ([how](docs/DEADMAN.md)) |
| `tools/steward/` | PR steward agent: triages every open PR in the fleet and reports it on the ledger ([how](docs/STEWARD.md)) |
| `tools/report/` | Report: any agent on a PC or server tells the command centre what it is doing ([how](docs/REPORT.md)) |
| `tools/local/` | Run the command centre on your own machine, for agents that report in locally ([how](docs/REPORT.md#a-command-centre-on-your-own-machine)) |
| `deploy/forge-vm/` | The Forge VM: Vortex on one Always Free e2-micro behind Tailscale Funnel, with per-agent API keys ([runbook](docs/FORGE_VM.md)) |
| `docs/` | [Quartermaster runbook](docs/QUARTERMASTER.md) and [backlog](docs/BACKLOG.md) |

## Checks

```bash
python -m tools.quartermaster validate
python -m unittest discover -s tests -t .
python -m unittest discover -s services/hello
```

Everything is Python standard library: no install step.

## Ground rules

- Every deploy goes through Quartermaster. No one, human or agent, deploys by hand.
- Services are private by default and capped at 2 instances. The cap lives in code and in a guarded file.
- Google credentials are keyless (Workload Identity Federation), trusted only for the Quartermaster
  workflow on `main` of this repository.
- Agents follow [AGENTS.md](AGENTS.md).
