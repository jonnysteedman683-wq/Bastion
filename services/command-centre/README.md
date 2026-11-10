# command-centre

Bastion's private dashboard: roster, live ledger feed, headline numbers. Backlog item S1.

- `app.py`: standard-library server. Serves `web/dist`, plus `/health`, `/api/feed`, `/api/roster`, `/api/operations`,
  `/api/approvals` and `/api/spend`.
- `caps.json`: daily spend caps in USD (fleet, per-agent default, per-agent overrides). Display only until G1d.
- `web/`: React + Tailwind + shadcn/ui. The browser only talks to `app.py`, never to Supabase.
- `roster.json`: the agents shown on the roster.
- `sources.json`: other AI systems connected to Bastion (see [Sources](#sources)).
- `ledger/bastion_reader.sql`: read-only `bastion.feed` and `bastion.roster` views over the existing UniSync tables
  (Supabase project UNISYNCARC), and the `bastion_reader` role that can read only those.
- `ledger/bastion_spend.sql`: `bastion.spend_daily`, token and dollar usage per agent per UTC day.
- `ledger/bastion_tasks_control.sql`: `bastion.tasks` (each task's status, by Lestinxor's rules) and `bastion.control`
  (LinX's kill switch), both read-only.

## Data source

With no ledger configured it runs on **demo data** (labelled in the UI), generated from the clock so the feed moves.
In production it reads the UniSync ledger as `bastion_reader` through the `LEDGER_DSN` secret (Secret Manager,
never in the repo): a `postgresql://` URL for Supabase's session pooler. The role is read-only, times out after 5 s,
and sees one-line summaries only, never event bodies or tokens.

## Approvals

The Approvals view lists every task awaiting the owner, oldest first, from `bastion.dashboard_tasks`. It is read-only:
decisions are signed by the owner key, which lives only on Jonny's PC, so each item offers a copyable
`linx approve <task>` / `linx reject <task> --reason "<why>"` to run there. Task ids are written by agents, so a
command is offered only for plain ids (`[A-Za-z0-9][A-Za-z0-9._:-]{0,79}`); anything else must be decided from
`linx pending`. One-click decisions are backlog item O2.

## Spend

Agents report usage by adding a `usage` object to any ledger event, normally `task.result` or `task.failed`:

```json
"usage": {"model": "claude-...", "input_tokens": 1234, "output_tokens": 567, "cost_micro_usd": 12300}
```

Every field is optional. Cost is in integer micro-dollars, because the UniSync gateway rejects non-integer numbers
anywhere in an envelope. A summoned agent writes a usage file (with `cost_usd` as a plain number), and
`python -m tools.summon finish ... --usage FILE` cleans it up, converts the cost and attaches it to the outcome. A bad
file is a warning; the outcome still posts. A `cost_usd` number written straight to Supabase is still counted. `bastion.spend_daily` (`ledger/bastion_spend.sql`, apply after `bastion_reader.sql`) sums them
per agent per UTC day. Agents write these bodies, so the view counts a field only when it is a JSON number, treats
negatives as 0 and clamps outliers. The Spend view shows today, 7 and 30 days, a 30-day chart and each agent's spend
today against `caps.json`. Until an agent reports usage the view says so instead of showing zeros as fact.

## Kill switch

The Operations view shows LinX's kill switch from `bastion.control` (the owner's last `linx/control/halt` write):
running or halted, since when and why. A halt also puts a red banner on every view, within one 5-second poll.
Like approvals, flipping it needs the owner key, so the card offers a copyable `linx halt --reason "<why>"` or
`linx resume --reason "<why>"` to run on Jonny's PC.

## Agent ingest

Private agents can report lifecycle events to `POST /api/ingest`. Configure a distinct secret for each allowed agent
in the service environment; never place this value in the repository:

```text
INGEST_AGENT_KEYS={"researcher-01":"replace-with-a-secret","writer-02":"replace-with-a-different-secret"}
```

An agent sends its own key as a Bearer token. Bastion derives the agent name from that key, so an `agent` field in
the request body is ignored. The body accepts `source`, `kind`, `summary`, and an optional UTC `ts`; `summary` is
required. For example, substitute the appropriate agent-specific secret at deployment time:

```bash
curl --request POST http://localhost:8080/api/ingest \
  --header 'Authorization: Bearer <agent-specific-secret>' \
  --header 'Content-Type: application/json' \
  --data '{"source":"local-host","kind":"task.completed","summary":"Indexed 24 documents"}'
```

Removing an agent from `INGEST_AGENT_KEYS` revokes its access; replacing only its value rotates its key. Keep the
command centre private: agents receive the ingest endpoint and their own secret, never database credentials.

## Sources

`sources.json` is the registry of other AI systems connected to Bastion. It is read once at startup (set
`SOURCES_CONFIG` to read a different file), and every source in it joins the roster after the ledger's agents:

```json
[
  {"name": "Den Ollama", "type": "ollama", "endpoint": "http://192.168.1.20:11434", "poll_interval": 60},
  {"name": "LM Studio", "type": "openai", "endpoint": "https://lm.example/v1", "auth": {"env": "LM_STUDIO_KEY"}},
  {"name": "Hermes", "type": "push"}
]
```

| Field | Meaning |
| --- | --- |
| `name` | Shown on the roster. Unique; a name already on the ledger roster is not listed twice. |
| `type` | `ollama` or `openai` (a model server Bastion polls, backlog H3), or `push` (the system reports in). |
| `endpoint` | `http(s)` URL of the server. Required unless `push`. No credentials, query or fragment in the URL. |
| `auth` | Optional `{"env": "NAME"}`: the *name* of the environment variable holding the key. Never the key. |
| `poll_interval` | Seconds between polls, 10 to 86400, default 60. |

A `push` source shows as active once it posts to `/api/ingest` with a key registered under the same name in
`INGEST_AGENT_KEYS`. A bad entry is skipped with a warning in the log and the rest still load; a warning never
repeats what was written under `auth`. The roster shows a source's host, never its auth.

## Sign-in

The command centre requires an existing Supabase Auth user. Configure `SUPABASE_URL` and
`SUPABASE_PUBLISHABLE_KEY` as service secrets; these identify the Auth project but are never
sent to the browser. Before deployment, add `https://<command-centre-host>/auth/callback` to
Supabase Auth's allowed redirect URLs and update the Magic Link template to link to
`/auth/callback?token_hash={{ .TokenHash }}&type=email`. The service requests links with user
creation disabled, so create authorised users in Supabase first.

## Run locally

```bash
cd services/command-centre/web && npm ci && npm run build && cd .. && python app.py
```

Then open http://localhost:8080. For live reload: `python app.py` in one terminal and `npm run dev` in `web/`.

## Checks

```bash
python -m unittest discover -s services/command-centre
cd services/command-centre/web && npm test && npm run build
```
