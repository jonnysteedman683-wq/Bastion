# Forge P1a: Vortex on Postgres Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Vortex runs on Supabase Postgres in a `forge` schema with a least-privilege role, keeps SQLite working, and
relays every task and operator event to the UniSync ledger exactly once through an outbox.

**Architecture:** A thin `Database` layer sits under `VortexStore`; the store's SQL is made portable and runs on either
`SqliteDatabase` or `PostgresDatabase`, picked by the store target (a `postgresql` URL or a file path). The whole
existing test suite runs against both backends. Schema creation moves to a migrate command run with an admin
connection; the server connects as `vortex_writer`, which has row access only. An outbox table is written in the same
transaction as every task event and operator action, and a relay process copies unsent rows to the ledger.

**Tech Stack:** Python 3.11+, `mcp`, `psycopg[binary]>=3.2,<4` (new runtime dependency), `lestinxor` from git (new
optional extra `unisync`, used only by the relay), PostgreSQL 16 in CI, pytest, ruff.

**Spec:** `Bastion/docs/superpowers/specs/2026-11-10-forge-design.md` (phase P1; sections "Hosting and credentials",
"Data model" invariant 3, "Failure handling", "Testing").

**Repo:** all code changes land in `jonnysteedman683-wq/Vortex-MCP`. This plan lives in Bastion next to the spec.

**Sibling plans (written after this one is approved):** P1b deploys Vortex and Keycloak on the Oracle VM behind a
Cloudflare Tunnel (operations runbook); P1c adds magic-link login to the Bastion dashboard.

## Global Constraints

- Agents never get database credentials; only Vortex (as `vortex_writer`) and the migrate command (admin) connect.
- `vortex_writer` can `SELECT, INSERT, UPDATE, DELETE` on `forge` tables and use their sequences; no DDL.
- Every task event and operator action and its `outbox` row commit in the same transaction.
- A retried relay never writes the same outbox row to the ledger twice.
- SQLite stays fully supported; with `VORTEX_STORE_URL` unset, behaviour is exactly today's.
- Agent-facing tool names, arguments and results do not change.
- Exactly one Vortex server process may run against a given Postgres schema.
- Connection strings and tokens come from environment variables only; never in the repo, logs, errors or test output.
- Lint and tests stay green on Python 3.11 and 3.13 (existing CI matrix).

## Review Focus

1. **A second Vortex process on the same database** must refuse to start with a clear message, not double-claim
   tasks. Test in Task 5.
2. **Supabase drops the connection** (pooler restart, idle timeout): the next tool call reconnects and succeeds
   rather than hanging or crashing the server. Test in Task 3.
3. **The relay crashes after the ledger accepted an event but before marking it sent:** on restart the event is
   not posted again. Test in Task 7.
4. **The ledger gateway refuses an event** (token lacks `memory.write`, 4xx, network down): the row stays unsent,
   the relay backs off, and it never spins in a tight loop. Test in Task 7.
5. **Text Postgres rejects but SQLite accepts** (a NUL character in a message or goal): both backends reject it
   with the same `ValueError` before writing. Test in Task 2.

---

### Task 1: Database layer and SQLite backend, no behaviour change

**Files:**
- Create: `vortex/db.py`
- Create: `vortex/schema/sqlite.sql` (the `CREATE TABLE` / `CREATE INDEX` text now inline in `store.py`)
- Modify: `vortex/store.py` (`__init__`, `_initialize`, every `self._db.execute`, `close`)
- Test: `tests/test_db.py`

**Interfaces:**
- Produces:
  - `class Database(Protocol)`: `execute(sql: str, params: Sequence[Any] = ()) -> Cursor`;
    `transaction() -> ContextManager[None]` (one atomic unit, used under the store's lock); `close() -> None`;
    `dialect: Literal["sqlite", "postgres"]`.
  - `class SqliteDatabase(Database)`: `__init__(path: str)`; applies `PRAGMA foreign_keys = ON` and WAL for file
    paths; `migrate()` runs `vortex/schema/sqlite.sql` plus the existing additive column migrations.
  - `def open_database(target: str, *, schema: str = "forge") -> Database`: a target starting `postgresql://` or
    `postgres://` returns `PostgresDatabase` (Task 3); anything else returns `SqliteDatabase`.
  - Rows behave like mappings (`row["col"]`, `dict(row)`) on both backends.
  - `VortexStore.__init__(target: str | Path, agents=..., approval_required_for=..., *, schema: str = "forge")`:
    same first argument as today, now passed to `open_database`.

- [ ] **Step 1: Write the failing test** `tests/test_db.py::test_sqlite_open_returns_mapping_rows`:
  `db = open_database(":memory:")`, `db.execute("CREATE TABLE t(a TEXT)")`, insert `("x",)` with `?`,
  `row = db.execute("SELECT a FROM t").fetchone()`; assert `row["a"] == "x"`, `dict(row) == {"a": "x"}`,
  `db.dialect == "sqlite"`.
- [ ] **Step 2: Run** `uv run python -m pytest tests/test_db.py -q`. Expected: FAIL, `ModuleNotFoundError: vortex.db`.
- [ ] **Step 3: Implement `vortex/db.py`** with `Database`, `SqliteDatabase`, `open_database`; move schema text to
  `vortex/schema/sqlite.sql` (loaded with `importlib.resources`; add it to package data); switch `VortexStore` to
  `self._db = open_database(...)` and `with self._lock, self._db.transaction():` in place of `with self._lock,
  self._db:`.
- [ ] **Step 4: Run the whole suite** `uv run python -m pytest -q && uv run ruff check .`. Expected: every existing
  test PASSES unchanged, plus the new one.
- [ ] **Step 5: Commit** `refactor(store): put a Database layer under VortexStore (SQLite only, no behaviour change)`.

### Task 2: Portable SQL

**Files:**
- Modify: `vortex/store.py`
- Test: `tests/test_store.py` (add one test)

**Interfaces:**
- Consumes: `Database.execute`, `Database.dialect` (Task 1).
- Produces: store SQL that runs unchanged on SQLite ≥ 3.24 and Postgres 16: `?` placeholders only (the Postgres
  backend rewrites them), `INSERT ... ON CONFLICT DO NOTHING` instead of `INSERT OR IGNORE`, `RETURNING` instead of
  `lastrowid`, no `PRAGMA` outside `SqliteDatabase`.
  `def _check_text(name: str, value: str) -> None` in `store.py`: raises
  `ValueError(f"{name} must not contain NUL characters")` when `"\x00" in value`; called for message bodies, task
  goals, results, reasons and notes before any write.

- [ ] **Step 1: Write the failing test** `test_nul_characters_are_rejected_before_writing`:
  `send("hermes-1", "spark-1", "a\x00b")` raises `ValueError` matching `"NUL"` and `inbox("spark-1") == []`
  afterwards; same for `create_task("hermes-1", "goal\x00")` with `list_tasks` staying empty.
- [ ] **Step 2: Run** it. Expected: FAIL (SQLite stores the NUL today).
- [ ] **Step 3: Rewrite the SQLite-only constructs** listed above and add `_check_text` calls.
- [ ] **Step 4: Run the whole suite and lint.** Expected: PASS.
- [ ] **Step 5: Commit** `refactor(store): portable SQL and NUL rejection`.

### Task 3: Postgres backend and dual-backend test suite

**Files:**
- Modify: `vortex/db.py` (add `PostgresDatabase`)
- Create: `vortex/schema/postgres.sql`
- Create: `tests/backends.py`
- Modify: every test file that constructs `VortexStore` (`test_store.py`, `test_approval.py`,
  `test_fleet_control.py`, `test_horizontal_adapter.py`, `test_operator.py`, `test_sentinel_adapter.py`,
  `test_server_auth.py`) to build stores through `tests/backends.py`
- Modify: `pyproject.toml` (add `psycopg[binary]>=3.2,<4` to `dependencies`), `uv.lock`
- Modify: `.github/workflows/ci.yml` (add a `postgres` job, below)
- Test: `tests/test_db.py`

**Interfaces:**
- Produces:
  - `class PostgresDatabase(Database)`: `__init__(url: str, *, schema: str)`; one `psycopg` connection with
    `row_factory=dict_row` and `search_path` set to `schema`; rewrites `?` to `%s` outside quoted string literals;
    `transaction()` wraps `conn.transaction()`; on `psycopg.OperationalError` at the start of `execute` or
    `transaction`, reconnects once and retries; a failure on the retry raises `ConnectionError("database
    unavailable")` (no URL in the message).
  - `vortex/schema/postgres.sql`: the same tables as `sqlite.sql`, created in the current `search_path` schema;
    `BIGINT GENERATED ALWAYS AS IDENTITY` for `task_events.event_id` and `operator_actions.action_id`; every
    timestamp column stays `TEXT` with `COLLATE "C"` so ISO-8601 strings sort exactly as in SQLite.
  - `tests/backends.py`: `def make_store(tmp: Path, **kwargs) -> VortexStore` builds a SQLite store in `tmp` by
    default; when `VORTEX_TEST_DATABASE_URL` is set, it creates schema `t_<uuid hex>` with
    `vortex.migrate.apply(url, schema)` (Task 4), returns a store on it, and registers a schema drop as cleanup.
  - Tests that assert SQLite internals (they `import sqlite3`) get
    `@unittest.skipIf(os.environ.get("VORTEX_TEST_DATABASE_URL"), "SQLite-specific")`.
- CI job `postgres`: a `postgres:16` service container; `VORTEX_TEST_DATABASE_URL` is assembled in the job from the
  service's `POSTGRES_USER` / `POSTGRES_PASSWORD` env values and `localhost:5432`; runs
  `uv run --locked python -m pytest -q` on Python 3.13.

- [ ] **Step 1: Write the failing tests** in `tests/test_db.py`, skipped unless `VORTEX_TEST_DATABASE_URL` is set:
  `test_postgres_rows_and_placeholders` (Task 1's assertions, plus a stored value containing a literal `'?'`
  survives the placeholder rewrite) and `test_postgres_reconnects_after_dropped_connection` (open; from a second
  connection run `SELECT pg_terminate_backend(<first backend pid>)`; then `execute("SELECT 1")` on the first
  returns `1`).
- [ ] **Step 2: Run** with `VORTEX_TEST_DATABASE_URL` pointing at a local Postgres 16:
  `uv run python -m pytest tests/test_db.py -q`. Expected: FAIL, `PostgresDatabase` missing.
- [ ] **Step 3: Implement `PostgresDatabase`, `postgres.sql`, `tests/backends.py`**; switch test construction sites
  to `make_store`; add the dependency and the CI job.
- [ ] **Step 4: Run the whole suite on both backends** (without and with `VORTEX_TEST_DATABASE_URL`) and lint.
  Expected: identical pass counts apart from the SQLite-specific skips.
- [ ] **Step 5: Commit** `feat(store): Postgres backend; run the whole suite on SQLite and Postgres`.

### Task 4: Migrate command and the `vortex_writer` role

**Files:**
- Create: `vortex/migrate.py`
- Create: `vortex/schema/postgres_roles.sql`
- Modify: `vortex/__main__.py` (add `migrate` subcommand)
- Modify: `vortex/db.py` (`PostgresDatabase` runs no DDL; it checks the schema exists)
- Test: `tests/test_migrate.py` (Postgres-only, skipped otherwise)

**Interfaces:**
- Produces:
  - `def apply(admin_url: str, schema: str = "forge") -> None`: idempotent; validates `schema` against
    `^[a-z_][a-z0-9_]{0,62}$` (else `ValueError`), creates the schema, runs `postgres.sql` in it, then
    `postgres_roles.sql` with the schema name substituted.
  - `postgres_roles.sql`: creates role `vortex_writer NOLOGIN` if missing (the owner grants it login with its own
    password in Supabase, in P1b); `GRANT USAGE ON SCHEMA`; `GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES`;
    `GRANT USAGE, SELECT ON ALL SEQUENCES`; matching `ALTER DEFAULT PRIVILEGES`; no `CREATE`.
  - CLI `python -m vortex migrate`: reads `VORTEX_MIGRATE_URL` (admin connection) and `VORTEX_SCHEMA`
    (default `forge`).
  - `PostgresDatabase.__init__` raises `RuntimeError("schema <name> is missing; run: python -m vortex migrate")`
    when the schema or its `tasks` table is absent.

- [ ] **Step 1: Write the failing tests:** `test_apply_is_idempotent` (run `apply` twice, no error, tables present);
  `test_writer_cannot_run_ddl` (create a throwaway login role granted `vortex_writer`, connect as it: `CREATE TABLE`
  in the schema raises `psycopg.errors.InsufficientPrivilege`, an `INSERT` into `agents` succeeds);
  `test_store_refuses_unmigrated_schema` (`VortexStore(url, schema="nope")` raises `RuntimeError` matching
  `"python -m vortex migrate"`); `test_bad_schema_name_is_refused` (`apply(url, "x; drop")` raises `ValueError`).
- [ ] **Step 2: Run** them on Postgres. Expected: FAIL.
- [ ] **Step 3: Implement** `migrate.py`, the roles file and the CLI subcommand; point `tests/backends.py` at
  `apply`.
- [ ] **Step 4: Run both backends and lint.** Expected: PASS.
- [ ] **Step 5: Commit** `feat(migrate): forge schema migration and least-privilege vortex_writer role`.

### Task 5: One server per database

**Files:**
- Modify: `vortex/db.py`, `vortex/server.py`
- Test: `tests/test_db.py`

**Interfaces:**
- Produces: `PostgresDatabase.acquire_instance_lock() -> None`: `pg_try_advisory_lock(hashtext('vortex:' ||
  schema))` on its connection; when it returns false, raises `RuntimeError("another Vortex server is already using
  schema <schema>")`. After a reconnect the lock is re-acquired; if that fails, the server exits non-zero.
  `server.py` calls it once at startup when the backend is Postgres. Stores built by tests do not take the lock.

- [ ] **Step 1: Write the failing test** `test_second_server_is_refused`: two `PostgresDatabase` on one schema;
  the first `acquire_instance_lock()` succeeds, the second raises `RuntimeError` matching `"already using"`; after
  closing the first, a third succeeds.
- [ ] **Step 2: Run.** Expected: FAIL.
- [ ] **Step 3: Implement** and call it at server startup.
- [ ] **Step 4: Run both backends and lint.** Expected: PASS.
- [ ] **Step 5: Commit** `feat(server): refuse to start a second Vortex on the same database`.

### Task 6: Outbox written atomically with every event

**Files:**
- Modify: `vortex/schema/sqlite.sql`, `vortex/schema/postgres.sql` (add `outbox`)
- Modify: `vortex/store.py`
- Test: `tests/test_outbox.py` (both backends)

**Interfaces:**
- Produces:
  - Table `outbox(outbox_id` integer identity primary key, `event_kind TEXT NOT NULL`, `payload_json TEXT NOT
    NULL`, `created_at TEXT NOT NULL`, `sent_at TEXT)`, index on `(sent_at, outbox_id)`.
  - `VortexStore._emit(event_kind: str, payload: dict) -> None`: inserts one outbox row inside the open
    transaction. Called beside every `task_events` insert with `event_kind="task.<event_type>"` and payload
    `{event_id, task_id, actor_id, event_type, details, created_at}`, and beside every `operator_actions` insert
    with `event_kind="operator.<action>"` and payload `{action_id, actor_id, action, target_id, details,
    created_at}`.
  - `VortexStore.outbox_unsent(limit: int = 100) -> list[dict]` (oldest first) and
    `VortexStore.outbox_mark_sent(outbox_id: int) -> None`, used only by the relay.

- [ ] **Step 1: Write the failing tests:** `test_every_task_event_has_one_outbox_row` (create, claim, complete a
  task; the number of `task.*` outbox rows equals the number of `task_events` rows and their `event_id`s match);
  `test_operator_action_writes_outbox` (pause an agent; exactly one `operator.pause_agent` row);
  `test_failed_write_leaves_no_outbox_row` (patch `_emit` to raise after the task-event insert; afterwards neither
  that event nor any outbox row exists).
- [ ] **Step 2: Run on both backends.** Expected: FAIL.
- [ ] **Step 3: Implement** the table, the `_emit` calls and the two relay helpers.
- [ ] **Step 4: Run both backends and lint.** Expected: PASS.
- [ ] **Step 5: Commit** `feat(store): transactional outbox for task events and operator actions`.

### Task 7: Ledger relay

**Files:**
- Create: `vortex/relay.py`
- Modify: `vortex/__main__.py` (add `relay` subcommand)
- Modify: `pyproject.toml` (optional extra `unisync = ["lestinxor @ git+https://github.com/jonnysteedman683-wq/LESTINXOR-AGENT.git"]`, also in the `dev` group), `uv.lock`
- Test: `tests/test_relay.py`

**Interfaces:**
- Consumes: `VortexStore.outbox_unsent`, `VortexStore.outbox_mark_sent` (Task 6).
- Produces:
  - `SCOPE = "forge"`, `SENDER = "vortex"`, key format `"outbox/{outbox_id}"`.
  - `class LedgerLike(Protocol)`: `post(sender, kind, task_id="", **body)`; `memory(scope) -> dict[str, Any]`
    (the shape LinX's relay already uses).
  - `def relay_once(store: VortexStore, ledger: LedgerLike, *, batch: int = 100) -> int`: takes unsent rows in
    `outbox_id` order; if the row's key is already in `ledger.memory(SCOPE)` it only marks the row sent; otherwise
    posts `kind="memory.write"` with body `{"scope": SCOPE, "key": key, "value": {"kind": event_kind, **payload}}`,
    then marks it sent. Returns how many it marked. On the first post failure it stops, leaving that row and later
    ones unsent, and re-raises.
  - `def run(store, ledger, *, interval: float = 2.0, max_backoff: float = 300.0, sleep=time.sleep) -> None`:
    loops `relay_once`; after a failure waits `min(interval * 2**failures, max_backoff)` and logs only the error
    class and outbox id (never payloads or tokens); resets the count after a success.
  - CLI `python -m vortex relay`: opens the store from `VORTEX_STORE_URL` and the ledger with lestinxor's
    `SupabaseLedger(url=os.environ["UNISYNC_GATEWAY_URL"], key=os.environ["UNISYNC_VORTEX_TOKEN"])`.

- [ ] **Step 1: Write the failing tests** with an in-memory `FakeLedger`:
  `test_relays_each_row_once_in_order`; `test_crash_after_post_does_not_duplicate` (the fake accepts the post, then
  `outbox_mark_sent` raises once; a second `relay_once` posts nothing new and marks the row sent);
  `test_post_failure_leaves_row_unsent_and_stops` (the fake fails on row 2: row 1 sent, rows 2 and 3 unsent);
  `test_run_backs_off_exponentially_and_caps` (always-failing fake and a recording `sleep`: waits 4, 8, 16, ...
  capped at 300, never 0); `test_logs_contain_no_payload` (a failing post for a payload containing
  `"secret-marker"`; captured logs do not contain it).
- [ ] **Step 2: Run.** Expected: FAIL.
- [ ] **Step 3: Implement** `relay.py` and the CLI subcommand.
- [ ] **Step 4: Run both backends and lint.** Expected: PASS.
- [ ] **Step 5: Commit** `feat(relay): copy the outbox to the UniSync ledger exactly once`.

### Task 8: Server wiring, settings and docs

**Files:**
- Modify: `vortex/settings.py`, `vortex/server.py`
- Modify: `README.md` (new "Storage" section: env vars, migrate, relay, the single-instance rule)
- Test: `tests/test_settings.py`

**Interfaces:**
- Produces settings: `VORTEX_STORE_URL` (unset = today's SQLite file under the data dir; a `postgresql` URL =
  Postgres, connecting as `vortex_writer`) and `VORTEX_SCHEMA` (default `forge`). The URL is never echoed in logs,
  errors or `repr`; a startup log line names only the backend and schema.

- [ ] **Step 1: Write the failing tests:** `test_store_url_defaults_to_sqlite_file` (unset -> path ends
  `vortex.sqlite3`); `test_postgres_url_selects_postgres` (a `postgresql` URL built in the test from a user and
  password -> backend `postgres`, schema `forge`); `test_store_url_is_not_in_settings_repr` (neither that user nor
  that password appears in `repr(settings)`).
- [ ] **Step 2: Run.** Expected: FAIL.
- [ ] **Step 3: Implement** the settings, the server's store construction and the README section.
- [ ] **Step 4: Run both backends and lint.** Expected: PASS. Then end to end on local Postgres: `python -m vortex
  migrate`, start the server with `VORTEX_STORE_URL` set, run the dev-token round trip from
  `tests/test_server_auth.py` against it, and confirm a message round trip and a task claim succeed and appear in
  `outbox`.
- [ ] **Step 5: Commit** `feat(server): select the store from VORTEX_STORE_URL; document storage`.

## Done when (P1a exit)

- The full Vortex suite passes on SQLite and on Postgres 16 in CI.
- `python -m vortex migrate` creates `forge` and `vortex_writer`; the server runs as `vortex_writer` and cannot run
  DDL.
- A second server on the same schema is refused.
- Every task event and operator action reaches the ledger as a `memory.write` in scope `forge`, exactly once.

## Owner prerequisites (needed for P1b, not to build P1a)

- The `vortex` ledger token must allow the `memory.write` kind (`public.unisync_tokens.kinds`). LinX's relay already
  relies on this; check rather than assume.
- A Supabase admin connection string to run `python -m vortex migrate` once, kept in your environment only.
