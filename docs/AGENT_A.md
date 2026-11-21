# Agent-A: the sentry

Agent-A is a worker in the fleet that acts as a sentry: it patrols the perimeter and reports danger
before it lands. Like the [steward](STEWARD.md) it is a `watcher` on the ledger — it observes,
reports and escalates, and never executes (trust level R0). Its defense is proactive detection, and
a finding always names the danger's location, never the dangerous value itself.

| Piece | What it does |
| --- | --- |
| `python -m tools.agent_a patrol --owner <user>` | Drafts a patrol of every open PR under the owner, worst findings first |
| `--summary FILE` / `--json FILE` | Writes the Markdown report (the job summary) or the full patrol as JSON |
| `--post` | Posts one `heartbeat` with the patrol, plus one `escalate` per PR that needs you |
| `--notify` | Pushes a phone alert (ntfy, `tools.notify`) for critical findings, at most 3 per run |

## What it looks for

Two kinds of danger, both invisible to ordinary CI:

| Kind | Severity | What it means |
| --- | --- | --- |
| `secret` | critical | An **added line** carries a secret-shaped value: an AWS key, a GitHub token, a model-provider key, a Slack token, a Google API key, a private key, or a quoted credential literal. Once merged, it is in git history forever; rotate the credential and drop the change. |
| `guarded` | high | The PR touches a path on [AGENTS.md](../AGENTS.md)'s Never list: `.github/`, `deploy/policy.json`, `infra/`, `tools/quartermaster/`, `AGENTS.md`, `CLAUDE.md`. That is the edit a rogue or runaway PR makes to escalate itself. |

Deleted and unchanged lines are not scanned: a sentinel does not flag what is already in history.

With `--post` the sentry also watches the ledger: an agent that has failed `FAILURE_STREAK` (3)
outcomes in a row since its last result is reported, so a stuck agent gets noticed before it is
summoned again. Streak escalation is deduped per agent per UTC day.

## Escalations

PR escalations are deduped the steward's way: the task id is
`agent-a/<repo>#<number>@<head commit>`, so you hear about a PR once, and again only after a new
push. At most 10 escalations go out per run, sorted worst first, so a bad day cannot flood the
ledger.

## One-time setup (owner)

1. **Roster row.** It appears on the dashboard as soon as it exists:
   ```sql
   insert into public.unisync_agents (name, role, repos) values ('agent-a', 'watcher', '{}') on conflict (name) do nothing;
   ```
2. **Ledger token.** Generate one with `python -c "import secrets; print(secrets.token_urlsafe(32))"`. Save it as the
   Bastion repository secret `UNISYNC_AGENT_A_TOKEN`, then store only its hash:
   ```sql
   insert into public.unisync_tokens (agent, token_hash, kinds)
   values ('agent-a', encode(sha256('<token>'::bytea), 'hex'), '{heartbeat,escalate}');
   ```
3. **GitHub token.** Create a fine-grained personal access token with access to all your repositories. Give it
   read-only access to Pull requests and Metadata, and nothing else. Save it as the Bastion repository secret
   `AGENT_A_GITHUB_TOKEN`.
4. To hear critical findings on your phone, set the `NTFY_TOPIC` (and optionally `NTFY_URL`,
   `NTFY_TOKEN`) of [tools.notify](NOTIFY.md) as repository variables/secret.

Scheduling the patrol on a cron workflow is an owner step in `.github/` (guarded), like A2 for the
steward. Run **Actions → Agent-A sentry → Run workflow** once the workflow exists.

To revoke the sentry: `update public.unisync_tokens set revoked_at = now() where agent = 'agent-a';`

## Next: trust steps

Detection today, action later: once G2's trust ladder exists, a promoted sentry may do more than
escalate (for example auto-draft a revert of a `main` regression it saw). Until then it never
writes anywhere.
