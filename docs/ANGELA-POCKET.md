# Angela-Pocket: Angela's phone satellite

Angela-Pocket is the Android companion for Angela. It forwards phone notifications to the
Angela core running on your laptop and provides a chat interface, while Angela stays the brain
on the laptop. Angela-Pocket does not connect directly to Bastion; it communicates with Angela
core, which then reports to Bastion through its own connection.

## Architecture

```
Phone (Android)   Tailscale   Laptop (Angela core)    Bastion
       |                                      |
        ntfy push (optional)       |
                                                  (for alerts)     |
                                                      reports via tools.report
```

## Connecting Angela-Pocket to Bastion

Angela-Pocket connects **through** Angela core, not directly. When you:
1. Chat with Angela from your phone
2. Receive notifications forwarded to Angela
3. Approve actions from your phone

These interactions go through Angela core on your laptop, which then reports them to Bastion
using the report tool or ledger connection configured for Angela.

### Setup

1. **Prerequisite**: Angela core must be [connected to Bastion](ANGELA.md) first

2. **Angela-Pocket configuration**:
   - Set `ANGELA_URL` to your laptop's Tailscale IP (e.g., `http://100.x.y.z:8765`)
   - Set `ANGELA_TOKEN` to the same token used by Angela core
   - Set `ANGELA_DEVICE` to identify this phone (e.g., `pixel`)

3. **Optional: ntfy for alerts**:
   - Install ntfy app on your phone
   - Subscribe to a topic
   - Set `ANGELA_NTFY_TOPIC` in Angela core's environment
   - Angela will push alerts to your phone via ntfy

### What gets reported to Bastion

When you use Angela-Pocket, the following events flow through Angela core to Bastion:

| Phone Action | Angela Core Event | Bastion Event |
| --- | --- | --- |
| Send chat message | Process message | `run.started` / `run.finished` |
| Forward notification | Process notification | `memory.write` (if saved) |
| Approve action | Execute approved action | `task.completed` |
| Receive ntfy alert | Heartbeat check | `heartbeat` (if configured) |

### Example: Phone-to-Bastion flow

```bash
# On phone (Termux):
angela-phone chat "What's my calendar today?"

# This sends to Angela core at ANGELA_URL
# Angela core processes the request
# Angela core reports to Bastion:
python -m tools.report send --kind task.completed --summary "Answered calendar question"
```

## Report-based connection for phone events

If you want Angela-Pocket to report events directly (separate from Angela core):

1. **Add to sources.json** (already done in Bastion's `services/command-centre/sources.json`)

2. **Generate a separate key** for Angela-Pocket:
   ```bash
   python -c "import secrets; print(secrets.token_urlsafe(32))"
   ```
   Add it to `INGEST_AGENT_KEYS` as `Angela-Pocket`

3. **Set environment on phone**:
   ```bash
   export BASTION_URL=https://bastion.example.com
   export BASTION_INGEST_KEY=<Angela-Pocket's key>
   ```

4. **Report from phone scripts**:
   ```bash
   # In Termux, after an action
   python -m tools.report send --kind notification.received --summary "Forwarded 3 notifications"
   ```

## Safety

- **No direct internet access**: Use Tailscale to keep all traffic on your tailnet
- **Token security**: The `ANGELA_TOKEN` is only valid for the Angela core API, not for Bastion
- **Minimal reporting**: Only event metadata (not message content) should be reported to Bastion
- **Read-only by default**: Angela-Pocket cannot trigger write actions without explicit approval

## One-time setup checklist

- [ ] Angela core is connected to Bastion
- [ ] Angela-Pocket is configured with `ANGELA_URL` and `ANGELA_TOKEN`
- [ ] Tailscale is running on both phone and laptop
- [ ] Optional: ntfy configured for alerts
- [ ] Optional: separate Bastion reporting for phone events
- [ ] First phone-to-Angela-to-Bastion flow tested

## Revocation

To revoke Angela-Pocket's access:
1. Change `ANGELA_TOKEN` on Angela core
2. Remove Angela-Pocket's key from `INGEST_AGENT_KEYS` (if using separate reporting)
3. Update the token on your phone
