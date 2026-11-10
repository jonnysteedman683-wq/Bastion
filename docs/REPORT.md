# Report: connecting an agent on your machine to Bastion

`python -m tools.report` lets any agent that runs on a PC or server tell the command centre what it is
doing. It is standard library only and works for agents you wrote and for open-source ones you cloned.

Agents write, dashboards read: this gives Bastion visibility of an agent, not control over it.

## Connect an agent

1. **Name it** in `services/command-centre/sources.json`:
   ```json
   {"name": "Hermes", "type": "push"}
   ```
2. **Give it a key** on the command centre, under the same name, in the `INGEST_AGENT_KEYS` secret (see the
   [command-centre README](../services/command-centre/README.md#agent-ingest)). One key per agent; never in the repo.
3. **Set two environment variables** where the agent runs:
   - `BASTION_URL`: the command centre. Defaults to `http://localhost:8080`. Anything that is not this
     machine must be `https`, so the key is never sent in the clear.
   - `BASTION_INGEST_KEY`: that agent's key. Bastion works out the agent's name from it.
4. **Report**, in whichever way fits the agent.

## Two ways to report

Wrap the agent's command. Bastion gets `run.started`, then `run.finished` or `run.failed` with the exit code
and how long it took:

```bash
python -m tools.report run --label "nightly research" -- <the agent's command>
```

The wrapper exits with the agent's own exit code. If Bastion is down or the key is missing, the agent still
runs and the problem is a warning on stderr. The summary names the program (or your `--label`), never its
arguments, because arguments can hold prompts or keys.

Or post single events from a hook, a cron job or a script:

```bash
python -m tools.report send --kind memory.write --summary "saved 3 notes"
```

`send` exits 0 when the event is stored, 1 if Bastion refused it or is unreachable, 2 if it is not configured.

## Check it

Run the agent once. It should show as **active** on the roster and its events should be in the feed within
a few seconds.
