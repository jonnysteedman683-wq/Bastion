# Simple-Agent: Connect to Bastion in 60 Seconds

This is the **fastest way** to test that Bastion is working. No database, no ledger, no configuration.
Just two environment variables and one command.

## Instant Setup (30 seconds)

### Step 1: Generate a key
```bash
python -c "import secrets; print(secrets.token_urlsafe(32))"
```
Copy the output. This is your agent's secret key.

### Step 2: Add key to command centre
Add the key to your command centre's `INGEST_AGENT_KEYS` environment variable under the name **Simple-Agent**:

```json
{"Simple-Agent": "your-generated-key-here"}
```

If you're running the command centre locally with `python -m tools.local`, put this in `.env.local`:
```
INGEST_AGENT_KEYS={"Simple-Agent":"your-generated-key-here"}
```

### Step 3: Set environment variables
```bash
export BASTION_URL=http://localhost:8080  # or your Bastion URL
export BASTION_INGEST_KEY=your-generated-key-here
```

### Step 4: Ping Bastion
```bash
python -m tools.simple_agent ping
```

**That's it!** You should see:
```
Simple-Agent: heartbeat sent to Bastion
```

And within seconds, "Simple-Agent: ping" will appear in Bastion's feed.

## Try It

### Send a heartbeat
```bash
python -m tools.simple_agent ping
```

### Run a command and report it
```bash
python -m tools.simple_agent run echo "Hello from Simple-Agent"
```

### Loop forever (Ctrl+C to stop)
```bash
python -m tools.simple_agent loop --interval 30
```
This pings Bastion every 30 seconds. You'll see the count increment in the feed.

## What You'll See in Bastion

- **Roster**: Simple-Agent appears with status "active" (after first ping)
- **Feed**: Events like "Simple-Agent: ping" or "Simple-Agent: heartbeat #1"
- **Last seen**: Updates with each ping

## How It Works

Simple-Agent uses Bastion's `tools.report` module, which posts events to the command centre's
`/api/ingest` endpoint. The command centre validates the key, extracts the agent name from it,
and stores the event.

No ledger, no database, no GitHub workflows. Just HTTP POST requests.

## Clean Up

To remove Simple-Agent:
1. Remove it from `INGEST_AGENT_KEYS`
2. Restart your command centre
3. It will disappear from the roster after the cache clears

## Next: Make Your Own Agent

Copy `tools/simple_agent/__init__.py` as a template. The pattern is:

```python
from tools.report import main as report_main

# Post an event
report_main(["send", "--kind", "my.event", "--summary", "Something happened"])
```

That's all you need to connect any agent to Bastion.
