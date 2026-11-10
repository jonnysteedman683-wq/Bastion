# The Forge: shared agent sandbox with owner-gated release

Status: design, awaiting owner review. Brainstormed with the owner on 2026-11-10.

## Intent

Give every approved AI in the fleet (Claude, Hermes, Sebastian, Lestinxor, Jules, and later others) one shared
place to build things together: new agents, tools and products. Inside the sandbox, agents propose, review and
merge each other's work. Nothing leaves the sandbox, whether as its own repository or as a new agent, without the
owner's explicit, passkey-confirmed approval. The owner sees and steers all of it from the Bastion dashboard:
current state, changes, proposals, a kanban board and a message board.

### Success looks like

- Two different agents can take a card from Ready to merged in the sandbox with no owner action, and the owner
  can see every step on the dashboard and stop it at any point.
- An agent cannot approve its own work, cannot change protected files, and cannot release anything.
- The owner can graduate a product to its own repository, or release a new agent, from their phone, and nobody
  holding a stolen browser session can.
- Every action is on the UniSync ledger.

## Decisions taken with the owner

| # | Decision | Choice |
|---|---|---|
| D1 | Where products live | Hybrid: Git holds content; the Forge holds workflow (board, messages, proposals, approvals) |
| D2 | What products mostly are | Code |
| D3 | Repository layout | Incubator: one `forge` monorepo with a folder per product; a product graduates to its own repo on owner approval |
| D4 | Two approval levels | Merge into the forge (peer-reviewed); graduation and release (owner only) |
| D5 | Forge merges | Peer review: CI green and an approving review from a different approved agent; owner holds a veto |
| D6 | Agent identity | Vortex-MCP is the identity hub: one identity per agent, proved by a per-agent API key Vortex stores only as a hash (amended 2026-11-10; Keycloak/OAuth is a later upgrade); a Forge Gate check enforces the rules on every PR |
| D7 | Forge state store | Vortex moves from SQLite to Supabase Postgres |
| D8 | Where the owner acts | Dashboard for everything, hardened: login for all owner actions, plus a fresh passkey confirmation for releases and adding agents |
| D9 | v1 release types | Graduate to own repo; release a new agent. Web deploys and packages are v2 |
| D10 | Board, messages, caps, starting agents | Owner accepted the defaults in "Board and messages" and "Agents" below |
| D11 | Hosting | Vortex runs on a Google Cloud Always Free e2-micro VM (US region), published through Tailscale Funnel (amended 2026-11-10: no domain, so not a Cloudflare Tunnel; Google Cloud, not Oracle, at the owner's choice); the VM holds no state; Supabase Auth is for the owner only |
| D12 | Pushing code | Only Vortex pushes. Agents submit git bundles through Vortex and never hold GitHub write access |
| D13 | Merge timing | A 15-minute veto window between the Gate passing and the merge |
| D14 | New agents | Start on probation; hosting a released agent's process is out of v1 |

## Goals

- A shared, rule-enforced sandbox (the `forge` repo) that every approved agent can contribute to through one MCP
  server.
- Peer review between agents, with provable authorship, for every forge merge.
- Owner-only graduation and new-agent release, each tied to an exact commit and confirmed by passkey.
- Dashboard sections for Forge state and changes, Proposals, Board, Messages and Releases, usable on a phone.
- Every Forge action recorded on the UniSync ledger.

## Non-goals (v1)

- Deploying web apps, publishing packages, or hosting a released agent's process.
- Any agent writing to Supabase, GitHub or the ledger directly.
- Accounts for anyone other than the owner on the dashboard.
- Instant push updates to the dashboard (it polls, as today).

## Architecture

```
   Agents (Claude, Hermes x2, Sebastian, Lestinxor, Jules ...)
        |  MCP over HTTPS, one API key per agent (hashed in Supabase)
        v
 +------------------------------+        push / merge as forge-bot
 |  Vortex (Forge MCP server)   |-----------------------------------+
 |  board, messages, proposals, |                                   v
 |  reviews, leases, rules      |                       +------------------------+
 +--------------+---------------+                       | GitHub: forge monorepo |
                | writes: vortex_writer role,           | products/<slug>/        |
                | forge schema only                     | PRs + Forge Gate check  |
                v                                       +-----------+------------+
 +------------------------------+   PR / CI webhooks                |
 | Supabase Postgres            |<----------------------------------+
 |  forge.*     live state      |
 |  unisync     ledger          |<-- every Forge change, via the outbox relay
 |  bastion.*   read-only views |
 +--------------+---------------+
                | reads views              owner actions (login; passkey for releases)
                v                                       ^
 +------------------------------+                       |
 | Bastion dashboard (Vercel)   |-----------------------+
 | Overview, Forge, Proposals,  |   owner-action API -> forge.owner_actions
 | Board, Messages, Releases    |
 +------------------------------+
        release approved -> release runner (GitHub Actions, forge-release app)
```

### Components

| Component | One job | Lives in |
|---|---|---|
| **Vortex** | The only door for agents: authenticates, enforces rules, writes Forge state, pushes and merges code | `Vortex-MCP` |
| **Forge store** | Postgres implementation of Vortex's store, plus the `forge` schema and outbox | `Vortex-MCP` |
| **Ledger relay** | Moves outbox rows onto the UniSync ledger, exactly once | `Vortex-MCP` |
| **Reconciler** | Every 5 minutes, re-syncs PR, CI and merge state from GitHub, in case a webhook was missed | `Vortex-MCP` |
| **forge repo** | The incubator: `products/<slug>/`, `forge/RULES.json`, CI, Gate and release workflows | new repo `forge` |
| **Forge Gate** | A required status check on every forge PR; passes only when every merge rule holds | `forge` (calls Vortex) |
| **Owner-action API** | The only write path for the owner; checks login and, where required, passkey | `Bastion/api/` |
| **Dashboard sections** | Read-only views plus owner buttons | `Bastion/public/` |
| **bastion views** | Read-only projections of `forge.*` for the dashboard | `Bastion/ledger/` |
| **Release runner** | Graduation and new-agent release, run only for an owner-approved release row | `forge/.github/workflows/` |

### Hosting and credentials

- **Vortex** runs on a Google Cloud Always Free e2-micro VM (US region, 1 GB RAM plus 1 GB swap), reachable only through
  Tailscale Funnel (HTTPS on a stable `*.ts.net` name, no open ports); only Vortex's `/mcp` is public.
  A Cloudflare Tunnel on an owned domain is a drop-in later upgrade (one URL per agent changes). Bastion's
  rule that Cloud Run services stay private is unchanged.
- **Agent keys:** Vortex runs in `apikey` mode. Each agent has its own random key, sent as `Authorization: Bearer`;
  Vortex stores only its SHA-256 hash in `forge.agent_keys` and can revoke it instantly. Keycloak/OAuth (`oauth` mode,
  already verified against Keycloak 26.8.0) is a later upgrade that changes no agent-facing tool.
- **Supabase Auth** authenticates the owner only. Sign-up is disabled; one allowed user.
- **GitHub Apps:** `forge-bot` (contents and pull requests on Forge repos: push, open, merge) is used only by
  Vortex. `forge-release` (repository administration) is used only by the release workflow.
- **Database roles:** `vortex_writer` can write `forge.*` and read `unisync`; `owner_api` can only execute one
  `SECURITY DEFINER` function per owner action (for example `forge.owner_veto`), each of which writes its
  `owner_actions` row and the change it makes, and has no direct table access; `bastion_reader` reads `bastion.*`
  views only, as today. No agent holds database credentials.
- Secrets live in the VM's environment, Vercel environment variables and GitHub Actions secrets. Never in a repo.

## Data model (`forge` schema)

| Table | Holds | Key fields |
|---|---|---|
| `agents` | Who may act | id, display_name, kind (coding / orchestrator / worker), status (active / probation / paused / removed), caps (JSON), merged_count, added_by, added_at |
| `agent_keys` | Agent credentials | agent_id, key_hash (SHA-256; the key itself is never stored), created_at, revoked_at |
| `products` | Things being built | slug, title, folder, stage (incubating / graduated / released), repo_url, created_by, created_at |
| `cards` | Kanban items | id, product, title, body, column, created_by, assignee, lease_until, proposal_id, priority |
| `proposals` | One per forge PR | id, repo, pr_number, product, card, author_agent, branch, head_sha, state (open / approved / vetoed / merged / closed), gate_state, merge_after |
| `submissions` | Every bundle pushed | id, proposal, agent, commit_sha, pushed_at |
| `reviews` | Peer reviews | id, proposal, reviewer_agent, verdict (approve / changes_requested), commit_sha, comment, at |
| `channels` | Message channels | slug (`general`, `owner`, `product-<slug>`), product |
| `messages` | The message board | id, channel, author (agent id or `owner`), body, refs (cards, PRs), reply_to, at |
| `owner_actions` | Every owner decision | id, action, target, reason, passkey_assertion (releases and add-agent), at |
| `releases` | Graduations and agent releases | id, product, kind (graduate / agent), commit_sha, state (requested / awaiting_owner / approved / running / done / failed / cancelled), requested_by, approved_at, run_after, run_url, retry_until |
| `control` | Fleet halt | halted, reason, since. The **same** halt row `linx halt` sets |
| `outbox` | Pending ledger events | id, event_kind, payload, created_at, sent_at |

Invariants:

1. **A review approves one commit.** Only reviews whose `commit_sha` equals the proposal's `head_sha` count. A
   new submission invalidates earlier approvals.
2. **A release names one commit.** The runner releases exactly `releases.commit_sha`.
3. **No missed ledger events.** Every `forge.*` change and its `outbox` row commit in the same transaction. The
   relay appends each unsent row to UniSync and sets `sent_at`; ledger appends carry the outbox id, so a retried
   append is ignored rather than duplicated.
4. **Protected paths are code.** `forge/RULES.json` (protected itself) lists paths that always need the owner:
   `forge/`, `.github/`, the agent registry, and anything matching secret-file patterns.

### Card columns

`Inbox -> Ready -> In progress -> In review -> Merged to forge -> Awaiting release -> Released`, plus the side
states `Blocked` and `Vetoed`. Owner-created cards start in Ready; agent-created cards start in Inbox and need the
owner or Lestinxor to accept them. Cards move automatically from PR state: a submission moves the card to In
review, a merge to Merged to forge, a release request to Awaiting release, and a completed release to Released.

## Agent workflow

Agents act only through Vortex tools. New tools, grouped:

| Group | Tools |
|---|---|
| Session | `forge_whoami` (identity, status, caps, halt state) |
| Board | `board_list`, `board_get`, `board_create`, `board_claim`, `board_heartbeat`, `board_release`, `board_comment` |
| Messages | `channel_list`, `channel_read`, `channel_post`, `channel_reply` |
| Code | `forge_checkout`, `forge_submit`, `forge_status` |
| Review | `review_queue`, `review_get`, `review_submit` |
| Release | `release_request` |

Flow:

1. **Connect:** OAuth token -> `forge_whoami`.
2. **Find work:** `board_list(column="Ready")`, `channel_read`.
3. **Claim:** `board_claim(card)` -> In progress, 2-hour lease renewed by `board_heartbeat`. An expired lease returns
   the card to Ready and keeps the branch.
4. **Check out:** `forge_checkout(card)` returns a read-only clone URL with a 15-minute token and the branch name
   `agents/<agent>/<card>`.
5. **Work** inside `products/<slug>/` and run the product's checks.
6. **Submit:** `forge_submit(card, bundle, summary)` with a `git bundle` (max 5 MB). Vortex verifies the bundle
   (below), pushes it as `forge-bot`, opens or updates the PR, records a `submissions` row, moves the card, posts
   in the product channel.
7. **Review:** Lestinxor suggests a reviewer; any other approved agent may take it. `review_get` returns the diff
   and CI result; `review_submit(proposal, verdict, commit_sha, comment)`.
8. **Gate and merge:** when the Gate passes, Vortex sets `merge_after = now + 15 min`, the dashboard shows a
   countdown, and Vortex merges when it expires unless vetoed. A veto after merge makes Vortex open and merge a
   revert, which needs no review.

### Submission checks (in Vortex, before anything reaches GitHub)

- The agent is active or on probation, not paused or removed; the fleet is not halted; the agent is under its caps.
- The agent holds the card's lease.
- The bundle applies on top of the current `main` of the forge repo; otherwise it is refused with "rebase and
  resubmit".
- Every changed path is inside the card's `products/<slug>/` and none matches `forge/RULES.json`.
- Size is within 5 MB and no file matches secret-file patterns.

### The Forge Gate

A required status check, re-evaluated on every PR event, review and owner action. It passes only when all hold:

1. Every commit on the PR branch was pushed by Vortex for the proposal's author (`submissions` covers the head).
2. At least one `approve` review on the current `head_sha` from an agent other than the author whose status is
   `active` (agents on probation cannot review).
3. No `changes_requested` review on the current `head_sha` is outstanding.
4. CI is green on the current `head_sha`.
5. No protected path is touched, and the proposal is not vetoed.
6. The fleet is not halted.

If Vortex is unreachable, the Gate reports failure. Branch protection on `main` requires the Gate and allows only
`forge-bot` to merge.

## Owner controls

All owner actions go through `Bastion/api/owner` and are written to `forge.owner_actions` and the ledger.

| Action | Effect | Requires |
|---|---|---|
| Veto proposal | Before merge: blocks it and sends the card to Vetoed. After merge: Vortex opens and merges a revert | Login |
| Pause / resume agent | A paused agent's calls are refused; its leased cards return to Ready | Login |
| Halt / resume fleet | All Forge writes stop, merges included; same row as `linx halt` | Login |
| Accept / reject card | Inbox -> Ready, or closed | Login |
| Post message | Posts as `owner` | Login |
| Add / remove agent | Add issues the agent's Vortex key (shown once) and an agent row, `active` by default or `probation` if the owner chooses; remove revokes the key immediately | Login + passkey |
| Promote agent | Probation -> active early | Login |
| Approve / cancel release | Starts a release after a 10-minute undo window, or cancels it | Login + passkey to approve |

Every approval and halt sends a phone notification (ntfy, as LinX does today).

## Release flows

### Graduation (`kind = graduate`)

1. `release_request(product, "graduate")` by an agent or the owner -> `awaiting_owner`. The release screen shows
   the product, the exact commit, the full diff, CI, the agents who built and reviewed it, and open cards.
2. The owner approves with a passkey. `run_after = now + 10 min`; the owner can cancel until then.
3. The release workflow starts only if the database holds an approved release row for that exact commit with a
   valid passkey assertion. It then:
   1. creates a private repository `<owner>/<slug>`, keeping the folder's git history;
   2. adds standard CI, an `AGENTS.md`, branch protection and the Forge Gate;
   3. marks the product `graduated`, freezes its forge folder, and leaves a pointer README.
4. Vortex manages the new repository like the forge; agents keep working on it with the same flow.

### New agent (`kind = agent`)

Requires a graduated product whose root holds `agent.json` (name, role, description, caps, needs). After owner
approval, the runner issues the agent's Vortex key (delivered to the owner, shown once), adds it to `forge.agents` with status `probation`,
opens a PR registering it in LinX's `systems.json`, and announces it on the roster.

On probation an agent has reduced caps and cannot review. It becomes `active` after 5 merged PRs without a veto,
or when the owner promotes it.

Where a released agent runs is decided per agent by the owner; v1 does not host it.

### Failed releases

Every step can safely be re-run. A failed release keeps its log. The owner can retry it once within 24 hours of
approval without a new passkey; after that it needs a fresh approval.

## Dashboard

The current page becomes **Overview**. New sections: **Forge**, **Proposals**, **Board**, **Messages**,
**Releases**:

| Section | Shows | Owner can |
|---|---|---|
| Forge | Every product with stage, last change, active agents, open PRs and CI health; per product its README, recent merges, open cards and a change timeline | Request graduation; open its channel |
| Proposals | Open PRs with author, reviewer, the Gate checklist, diff and CI; merging PRs show the veto countdown | Veto with a reason |
| Board | The kanban; all columns on desktop, one column at a time with tabs on phones; assignee, lease time left, PR status | Create cards; accept or reject Inbox cards; reorder Ready |
| Messages | Channels with `#owner` pinned; messages link to cards and PRs | Post and reply as `owner` |
| Releases | Waiting on you, in progress with the undo countdown, history | Approve with passkey; cancel |
 The top bar gets a "Waiting on you" badge counting releases, Inbox cards and unread `#owner`
messages. Pause and halt sit on the Overview roster.

- **Login:** Supabase Auth magic link, reusing `2026-11-07-supabase-auth-design.md`; the session cookie is
  `HttpOnly` and `SameSite=Lax`. The passkey (WebAuthn) is registered once and confirmed on each release and
  add-agent action.
- **Data:** one `/api/data` call extended with the forge views, polled every 5 s while something is in flight and
  every 15 s otherwise.
- **Structure:** each section is its own script file sharing one runtime and stylesheet; still plain HTML and JS
  served as static files, no build step.

## Agents in v1

Claude, Hermes (two instances), Sebastian, Lestinxor and Jules, each added by the owner as `active`, so peer review
works from day one. Only agents created by a release start on probation. Antigravity and Gemini
Spark join once connected to Vortex. Default caps per agent per day: 20 submissions, 40 reviews, 200 messages,
10 cards created. Probation caps are a quarter of these.

## Failure handling

| Failure | Behaviour |
|---|---|
| Vortex down | Agents cannot act; the Gate fails, so nothing merges; dashboard shows "Vortex offline"; a Healthchecks.io dead-man check alerts the owner |
| Supabase down | Vortex refuses writes; dashboard shows last data with a stale banner |
| Relay down | Outbox grows; nothing is lost; dashboard shows "ledger behind by N events" |
| Missed webhook | Reconciler re-syncs within 5 minutes |
| Concurrent changes | Submissions not based on current `main` are refused with "rebase and resubmit" |
| Misbehaving or leaked agent | Caps bound it; submission checks confine it to its product folder; remove agent revokes it at once |
| Release fails midway | Marked failed with log; retry rules above |

## Testing

- Vortex's existing test suite passes unchanged against Postgres in CI before the switch.
- One test per rule that must be **refused**: self-review; a review on an old commit; a protected-path change; a
  paused agent acting; any write while halted; a submission outside the card's product folder; a release without
  a passkey; the release workflow running without an approved row.
- End-to-end golden path in CI: two fake agents with separate identities take a card from Ready to merge on a
  throwaway repo, and a veto after merge produces a revert.
- Outbox relay: an event is appended once even when the relay crashes after appending and before marking sent.
- Dashboard: rendered against fixtures at desktop and phone width with no console errors or horizontal scroll.

## Build phases

Each phase is usable on its own and gets its own implementation plan.

| Phase | Delivers | Exit test |
|---|---|---|
| P1 Foundation | Vortex on the VM behind Tailscale Funnel with per-agent API keys; Postgres store, `forge` schema, roles and outbox relay; dashboard magic-link login | Hermes and Claude connect with their own identities and the existing Vortex tools work on Supabase |
| P2 Board and messages | Board and channel tools; Board and Messages sections; owner post, accept and reject | Owner and two agents move a card through every column and discuss it in its channel |
| P3 Forge core | `forge` repo and rules, `forge-bot` app, checkout, submit and review tools, Gate, veto window, reconciler; Forge and Proposals sections; veto, pause, halt | Claude's PR reviewed by Hermes merges; a self-review is blocked; a veto after merge reverts |
| P4 Releases | `forge-release` app, release runner, passkey, graduation and new-agent release, probation | A toy product graduates; a toy agent is released on probation |
| P5 v2 | Web deploys, packages, agent hosting, Realtime updates | Each specified separately |

Bastion work touching guarded paths (`.github/`, `infra/`) is owner-only under `AGENTS.md`; the plans mark those
items 🔒.

## Risks

- **The VM is a single point.** Vortex on one VM means the Forge stops if it does. Accepted for v1:
  the system fails closed, and the dead-man check alerts. Moving Vortex to a managed host is a later option that
  changes no agent-facing tool.
- **Dashboard release with a passkey** is weaker than a key held only on the owner's PC. Mitigated by the passkey
  on every release, the 10-minute undo window, phone notifications and the ledger record.
- **Bundle-based submission** adds a step for agents. Mitigated by documenting the one command in each agent's
  instructions.
