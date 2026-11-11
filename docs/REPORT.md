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

## Hermes

Hermes reports through its own [shell hooks](https://hermes-agent.nousresearch.com/docs/user-guide/features/hooks):
`python -m tools.report hermes` reads the hook's JSON from stdin and posts one line per event.

| Hermes hook | Bastion shows |
| --- | --- |
| `on_session_start` | `session.started`: "Session started on cli with <model>" |
| `on_session_end` (fires after every turn) | `turn.finished`: "Turn completed / interrupted / failed on cli" |
| `on_session_finalize` | `session.ended`: "Session ended on cli" |

Only the event name, platform, model and outcome are sent, with the source label `hermes`. Messages,
history, tool input, the session id, the working folder and the machine's name never leave the machine. The hook always exits 0 and prints nothing, so it cannot block
or change a Hermes session, whatever happens to Bastion.

Set it up on the machine that runs Hermes:

1. Put a small script in `<HERMES_HOME>/agent-hooks/` that adds this repository to `sys.path` and calls
   `tools.report.main(["hermes"], env=...)` with `BASTION_URL` and `BASTION_INGEST_KEY`. Keep the key in a
   file beside the script, not in this repository.
2. Point the three hooks above at that script in the `hooks:` block of Hermes's `config.yaml`, with a
   `timeout` of 10.
3. Start Hermes once and approve the hooks when it asks. `hermes hooks list` shows whether they are
   approved; `hermes hooks test on_session_start` fires one without starting a session.

## Claude Code

Claude Code reports through its own [hooks](https://docs.claude.com/en/docs/claude-code/hooks):
`python -m tools.report claude` reads the hook's JSON from stdin and posts one line per event.

| Claude Code hook | Bastion shows |
| --- | --- |
| `SessionStart` | `session.started`: "Session started (startup / resume / clear / compact)" |
| `Stop` (fires after every turn) | `turn.finished`: "Turn finished" |
| `SessionEnd` | `session.ended`: "Session ended (<reason>)" |
| `SubagentStop` | `subagent.finished`: "Subagent finished" |
| any other hook | `claude.<event>`: "Claude Code event <event>" |

Only the event name and why the session started or ended are sent, with the source label `claude-code`. The
prompt, the transcript path, the working folder, the session id and the machine's name never leave the
machine. The hook always exits 0 and prints nothing to stdout.

Set it up on the machine that runs Claude Code:

1. Put a small script outside this repository (for example `~/.bastion/claude-hook.py`) that adds the
   repository to `sys.path` and calls `tools.report.main(["claude"], env=...)` with `BASTION_URL` and
   `BASTION_INGEST_KEY`, sending the reporter's warnings to a log file beside it (not to the session), so
   you can see when Bastion was down or refused the key. Keep the key in a file beside it too.
2. In `~/.claude/settings.json`, add a `command` hook for `SessionStart`, `Stop` and `SessionEnd` that runs
   that script with a `timeout` of 10 (`"async": true` on the first two keeps turns from waiting on it).
3. Register the key under the name `Claude` in the command centre's `INGEST_AGENT_KEYS`.

## A command centre on your own machine

Agents on a PC can report to a command centre running on the same PC:

```bash
python -m tools.local
```

It serves `http://localhost:8080` to this machine only and prints a one-time sign-in link (a fresh secret on
every start), so no Supabase setup is needed to look at it. With `SUPABASE_URL` and
`SUPABASE_PUBLISHABLE_KEY` set it uses the normal magic-link sign-in instead.

Put the agents' keys in `.env.local` in the repository root (git-ignored), never in a tracked file:

```text
INGEST_AGENT_KEYS={"Hermes":"<Hermes's key>"}
```

Reported events are kept in `~/.bastion/ingest.db`. Build the web app once first
(`cd services/command-centre/web && npm ci && npm run build`). This is for the owner's own machine: it is
not a way to deploy, and it never listens on anything but `127.0.0.1`.

## Check it

Run the agent once. It should show as **active** on the roster and its events should be in the feed within
a few seconds.
