# Angela: personal AI assistant

Angela is a personal AI agent that runs on your laptop as the core brain, with an optional
phone satellite (Angela-Pocket) for notifications, chat, and approvals on the go. She maintains
a long-term memory vault in Obsidian and can perform tasks using tools like filesystem access,
web search, and MCP servers.

## Connecting Angela to Bastion

Angela connects to Bastion using the **report tool** (push-based), allowing her to share her
activity with the fleet's command centre without exposing her personal data or capabilities.

### Report-based connection

1. **Ingest key.** Generate a unique key for Angela:
   ```bash
   python -c "import secrets; print(secrets.token_urlsafe(32))"
   ```
   Add it to the command centre's `INGEST_AGENT_KEYS` secret under the name `Angela`.

2. **Environment variables.** Set these where Angela runs (typically in your shell profile or `.env`):
   ```bash
   export BASTION_URL=https://bastion.example.com
   export BASTION_INGEST_KEY=<Angela's key>
   ```

3. **Report wrapper.** Wrap Angela's commands with the report tool:
   ```bash
   # For a chat session
   python -m tools.report run --label "Angela chat" -- angela-core
   
   # For a specific task
   python -m tools.report run --label "Angela research" -- angela-core --task "Research X"
   ```

4. **Or use send for specific events:**
   ```bash
   python -m tools.report send --kind task.completed --summary "Researched topic X, saved to vault"
   ```

### Alternative: Direct ledger connection

For deeper integration (optional):

1. **Roster row:**
   ```sql
   insert into public.unisync_agents (name, role, repos)
   values ('angela', 'assistant', '{}') on conflict (name) do nothing;
   ```

2. **Ledger token:**
   ```sql
   insert into public.unisync_tokens (agent, token_hash, kinds)
   values ('angela', encode(sha256('<token>'::bytea), 'hex'), '{heartbeat,task.completed}');
   ```

3. **Modify Angela's code** to post events directly to the ledger after completing tasks.

## What Angela reports

| Event Kind | When | Summary Example |
| --- | --- | --- |
| `run.started` | Session or task begins | "Angela chat: started" |
| `run.finished` | Session or task completes | "Angela chat: finished in 2m 15s" |
| `task.completed` | Specific task done | "Researched MCTS topology, saved 3 notes" |
| `memory.write` | Vault update | "Saved conversation to journal" |
| `run.failed` | Error occurred | "Angela: failed with exit code 1 after 30s" |

## Safety and privacy

- **No personal data leaves your machine**: The report tool only sends event metadata (kind, summary, source)
- **Summaries are sanitized**: Keep them to what happened, not what was said
- **Vault stays private**: Angela's Obsidian vault is never transmitted
- **Read-only by default**: Angela's filesystem tools are confined to `ANGELA_FS_ROOTS`

## Angela-Pocket

Angela-Pocket is the Android companion that forwards notifications and enables chat from your phone.
It connects to the Angela core on your laptop, not directly to Bastion. When Angela-Pocket triggers
an action that goes through Angela core, those events are reported through Angela's connection.

See [ANGELA-POCKET.md](ANGELA-POCKET.md) for phone-specific setup.

## Example workflow

```bash
# Start Angela with reporting
python -m tools.report run --label "Angela" -- angela-core

# Or report specific milestones from scripts
git clone <repo> && cd <repo>
python -m tools.report send --kind task.started --summary "Starting research on X"
python -m angela.core --task "Research X" --max-steps 5
python -m tools.report send --kind task.completed --summary "Research on X complete, 5 notes saved"
```

## One-time setup checklist

- [ ] Ingest key generated and added to `INGEST_AGENT_KEYS` as `Angela`
- [ ] `BASTION_URL` and `BASTION_INGEST_KEY` set in Angela's environment
- [ ] Angela appears on the Bastion roster (from `sources.json`)
- [ ] First heartbeat or event posted successfully

## Revocation

Remove Angela's key from `INGEST_AGENT_KEYS` and restart the command centre to revoke access.
