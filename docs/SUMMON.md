# Summon

Ask Sebastian to do something from the command centre (or Actions → **Summon** → **Run workflow**) and
watch it move on the UniSync ledger. Costs $0 and needs no model API key: v0 runs `sebastian demo`,
Sebastian's offline scripted check.

| Step | Who posts | Kind |
| --- | --- | --- |
| Ask | `lestinxor` (orchestrator) | `task.request` with `flow: bastion-summon`, `agent: sebastian`, `requested_by: jonny` |
| Take it | `sebastian` | `task.claim` |
| Report | `sebastian` | `task.result` (`summary`) or `task.failed` (`reason`), both with the run's URL |

It follows LinX's conventions, so `linx log`, `linx brief` and the command centre show summons the same
way as nightly tasks. The orchestrator posts the request, as LinX's nightly loop does, because owner
(`jonny`) entries are kept for the owner's signed key.

## Safety

- Every post goes through the UniSync gateway (Supabase edge function `unisync`) with a token that can
  post as **one** agent and only its listed kinds. Only the token's sha256 is stored (`unisync_tokens`).
- Lestinxor's `SupabaseLedger` replays and checks the whole ledger before each write.
- Lestinxor and Sebastian are installed from pinned commits with `--no-deps`.
- The task title is passed to scripts as an environment variable, never inlined into shell.

## Setup (owner, once)

Two repository secrets, from the token values Claude creates (revoke with
`update unisync_tokens set revoked_at = now() where ...`):

| Secret | Agent | Allowed kinds |
| --- | --- | --- |
| `UNISYNC_ORCHESTRATOR_TOKEN` | `lestinxor` | `task.request` |
| `UNISYNC_AGENT_TOKEN` | `sebastian` | `task.claim`, `task.result`, `task.failed` |
