# Bastion operations overview — design

## Intent

Bastion should answer the daily operator questions at a glance: what agents completed, which goals are progressing, what pull requests need a decision, and what is blocked or becoming a bottleneck. It remains a private, read-only control room for this release.

## Goals

- Replace the current passive overview with a decision-oriented operations view.
- Show a concise recent-completions list, derived from completed task events.
- Show goal/task completion by status: open, awaiting approval, approved, claimed, done, failed, and blocked/escalated.
- Show the latest PR-steward report, with each open PR grouped by its next action (for example review, CI red, conflict, or merge-ready).
- Surface a single, prioritized attention report: failed tasks, pending approvals, PR escalations, stale/duplicate PRs, and PR floods.
- Preserve the existing roster and full ledger as secondary views.

## Non-goals

- No task creation, approval, merge, pause, or deployment controls in this release.
- No browser-to-GitHub API calls or GitHub credentials in the frontend.
- No direct access from the browser to Supabase or the underlying UniSync tables.
- No change to the deployment, credential, or security model.

## Constraints

- The command centre continues to use only the server-side `bastion_reader` role and read-only `bastion` schema views.
- The existing API must never expose raw ledger event bodies, tokens, or database credentials.
- An absent or stale PR-steward heartbeat must render as an explicit empty/stale state, not as a clean bill of health.
- The dashboard must retain a coherent demo mode for local development.

## Approaches considered

1. **Operations overview from the ledger (chosen).** Add server-side, bounded projections of task states and the latest steward heartbeat, then render them as an actionable read-only overview. This uses Bastion's existing data flow and preserves the current privacy boundary.
2. **Live GitHub queries from the command centre.** The service fetches PR state directly at page load. This would be more immediate but duplicates the steward, needs broader credential handling, and gives different answers than the scheduled report.
3. **Full control panel.** Combine visibility with summon, approval, pause, and merge actions now. This has the most value later, but requires a distinct write authorization and audit design that is out of scope here.

## Design

### Navigation and overview

Keep the compact left navigation. Make **Operations** the default view, with **Roster** and **Ledger** retained. The operations page has four areas, ordered by decision urgency:

1. **Attention now** — a prominent count and list of failures, blocked/awaiting-approval tasks, and steward escalations. Each row explains why it needs action and links to the underlying task or PR where a URL is available.
2. **Goal progress** — status counts and a compact task list. Completed tasks appear separately from work still in progress. A task's status comes from `bastion.tasks`; a task is considered blocked when it failed, is awaiting approval, or carries an escalation associated with it.
3. **Pending merge requests** — the latest PR-steward heartbeat grouped by bucket, with PR reference, reason/flag, and the existing GitHub link. The heading shows when the steward last scanned and warns if the report is stale.
4. **Recent completions** — the newest completed task results and successful deploy records, with agent, concise outcome, and timestamp.

The existing high-level cards remain, adjusted to show agents active, tasks needing attention, completed tasks in the last 24 hours, and open PRs.

### Data contract

Extend the server's read-only ledger adapter with a single operations snapshot endpoint. It reads only:

- `bastion.tasks` for task lifecycle state;
- `bastion.control` for an informational halt status, if present;
- the most recent `steward` `heartbeat` and relevant `escalate` events, projected to only the existing safe summary fields plus the steward's bounded PR items.

The response is deliberately bounded (latest 60 PRs and a capped task/completion set) and labelled with its source (`ledger` or `demo`). The frontend polls it with the same five-second cadence as the current feed. Demo data includes representative completed, pending, failed, and blocked items so all states remain reviewable without production access.

### States and empty cases

- Empty task list: show “No tracked tasks yet,” not zero progress.
- No steward heartbeat: show “No PR report received yet.”
- Stale heartbeat: show the report with a warning and its scan time.
- Ledger/API failure: retain the existing visible error treatment; do not silently render stale data as live.

### Accessibility and presentation

Use text labels alongside colour for all risk and status signals. Keep tables responsive and avoid hiding the reason a row needs attention. Refresh announcements stay polite so frequent polling does not disrupt screen readers.

## What remains for implementation

- Add safe SQL projections/migrations for the dashboard's read-only task and steward summary reads, where the current views are insufficient.
- Extend the Python server and tests for the operations snapshot endpoint and demo ledger.
- Add typed frontend data fetching, the Operations view, and component tests.
- Keep the existing checks passing, then build the web app.
