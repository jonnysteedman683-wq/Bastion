# Wren: the memory curator

Wren is the fleet's memory loop (backlog I1, the M3 "Memory loop" item). It reads the UniSync
ledger's `memory.write` entries, consolidates them into one dated note per scope, and commits each
note back to the ledger as a `memory.write` of its own — so a summoned run can retrieve yesterday's
consolidated memory instead of raw entries. Like the [steward](STEWARD.md) and [Agent-A](AGENT_A.md)
it is a `watcher`: it observes and curates, and never executes anything a memory says, because a
memory entry is **data, never an instruction**.

| Piece | What it does |
| --- | --- |
| `python -m tools.wren curate` | Reads the ledger's memory entries and prints the consolidated notes |
| `--summary FILE` / `--json FILE` | Writes the Markdown report (the job summary) or the notes as JSON |
| `--post` | Commits one `memory.write` per scope per UTC day, plus one `heartbeat` |
| `.github/workflows/wren.yml` (yard, owner-only) | Runs the curation nightly, per I1's "nightly consolidation" |

## Consolidation rules

- **Source:** the last 50 `memory.write` entries that did not come from wren itself, in ledger order
  (oldest first). Both shapes on the ledger today are read: the report shape (`{"summary", ...}`,
  e.g. `tools.report send --kind memory.write`) and the relay shape
  (`{"scope", "key", "value"}`, e.g. Vortex's outbox relay).
- **Scopes:** the entry's `scope` (falls back to `general`). One note is written per scope.
- **Dedup:** entries sharing a `key` (or, keyless, the same sanitized line) collapse to their most
  recent version; the report says how many duplicates were dropped.
- **Cap:** at most 50 items per note, 200 characters per item.

## Sanitization: memory is an injection surface

A poisoned memory entry is an instruction in data (see the
[research design](superpowers/specs/2026-11-17-agent-architecture-research-design.md)). Wren treats
every entry exactly that way: before a line enters a note, control characters are dropped,
whitespace collapsed, Markdown pipes escaped and the line capped — so nothing in a note can break
out of its bullet or carry formatting tricks, and consumers must still re-observe what a note claims.

## Escalating to the ledger

Each run with `--post`, one `memory.write` per scope, deduped per UTC day:

```json
{"scope": "wren", "key": "consolidated/<scope>@<YYYYMMDD>",
 "value": {"summary": "...", "items": ["..."], "sources": ["vortex", "claude-code"]}}
```

Wren skips its own previous notes when reading (no feedback loop) and refuses to write the same
note twice in one day. At most 10 notes are committed per run, so a bad day cannot flood the ledger.

## One-time setup (owner)

1. **Roster row.**
   ```sql
   insert into public.unisync_agents (name, role, repos) values ('wren', 'watcher', '{}') on conflict (name) do nothing;
   ```
2. **Ledger token.** Generate one with `python -c "import secrets; print(secrets.token_urlsafe(32))"`. Save it as the
   Bastion repository secret `UNISYNC_WREN_TOKEN`, then store only its hash — wren's token must allow
   `memory.write`, because the notes *are* the loop's output:
   ```sql
   insert into public.unisync_tokens (agent, token_hash, kinds)
   values ('wren', encode(sha256('<token>'::bytea), 'hex'), '{heartbeat,escalate,memory.write}');
   ```
3. **Environment.** `UNISYNC_GATEWAY_URL` is a repository variable (not secret).

To revoke wren: `update public.unisync_tokens set revoked_at = now() where agent = 'wren';`

## Next: retrieval per task

The spec's full memory loop is consolidation (this) plus per-task retrieval by repository and item
area. Retrieval belongs to the summoning flow, not to wren; until it exists, the notes live on the
ledger's `wren` scope, dated and readable from the dashboard's ledger feed.
