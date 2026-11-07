# H1 Per-Agent Ingest Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Allow each configured private agent to submit authenticated lifecycle events that appear in Bastion's dashboard feed.

**Architecture:** Keep the existing standard-library HTTP server and SQLite buffer. Parse an environment-only per-agent key registry, resolve a Bearer token to its configured agent name, and ignore any client-provided identity. Wrap the base ledger with the local ingest buffer so accepted events participate in the existing feed and roster-status reads.

**Tech Stack:** Python 3.13 standard library (`http.server`, `sqlite3`, `json`, `hmac`), existing unittest suite.

**Spec:** `docs/superpowers/specs/2026-11-07-h1-agent-ingest-design.md`

## Global Constraints

- Implement backlog item H1 only; do not alter guarded paths.
- Keep the command centre private and its instance cap unchanged.
- Do not commit, log, or document real credential values.
- Retain the standard-library runtime and local SQLite ingest buffer.
- Run required validation using `C:\Users\grizz\AppData\Local\Programs\Python\Python313\python.exe` because `python` is not on this machine's PATH.

## Review Focus

- Missing, malformed, unknown, or duplicate per-agent key configurations must never authenticate a request.
- A valid key must override a conflicting client-supplied `agent` value.
- Invalid JSON and an absent `summary` must not create a SQLite event.
- Local and ledger events must have distinct stable API ids when merged.
- A local event must become visible through `GET /api/feed` promptly without regressing its limit behavior.

### Task 1: Per-agent key authentication

**Files:**
- Modify: `services/command-centre/app.py: imports, ingest configuration helpers, Handler._ingest`
- Modify: `services/command-centre/test_app.py: CommandCentreHttpTest request helper and ingest cases`

**Interfaces:**
- Produces: `ingest_agent_keys(env: Mapping[str, str] | None = None) -> dict[str, str]`, which returns an empty mapping for missing, invalid, non-object, empty-value, or duplicate-secret configuration.
- Produces: `agent_for_ingest_token(authorization: str, keys: Mapping[str, str]) -> str | None`, which returns the configured name only for a valid Bearer token using constant-time comparison.

- [ ] **Step 1: Write failing HTTP tests for a configured agent key**

Extend the test request helper to accept headers. Set a temporary `INGEST_AGENT_KEYS` JSON registry containing two placeholder secrets; assert that a matching key creates an event with the mapped agent name, and that a conflicting body `agent` does not change it.

- [ ] **Step 2: Run the focused test to verify it fails**

Run: `C:\Users\grizz\AppData\Local\Programs\Python\Python313\python.exe -m unittest services.command-centre.test_app.CommandCentreHttpTest`

Expected: FAIL because the current endpoint only supports the shared `INGEST_API_KEY` and trusts the body agent value.

- [ ] **Step 3: Write failing rejection tests**

Assert `401` for missing, malformed, and unknown Bearer tokens; assert that malformed registry JSON and duplicate secret values authenticate no agent; assert `400` for invalid JSON and for a body without `summary`.

- [ ] **Step 4: Implement `ingest_agent_keys` and `agent_for_ingest_token` in `app.py`**

Parse `INGEST_AGENT_KEYS` as a JSON object of non-empty string agent names and secrets. Treat any malformed or ambiguous registry as disabled. Use `hmac.compare_digest` for comparisons. In `_ingest`, derive the agent only from the resolved token, retain the current payload fields other than `agent`, and preserve existing success/error response shapes.

- [ ] **Step 5: Run the focused service tests**

Run: `C:\Users\grizz\AppData\Local\Programs\Python\Python313\python.exe -m unittest discover -s services/command-centre`

Expected: PASS with all existing tests and new ingest authentication cases.

### Task 2: Merge accepted local events into the dashboard feed

**Files:**
- Modify: `services/command-centre/app.py: MergedLedger and build_server`
- Modify: `services/command-centre/test_app.py: feed visibility cases`

**Interfaces:**
- Consumes: `ingest_event(source, agent, kind, summary, ts) -> Event` and the authenticated agent identity from Task 1.
- Produces: `MergedLedger(base: Ledger)`, passed by `build_server`, whose `events(after, limit)` returns distinct id values across local and base events.

- [ ] **Step 1: Write a failing end-to-end feed test**

Post a valid ingest event using an existing roster agent, then fetch `/api/feed`; assert the event is present with its configured agent, kind, and summary, and that the result respects the requested limit.

- [ ] **Step 2: Run the focused test to verify it fails**

Run: `C:\Users\grizz\AppData\Local\Programs\Python\Python313\python.exe -m unittest discover -s services/command-centre -p test_app.py`

Expected: FAIL because `build_server` currently serves the base ledger without the local-ingest wrapper.

- [ ] **Step 3: Implement merged feed ids and wire `MergedLedger` in `build_server`**

Reserve a documented numeric id range for local SQLite events, return the offset id in API events, and translate that range when selecting local events. Merge by event timestamp newest-first, then apply the requested limit. Construct the handler with `MergedLedger(ledger or ledger_from_env())` so the production server and HTTP tests use the same path.

- [ ] **Step 4: Run the focused service tests**

Run: `C:\Users\grizz\AppData\Local\Programs\Python\Python313\python.exe -m unittest discover -s services/command-centre`

Expected: PASS, including the new end-to-end feed case.

### Task 3: Document configuration and complete H1

**Files:**
- Modify: `services/command-centre/README.md: local-agent ingest configuration and placeholder request example`
- Modify: `docs/BACKLOG.md: mark H1 complete only after verification`

**Interfaces:**
- Consumes: `INGEST_AGENT_KEYS` as a JSON environment variable and `POST /api/ingest` from Tasks 1 and 2.
- Produces: operator instructions that use placeholder secrets only.

- [ ] **Step 1: Update the command-centre README**

Document the private endpoint, the `INGEST_AGENT_KEYS` shape, the fields agents may send, the server-assigned agent identity, rotation/revocation by configuration, and a curl example using placeholder values.

- [ ] **Step 2: Run the full required checks**

Run:

```powershell
& 'C:\Users\grizz\AppData\Local\Programs\Python\Python313\python.exe' -m tools.quartermaster validate
& 'C:\Users\grizz\AppData\Local\Programs\Python\Python313\python.exe' -m unittest discover -s tests -t .
Get-ChildItem -Directory services | ForEach-Object { & 'C:\Users\grizz\AppData\Local\Programs\Python\Python313\python.exe' -m unittest discover -s $_.FullName }
```

Expected: every command exits `0`.

- [ ] **Step 3: Mark H1 complete in `docs/BACKLOG.md`**

Tick only H1 after the full checks pass and the manual local POST-to-feed verification succeeds within five seconds.

- [ ] **Step 4: Commit the H1 work**

```powershell
git add docs/BACKLOG.md docs/superpowers/specs/2026-11-07-h1-agent-ingest-design.md docs/superpowers/plans/2026-11-07-h1-agent-ingest.md services/command-centre
git commit -m "feat: add per-agent ingest authentication"
```

## Self-Review

- The plan covers authentication, identity spoof prevention, SQLite persistence, feed visibility, documentation, H1 completion, and all required checks.
- Each task has a focused failing test, the minimal corresponding implementation, and a passing verification step.
- The selected key registry keeps secrets outside the repository and leaves key management UI, public exposure, and direct database access out of scope.
