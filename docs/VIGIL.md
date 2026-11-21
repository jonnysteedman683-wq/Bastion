# VIGIL: the security scout

VIGIL is a proactive security scanner that watches every repository in a GitHub account.
Every night it runs seven security checks and files issues for real problems. It can also
post heartbeat events to the UniSync ledger so Bastion's command centre shows its activity
beside the fleet.

## Connecting VIGIL to Bastion

VIGIL has built-in support for posting to the UniSync ledger. This is the recommended
connection method, as it allows VIGIL to appear as a first-class fleet agent.

### Ledger-based connection (recommended)

VIGIL's README already includes partial setup. Complete it as follows:

1. **Roster row.** Add VIGIL to the ledger's agent roster:
   ```sql
   insert into public.unisync_agents (name, role, repos)
   values ('vigil', 'watcher', '{}') on conflict (name) do nothing;
   ```

2. **Ledger token.** Generate a secure token:
   ```bash
   python -c "import secrets; print(secrets.token_urlsafe(32))"
   ```
   Save it as the **VIGIL** repository secret `UNISYNC_VIGIL_TOKEN`, then store only its hash:
   ```sql
   insert into public.unisync_tokens (agent, token_hash, kinds)
   values ('vigil', encode(sha256('<token>'::bytea), 'hex'), '{heartbeat}');
   ```

3. **Environment in VIGIL.** Set these in the VIGIL workflow:
   - `UNISYNC_GATEWAY_URL`: the UniSync gateway URL (repository variable, not secret)
   - `UNISYNC_VIGIL_TOKEN`: the token from step 2 (secret)

4. **Enable Bastion feed in VIGIL.** The VIGIL code already has this support. Ensure the
   `scan.yml` workflow includes the step to post to the ledger after each scan.

### What VIGIL reports

| Event Kind | When | Summary Example |
| --- | --- | --- |
| `heartbeat` | After each scan | "Security scan finished: 12 repos checked" |

The event body (not shown in public views) contains:
- Open counts by severity (critical, high, medium, low)
- New and fixed findings since last scan
- Scanner errors
- Run URL

### Configuration in VIGIL

In `vigil.toml`, ensure the Bastion integration is enabled:
```toml
[bastion]
enabled = true
# Optional: set a custom agent name (defaults to 'vigil')
# agent_name = "vigil"
```

### Verification

After setup:
1. Run a manual scan: **Actions  Security scan  Run workflow**
2. Check Bastion's feed for VIGIL's heartbeat
3. Verify VIGIL appears on the roster with status "active"

## Alternative: Report-based connection

For simpler testing or if you don't want to modify the ledger:

1. **Add to sources.json** (already done in Bastion's `services/command-centre/sources.json`)

2. **Generate an ingest key** for VIGIL:
   ```bash
   python -c "import secrets; print(secrets.token_urlsafe(32))"
   ```
   Add it to the command centre's `INGEST_AGENT_KEYS` as `VIGIL`

3. **Modify scan workflow** to use the report tool after each scan:
   ```yaml
   - name: Report to Bastion
     run: |
       python -m tools.report send \
         --kind heartbeat \
         --summary "Security scan finished: ${{ steps.scan.outputs.repos_checked }} repos checked"
   ```

## Safety

- VIGIL is **read-only** on GitHub (read-only token)
- It only reports aggregate statistics to Bastion, never specific findings
- Findings go to VIGIL's private repository as GitHub issues
- The heartbeat summary is sanitized and contains no sensitive information

## One-time setup checklist

- [ ] Roster row added to `public.unisync_agents`
- [ ] Token generated and stored as `UNISYNC_VIGIL_TOKEN` in VIGIL repo
- [ ] Token hash stored in `public.unisync_tokens` with kinds `{heartbeat}`
- [ ] `UNISYNC_GATEWAY_URL` set as repository variable in VIGIL
- [ ] Bastion feed enabled in VIGIL configuration
- [ ] First scan completed and heartbeat posted
- [ ] VIGIL appears on Bastion roster

## Revocation

To revoke VIGIL's access:
```sql
update public.unisync_tokens set revoked_at = now() where agent = 'vigil';
```

Also remove the `UNISYNC_VIGIL_TOKEN` secret from the VIGIL repository.

## Next: Escalation support

Future enhancement: VIGIL could post `escalate` events for critical findings that need
immediate attention. This would require:
1. Adding `escalate` to VIGIL's allowed kinds in the token
2. Modifying VIGIL to post escalations for critical/high severity findings
3. Deduplicating escalations to avoid flooding the ledger
