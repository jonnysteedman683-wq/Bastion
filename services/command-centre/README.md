# command-centre

Bastion's private dashboard: roster, live ledger feed, headline numbers. Backlog item S1.

- `app.py`: standard-library server. Serves `web/dist`, plus `/health`, `/api/feed`, `/api/roster`, `/api/operations`
  and `/api/approvals`.
- `web/`: React + Tailwind + shadcn/ui. The browser only talks to `app.py`, never to Supabase.
- `roster.json`: the agents shown on the roster.
- `ledger/bastion_reader.sql`: read-only `bastion.feed` and `bastion.roster` views over the existing UniSync tables
  (Supabase project UNISYNCARC), and the `bastion_reader` role that can read only those.
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
