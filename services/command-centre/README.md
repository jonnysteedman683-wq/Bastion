# command-centre

Bastion's private dashboard: roster, live ledger feed, headline numbers. Backlog item S1.

- `app.py`: standard-library server. Serves `web/dist`, plus `/health`, `/api/feed` and `/api/roster`.
- `web/`: React + Tailwind + shadcn/ui. The browser only talks to `app.py`, never to Supabase.
- `roster.json`: the agents shown on the roster.
- `ledger/schema.sql`: the UniSync ledger table (append-only, hash-chained) and the read-only `bastion_reader` role.

## Data source

With no ledger configured it runs on **demo data** (labelled in the UI), generated from the clock so the feed moves.
To use the real ledger, set `LEDGER_URL` (the Supabase project URL) and the `LEDGER_READ_KEY` secret
(a key for `bastion_reader`; Secret Manager, never in the repo).

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
