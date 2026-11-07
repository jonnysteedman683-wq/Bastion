# Bastion Operations Overview Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make Bastion's default dashboard a read-only operations overview of agent completions, goal progress, PR state, and bottlenecks.

**Architecture:** Extend the command-centre's existing server-side ledger adapter with a bounded operations snapshot backed by read-only `bastion` views. The React app polls that snapshot and renders attention, task progress, PR stewardship, and recent completions, while retaining roster and ledger views.

**Tech Stack:** Python 3 standard-library HTTP server; pg8000 in deployed image; React 19, TypeScript, Tailwind, shadcn components, Vitest.

**Spec:** `docs/superpowers/specs/2026-11-07-operations-overview-design.md`

## Global Constraints

- Keep this release read-only: no summon, approval, pause, merge, or deployment actions.
- The browser communicates only with `app.py`; it never accesses Supabase or GitHub directly.
- Use only the `bastion_reader` role and bounded read-only `bastion` schema views.
- Never expose raw event bodies, task output, database credentials, or tokens.
- Allow only HTTPS GitHub URLs in rendered links.
- Treat a missing PR-steward report as unknown and a report older than 26 hours as stale.

## Review Focus

- Missing PR-steward heartbeat renders an explicit unknown state, not zero open PRs — Task 2 server test and Task 4 component test.
- Task output containing secrets cannot enter the operations JSON response — Task 2 server test.
- A `rejected` task remains a lifecycle bucket and is not silently dropped from progress totals — Task 2 server test.
- A stale steward heartbeat appears as an attention item even when it has no escalated PRs — Task 2 server test and Task 4 component test.
- A ledger-supplied non-GitHub/non-HTTPS URL is not emitted as a clickable link — Task 3 frontend test.

---

### Task 1: Add safe read-only operations projections

**Files:**
- Modify: `services/command-centre/ledger/bastion_reader.sql`
- Modify: `services/command-centre/ledger/bastion_tasks_control.sql`
- Test: `services/command-centre/test_app.py`

**Interfaces:**
- Produces: `bastion.dashboard_tasks`, containing task lifecycle fields but never `output`.
- Produces: `bastion.steward_report`, containing the newest steward heartbeat's timestamp and bounded, sanitised report fields.
- Consumes: `public.unisync_events`, only behind the private `bastion` schema.

- [ ] **Step 1: Write failing contract tests for safe operations rows**

Add tests that exercise command-centre conversion code with task and steward rows and assert output/log fields are absent, `rejected` survives, and the latest heartbeat wins.

- [ ] **Step 2: Run the command-centre test suite to verify the new contract is not yet implemented**

Run: `python -m unittest discover -s services/command-centre`

Expected: FAIL because the operations conversion/query interface does not exist.

- [ ] **Step 3: Create the two private views**

`bastion.dashboard_tasks` selects only `task_id`, `title`, `requested_by`, `agent`, `status`, `mode`, `run_url`, `requested_ts`, `claimed_ts`, `finished_ts`, `updated_ts`, and `detail` from `bastion.tasks`. `bastion.steward_report` projects the newest `steward` `heartbeat` and only its structured `summary`, `counts`, `floods`, bounded PR `items`, `truncated`, and `run_url` fields. Revoke public roles and grant `SELECT` only to `bastion_reader`.

- [ ] **Step 4: Run the command-centre test suite**

Run: `python -m unittest discover -s services/command-centre`

Expected: PASS.

- [ ] **Step 5: Commit the projection contract**

```bash
git add services/command-centre/ledger services/command-centre/test_app.py
git commit -m "feat: add safe operations ledger views"
```

### Task 2: Serve a bounded operations snapshot

**Files:**
- Modify: `services/command-centre/app.py`
- Modify: `services/command-centre/test_app.py`

**Interfaces:**
- Consumes: `bastion.dashboard_tasks` and `bastion.steward_report` from Task 1.
- Produces: `GET /api/operations` returning `{source, generated_at, attention, tasks, pr_report, completions, control}`.
- Produces: `Ledger.operations(now: datetime) -> OperationsSnapshot` for demo and Postgres ledgers.

- [ ] **Step 1: Write failing HTTP and adapter tests**

Cover the endpoint's bounded response, lifecycle counts including `rejected`, recent completion selection, failure/waiting-for-approval attention rows, missing report, stale report at 26 hours, halt attention, and the absence of output fields.

- [ ] **Step 2: Run the focused server tests to verify they fail**

Run: `python -m unittest discover -s services/command-centre`

Expected: FAIL because `/api/operations` and `Ledger.operations` do not exist.

- [ ] **Step 3: Implement typed, bounded snapshot models and ledger reads**

Add dataclasses for task, PR report, attention item, and operations snapshot. Extend `DemoLedger` with representative open, completed, failed, rejected, awaiting-approval, and stale-report states. Extend `PostgresLedger` to query the Task 1 views, cap task/completion and PR result sizes, derive `attention` separately from lifecycle status, and omit all raw output fields.

- [ ] **Step 4: Add `GET /api/operations` to `make_handler`**

Return the snapshot through the existing `_ledger_call` path and preserve the current error and cache behaviour.

- [ ] **Step 5: Run the command-centre test suite**

Run: `python -m unittest discover -s services/command-centre`

Expected: PASS.

- [ ] **Step 6: Commit the operations API**

```bash
git add services/command-centre/app.py services/command-centre/test_app.py
git commit -m "feat: expose command centre operations snapshot"
```

### Task 3: Add typed operations fetching and display helpers

**Files:**
- Modify: `services/command-centre/web/src/lib/ledger.ts`
- Modify: `services/command-centre/web/src/lib/ledger.test.ts`
- Modify: `services/command-centre/web/src/hooks.ts`

**Interfaces:**
- Consumes: `/api/operations` response from Task 2.
- Produces: `OperationsSnapshot` TypeScript types, `useOperations()` polling hook, `isSafeGithubUrl(value: string | null): boolean`.

- [ ] **Step 1: Write failing Vitest cases for operations decoding and link safety**

Assert a valid snapshot decodes, a missing PR report remains `null`, and only `https://github.com/...` links pass `isSafeGithubUrl`.

- [ ] **Step 2: Run the frontend unit tests to verify they fail**

Run: `pnpm test`

Expected: FAIL because the operations types, hook, and URL helper do not exist.

- [ ] **Step 3: Implement operations types, API fetcher, and polling hook**

Follow the existing feed/roster fetch and abort-controller pattern. Refresh every five seconds and retain explicit loading/error states.

- [ ] **Step 4: Run frontend unit tests**

Run: `pnpm test`

Expected: PASS.

- [ ] **Step 5: Commit the frontend data layer**

```bash
git add services/command-centre/web/src/lib services/command-centre/web/src/hooks.ts
git commit -m "feat: add operations dashboard data hook"
```

### Task 4: Render the Operations view

**Files:**
- Modify: `services/command-centre/web/src/App.tsx`
- Create: `services/command-centre/web/src/components/operations.tsx`
- Create: `services/command-centre/web/src/components/operations.test.tsx`
- Modify: `services/command-centre/web/src/index.css`

**Interfaces:**
- Consumes: `OperationsSnapshot` and `isSafeGithubUrl` from Task 3.
- Produces: `OperationsView`, the default navigation view, with `AttentionNow`, `GoalProgress`, `PendingPullRequests`, and `RecentCompletions` components.

- [ ] **Step 1: Write failing component tests**

Render representative snapshot fixtures and assert: attention reasons are visible in text; lifecycle status counts include rejected; stale/missing reports use warning/unknown copy; safe PR URLs render links; unsafe URLs render plain text; and a halt is shown as high-priority attention.

- [ ] **Step 2: Run frontend tests to verify they fail**

Run: `pnpm test`

Expected: FAIL because Operations components do not exist.

- [ ] **Step 3: Implement the default Operations view**

Replace the default `overview` navigation item with `operations`. Use four responsive card sections in the specification order, text-and-colour status signals, existing shadcn primitives, and safe links only. Keep Roster and Ledger unchanged as secondary views. Update headline cards to active agents, attention count, completed tasks in the last 24 hours, and open PRs.

- [ ] **Step 4: Run frontend tests and build**

Run: `pnpm test && pnpm run build`

Expected: PASS and a generated `web/dist`.

- [ ] **Step 5: Commit the Operations UI**

```bash
git add services/command-centre/web/src
git commit -m "feat: add Bastion operations overview"
```

### Task 5: Verify the complete dashboard

**Files:**
- Modify: `services/command-centre/README.md`

**Interfaces:**
- Consumes: the operations endpoint and UI from Tasks 2–4.
- Produces: concise local-run documentation noting demo operations data and the read-only scope.

- [ ] **Step 1: Update the command-centre README**

Document the Operations default view, demo data, and the `LEDGER_DSN` requirement for live read-only data; do not document or add any local secret storage.

- [ ] **Step 2: Run the full required verification set**

Run:

```bash
python -m tools.quartermaster validate
python -m unittest discover -s tests -t .
python -m unittest discover -s services/hello
python -m unittest discover -s services/command-centre
cd services/command-centre/web && pnpm test && pnpm run build
```

Expected: every command passes.

- [ ] **Step 3: Launch the local command centre and inspect it**

Run `python app.py` from `services/command-centre`, open `http://localhost:8080`, and verify the Operations view visibly presents demo attention, task progress, PR report, and completions.

- [ ] **Step 4: Commit verification documentation**

```bash
git add services/command-centre/README.md
git commit -m "docs: describe Bastion operations overview"
```

## Self-review

- Spec coverage: Tasks 1–2 implement the read-only bounded server contract and demo/live data states; Tasks 3–4 implement all four operations areas, safe links, polling, accessibility signals, and retained secondary views; Task 5 documents and verifies the result.
- Type consistency: `OperationsSnapshot` is the shared server response and frontend decoding contract; all later components consume the Task 3 TypeScript form.
- Review focus: each listed stale, missing, unsafe, rejected, and secret-output condition is pinned to the task that owns it.
- Proportion: five independently reviewable tasks cover schema safety, API, client data, UI, and final verification without prescribing implementation bodies.
