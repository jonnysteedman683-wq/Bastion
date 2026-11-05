# Steward: the PR steward agent

The steward triages every open PR across the fleet and reports it on the UniSync ledger, so the
command centre shows what needs merging, fixing or deciding. It is a `watcher` on the ledger: it
observes, reports and escalates, and never executes. On GitHub it is read-only (trust level R0):
it never merges, closes or comments.

| Piece | What it does |
| --- | --- |
| `python -m tools.steward scan --owner <user>` | Lists every open PR under the owner, reads its checks, sorts it into a bucket, prints a report |
| `--summary FILE` / `--json FILE` | Writes the Markdown report (the job summary) or the full triage as JSON |
| `--post` | Posts one `heartbeat` with the triage, plus one `escalate` per PR that needs you |
| `.github/workflows/steward.yml` (A2 🔒) | Runs the scan twice a day and on demand |

## Buckets

Each PR lands in exactly one bucket, most urgent first:

| Bucket | Meaning | Escalated |
| --- | --- | --- |
| `merge-ready` | Green, mergeable Dependabot or Renovate bump within one major version | No |
| `decide` | A major-version dependency bump | Yes |
| `ci-red` | A check fails on this PR and not on its base branch | Yes |
| `conflict` | Conflicts with its base branch | Yes |
| `review` | Green and mergeable; needs a review | No |
| `no-ci` | Mergeable, but no checks ran | No |
| `ci-pending` | Checks still running | No |
| `draft` | Draft | No |

Flags add to a bucket:
- **`stale`:** the PR hasn't been updated in 30 days. It's escalated.
- **`duplicate`:** an older PR names the same backlog item in its branch, e.g. two `agent/L7-…` branches. It's escalated.

The report also calls out a **flood**: a repo with 5 or more open PRs, which usually means an agent schedule is opening PRs nobody asked for.

A check that also fails on the base branch is noted but doesn't make the PR red. That's how a
broken deploy on `main` (for example Vercel) stays out of every PR's verdict.

## Escalations

An escalation's task id is `steward/<repo>#<number>@<head commit>`. The steward skips any it has posted
before, so you hear about a PR once, and again only after a new push. At most 10 escalations go out per run.

## One-time setup (owner)

1. **Roster row.** It appears on the dashboard as soon as it exists:
   ```sql
   insert into public.unisync_agents (name, role, repos) values ('steward', 'watcher', '{}') on conflict (name) do nothing;
   ```
2. **Ledger token.** Generate one with `python -c "import secrets; print(secrets.token_urlsafe(32))"`. Save it as the
   Bastion repository secret `UNISYNC_STEWARD_TOKEN`, then store only its hash:
   ```sql
   insert into public.unisync_tokens (agent, token_hash, kinds)
   values ('steward', encode(sha256('<token>'::bytea), 'hex'), '{heartbeat,escalate}');
   ```
3. **GitHub token.** Create a fine-grained personal access token with access to all your repositories. Give it
   read-only access to Pull requests, Checks, Commit statuses and Metadata, and nothing else. Save it as the Bastion
   repository secret `STEWARD_GITHUB_TOKEN`.
4. Merge A2 (the workflow), then run **Actions → PR steward → Run workflow** once.

To revoke the steward: `update public.unisync_tokens set revoked_at = now() where agent = 'steward';`

## Next: trust level R1

Once G2's trust ladder exists, a promoted steward may merge `merge-ready` PRs itself (backlog A3).
Until then, every merge is a person's decision.
