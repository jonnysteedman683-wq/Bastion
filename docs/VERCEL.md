# Command centre on Vercel

The command centre runs on Vercel while Google Cloud cannot start containers. Nothing is built: Vercel
serves `public/index.html` as is and runs one Python function, `api/data.py`.

| Piece | What it does |
| --- | --- |
| `public/index.html` | The dashboard: live progress, systems map, summon buttons, tasks with output, auto-refresh |
| `api/data.py` | `GET /api/data`: five fixed, read-only queries on the `bastion` views, as `bastion_reader` |
| `requirements.txt` | pg8000 and its dependencies, pinned with hashes, for the function |
| `vercel.json` | No install or build step, `public/` as the output, strict security headers |

## Privacy

On Vercel's free plan the production address is public, so the data is behind a passphrase: the
function answers only `Authorization: Bearer <BASTION_DASH_TOKEN>`, compares it in constant time, and
refuses everything when the variable is unset. The page itself holds no data. The passphrase is kept in
the browser's local storage; **Sign out** removes it.

## Setup (owner, once)

In Vercel → the `command-centre` project → Settings:

1. **Build and Deployment**: Framework Preset *Other*, Root Directory *empty* (the repository root).
   `vercel.json` sets the rest.
2. **Environment Variables** (Production and Preview):
   - `LEDGER_DSN`: the same value as the `LEDGER_DSN` secret in Google Secret Manager
     (`postgresql://bastion_reader.<ref>:<password>@aws-0-ap-southeast-2.pooler.supabase.com:5432/postgres`)
   - `BASTION_DASH_TOKEN`: a long passphrase you make up; you type it once per browser
3. Redeploy (Deployments → ⋯ → Redeploy), open the production URL and enter the passphrase.
