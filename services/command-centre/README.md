# command-centre

Bastion's private dashboard: roster, live ledger feed, headline numbers. Backlog item S1.

- `app.py`: standard-library server. Serves `web/dist`, plus `/health`, `/api/feed` and `/api/roster`.
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
