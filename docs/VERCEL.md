# Command centre on Vercel

The command centre runs on Vercel while Google Cloud cannot start containers. Nothing is built: Vercel
serves `public/index.html` as is and runs one Python function, `api/data.py`.

| Piece | What it does |
| --- | --- |
| `public/index.html` | The dashboard: live progress, systems map, summon buttons, tasks with output, auto-refresh |
| `api/data.py` | `GET /api/data`: five fixed, read-only queries on the `bastion` views, as `bastion_reader` |
| `requirements.txt` | pg8000 and its dependencies, pinned with hashes, for the function |
| `vercel.json` | Copies the static page to generated `dist/`, configures the function, and sets strict security headers |

## Access

The dashboard and its bounded, read-only datasets are public. The API uses only the private `bastion`
views through the `bastion_reader` database role; it does not expose raw ledger tables or accept writes.

## Setup (owner, once)

In Vercel → the `command-centre` project → Settings:

1. **Build and Deployment**: Framework Preset *Other*, Root Directory *empty* (the repository root).
   `vercel.json` sets the rest.
2. **Environment Variables** (Production and Preview):
   - `LEDGER_DSN`: the same value as the `LEDGER_DSN` secret in Google Secret Manager
     (`postgresql://bastion_reader.<ref>:<password>@aws-0-ap-southeast-2.pooler.supabase.com:5432/postgres`)
3. Redeploy (Deployments → ⋯ → Redeploy), then open the production URL.
