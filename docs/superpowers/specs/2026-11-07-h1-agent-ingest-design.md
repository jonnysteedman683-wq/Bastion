# H1 Per-Agent Ingest Design

## Intent

Let privately hosted agents report meaningful lifecycle events to the Bastion command centre, so their activity appears in the dashboard within five seconds without giving them ledger or dashboard access.

## Goals

- Accept authenticated `POST /api/ingest` requests from trusted agents.
- Identify the submitting agent from its individual Bearer key, not from client-supplied event data.
- Store accepted events in the local SQLite ingest buffer and include them in the dashboard feed.
- Support configuration-only key rotation and revocation.

## Non-goals

- A user interface for creating or distributing keys.
- Public internet exposure, cross-machine discovery, or direct Supabase writes.
- Changing the existing ledger's read-only dashboard role.

## Approaches

1. A shared API key is quick, but it cannot identify or revoke one agent independently.
2. A per-agent key registry is selected: an environment-provided JSON object maps agent names to secrets. The server derives the event's `agent` value from the matching key. Rotating or removing one entry changes only configuration.
3. Direct database credentials for every agent were rejected because they bypass the dashboard boundary and over-grant access.

## Design

`INGEST_AGENT_KEYS` holds a JSON object of `{ "agent-name": "secret" }`; it is deployment configuration and is never committed. A request must present `Authorization: Bearer <secret>`. The server uses a constant-time comparison against configured values, rejects absent, malformed, unknown, and ambiguous configurations with `401`, and assigns the mapped agent name to the stored event.

The JSON body accepts `source`, `kind`, `summary`, and optional ISO-8601 `ts`. `summary` remains required. Client-supplied `agent` is ignored so one agent cannot impersonate another. Successful requests return `201` with the assigned id and timestamp; malformed JSON or missing summary returns `400`.

The command centre wraps its configured ledger with the existing local-ingest merger before serving feed and roster reads. Thus accepted local events appear alongside ledger events, and roster status reflects their last-seen time. Pagination preserves distinct ids for local and ledger events.

## Constraints

- Retain the standard-library implementation and existing local SQLite buffer.
- Keep the command centre private; do not make it public or change instance caps.
- Never log or commit secrets.

## Verification

Tests will prove that an agent's key can post an event, the returned and displayed event uses the configured agent identity, incorrect or absent keys are rejected, a supplied conflicting `agent` field is ignored, and existing feed/roster behavior still works. The repository's required Quartermaster and unit-test checks will run with the installed Python runtime.
