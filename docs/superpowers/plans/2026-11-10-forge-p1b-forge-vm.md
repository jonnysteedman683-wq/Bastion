# Forge P1b: the Forge VM Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

> **Revised 2026-11-10:** Google Cloud e2-micro instead of Oracle, and per-agent Vortex API keys instead of Keycloak
> (owner's choice; spec D6 and D11 amended to match).

**Goal:** A Google Cloud Always Free e2-micro VM, built from one pasted cloud-init file plus one setup command, serves
Vortex (on Supabase, from P1a) over Tailscale Funnel, and every agent connects with its own revocable API key.

**Architecture:** Vortex-MCP gains an `apikey` auth mode: agents send `Authorization: Bearer <key>`; Vortex hashes it
and looks it up in `forge.agent_keys`, so keys live in Supabase and can be issued or revoked without touching the VM.
The VM bundle lives in Bastion `deploy/forge-vm/`: cloud-init installs Tailscale and `uv`, adds 1 GB of swap, creates an
unprivileged `forge` user and checks out Vortex-MCP at a pinned commit; `forge-setup.sh` writes secrets to a root-only
env file, starts Vortex and its relay under systemd, and publishes `/mcp` through Funnel. **The VM holds no state.**

**Tech Stack:** Python 3.11+, Vortex-MCP (P1a), `mcp` token verifier API, Supabase Postgres; Ubuntu 24.04 LTS (x86_64)
on e2-micro, cloud-init, Tailscale (Funnel), `uv`, systemd. Bastion tests: standard library only.

**Spec:** `docs/superpowers/specs/2026-11-10-forge-design.md` (phase P1; D6 and D11 as amended; "Hosting and
credentials"; data model `agent_keys`).

**Repos:** Task 1 in `jonnysteedman683-wq/Vortex-MCP`; Tasks 2–3 in Bastion `deploy/forge-vm/` and `docs/` (not guarded;
`infra/` is). Task 4 is run live with the owner.

## Global Constraints

- No secret, key, token, password or connection string is ever committed, logged, or put in an error or `repr`.
  Templates hold variable **names** only.
- API keys are `vtx_` + 43 URL-safe characters (32 random bytes from `secrets.token_urlsafe(32)`); only
  `sha256(key)` hex is stored; a key is printed exactly once, when issued.
- Revocation takes effect on the next request (no caching of positive lookups longer than 30 seconds).
- On the VM, secrets live only in `/etc/forge/forge.env`, mode `0600`, owner `root`, read via `EnvironmentFile=`.
- Vortex and the relay run as user `forge` (no sudo); Vortex binds `127.0.0.1:8000`; only `/mcp` is published.
- Free-tier limits: one e2-micro in `us-west1`, `us-central1` or `us-east1`; 30 GB standard persistent disk; about
  1 GB/month of free egress, which agent traffic stays well inside.
- Agent IDs: `claude-1`, `hermes-1`, `hermes-2`, `sebastian-1`, `lestinxor-1`, `jules-1`.
- Supabase connections use the direct or session-mode connection string (P1a requirement).
- Pinned: Vortex-MCP at the P1a merge commit (`VORTEX_REF` in `forge.env.example`).

## Review Focus

1. **A revoked key** must stop working within 30 seconds, including for an agent mid-session. Test in Task 1.
2. **A key that looks valid but is not** (wrong prefix, truncated, another agent's key with one character changed,
   empty, very long) is refused without a database error and without timing differences worth exploiting
   (hash-then-lookup, never compare raw keys). Test in Task 1.
3. **Re-running `forge-setup.sh`** changes nothing on a working VM. Test in Task 2 (dry-run) and Task 4.
4. **The VM runs out of memory** (1 GB): swap is on, and systemd restarts Vortex if it is killed. Checked in Task 4.
5. **A reboot** (Google maintenance) brings everything back without the owner. Checked in Task 4.

---

### Task 1: Vortex `apikey` auth mode and key management (Vortex-MCP)

**Files:**
- Modify: `vortex/schema/sqlite.sql`, `vortex/schema/postgres.sql` (add `agent_keys`)
- Modify: `vortex/store.py` (key methods), `vortex/settings.py` (mode `apikey`), `vortex/server.py` (verifier wiring),
  `vortex/__main__.py` (`keys` subcommand), `README.md` (auth modes table)
- Create: `vortex/keys.py`
- Test: `tests/test_keys.py` (both backends), `tests/test_settings.py`, `tests/test_server_auth.py`

**Interfaces:**
- Produces:
  - Table `agent_keys(key_hash TEXT PRIMARY KEY, agent_id TEXT NOT NULL REFERENCES agents(agent_id), created_at TEXT
    NOT NULL, revoked_at TEXT)` (Postgres columns `COLLATE "C"`), index on `agent_id`.
  - `VortexStore.issue_key(agent_id: str) -> str`: raises `ValueError` for an unknown agent; inserts the hash; returns
    the plaintext key (the only time it exists). An agent may hold several active keys (for rotation).
  - `VortexStore.revoke_keys(agent_id: str) -> int`: sets `revoked_at` on all active keys; returns how many.
  - `VortexStore.agent_for_key(key: str) -> str | None`: hashes and looks up an active key.
  - `VortexStore.list_keys() -> list[dict]`: `agent_id, created_at, revoked_at` and the first 8 hex of the hash; never
    the hash in full.
  - `class ApiKeyTokenVerifier(TokenVerifier)` in `vortex/keys.py`: `__init__(store, *, ttl: float = 30.0, clock=
    time.monotonic)`; `verify_token(token)` returns an `AccessToken` with `client_id=subject=agent_id` and the scopes
    `vortex:connect`, `vortex:read`, `vortex:message`, or `None`. Caches positive lookups for at most `ttl` seconds;
    rejects anything not matching `^vtx_[A-Za-z0-9_-]{43}$` before touching the store.
  - Settings: `VORTEX_AUTH_MODE=apikey` requires `VORTEX_PUBLIC_URL` (https), `VORTEX_ALLOWED_HOSTS`,
    `VORTEX_ALLOWED_ORIGINS` and a Postgres `VORTEX_STORE_URL`; no per-agent tokens or OAuth settings.
  - Server: in `apikey` mode, `issuer_url = resource base = VORTEX_PUBLIC_URL`, verifier `ApiKeyTokenVerifier(store)`,
    transport security as in `oauth` mode.
  - CLI: `python -m vortex keys add <agent>` (prints the key once, with a "store this now" line),
    `keys revoke <agent>`, `keys list`; all read `VORTEX_STORE_URL` and `VORTEX_SCHEMA`.

- [ ] **Step 1: Write the failing tests:** `test_issue_and_verify_round_trip`; `test_unknown_agent_cannot_get_key`;
  `test_revoked_key_stops_within_ttl` (fake clock: valid before revoke, still cached until `ttl`, refused after);
  `test_malformed_keys_never_reach_the_store` (empty, wrong prefix, 42 and 44 chars, 10 kB string, NUL; the store's
  lookup is never called); `test_near_miss_key_is_refused` (another agent's key with one character changed);
  `test_list_keys_hides_hashes`; `test_key_not_in_logs` (issue a key with logging captured; the key is absent);
  settings tests for `apikey` (requires https public URL and Postgres store; rejects SQLite); a server test that
  `create_server` in `apikey` mode uses `ApiKeyTokenVerifier` and the public URL.
- [ ] **Step 2: Run** on both backends. Expected: FAIL.
- [ ] **Step 3: Implement** to the interfaces above.
- [ ] **Step 4: Run** both suites and `ruff`. Expected: PASS, with the counts recorded.
- [ ] **Step 5: Commit** `feat(auth): per-agent API keys stored as hashes (apikey mode)`; push; open the PR.

### Task 2: The `deploy/forge-vm/` bundle and its static tests (Bastion)

**Files:**
- Create: `deploy/forge-vm/cloud-init.yaml`, `forge-setup.sh`, `forge-check.sh`, `vortex.service`,
  `vortex-relay.service`, `forge.env.example`, `README.md` (all under `deploy/forge-vm/`)
- Test: `tests/test_forge_vm.py`

**Interfaces:**
- Produces:
  - `cloud-init.yaml`: packages and Tailscale's apt repo; a 1 GB `/swapfile` in `/etc/fstab`; user `forge`; `uv`
    installed for `forge`; Vortex-MCP cloned to `/opt/forge/vortex` at `VORTEX_REF` with `uv sync --extra unisync`;
    bundle files copied to `/opt/forge/`; units installed but **not** started.
  - `forge.env.example`: one `NAME=` per line. Secret names: `TS_AUTHKEY`, `VORTEX_STORE_URL`, `UNISYNC_GATEWAY_URL`,
    `UNISYNC_VORTEX_TOKEN`. Non-secret pins: `VORTEX_REF`, `VORTEX_SCHEMA=forge`, `VORTEX_AUTH_MODE=apikey`,
    `VORTEX_AGENT_IDS=` (the six IDs), `VORTEX_HOST=127.0.0.1`, `VORTEX_PORT=8000`. `VORTEX_PUBLIC_URL`,
    `VORTEX_ALLOWED_HOSTS` and `VORTEX_ALLOWED_ORIGINS` are derived by `forge-setup.sh` from the Funnel name.
  - `forge-setup.sh [--dry-run]`: idempotent; prompts with `read -s` only for values missing from
    `/etc/forge/forge.env`; runs `tailscale up --authkey`, then `tailscale funnel --bg --set-path /mcp
    http://127.0.0.1:8000/mcp` (exact flags confirmed against `tailscale funnel --help` in Task 4); writes the derived
    URLs; enables and starts both units; never prints a secret. `--dry-run` with `FORGE_ROOT=<dir>` prints planned
    actions and changes nothing.
  - Units: `User=forge`, `EnvironmentFile=/etc/forge/forge.env`, `Restart=always`, `RestartSec=5`,
    `MemoryMax=600M` (Vortex) and `MemoryMax=200M` (relay), `NoNewPrivileges=yes`, `ProtectSystem=strict`.
  - `forge-check.sh`: PASS/FAIL lines for the Task 4 checks; exit non-zero on any FAIL.

- [ ] **Step 1: Write the failing tests** (standard library only): `test_bundle_files_exist`;
  `test_env_example_has_names_not_values`; `test_no_credential_shaped_strings` (no `://user:pass@`, no assignment of a
  value to a secret name, no `tskey-`, no `vtx_` followed by key characters, in any bundle file);
  `test_services_are_hardened` (the unit settings listed above); `test_only_mcp_is_published` (the only
  `tailscale funnel` path is `/mcp`); `test_swap_is_configured`; `test_scripts_parse` (`bash -n`);
  `test_setup_dry_run_touches_nothing`.
- [ ] **Step 2: Run** `python -m unittest tests.test_forge_vm -v`. Expected: FAIL (files missing).
- [ ] **Step 3: Write the bundle.**
- [ ] **Step 4: Run** the three repo checks in `AGENTS.md`. Expected: PASS.
- [ ] **Step 5: Commit** `feat(deploy): Forge VM bundle for a Google Cloud e2-micro`.

### Task 3: Owner runbook `docs/FORGE_VM.md` (Bastion)

**Files:** Create `docs/FORGE_VM.md`; modify `README.md` (one row in "What's here"). Test:
`tests/test_forge_vm.py::test_runbook_covers_every_owner_step`.

The runbook is numbered, and every step ends with "you'll see …":
1. **Tailscale:** free account; enable MagicDNS, HTTPS certificates and Funnel; create a one-off pre-approved auth key.
2. **Supabase:** from your PC run `python -m vortex migrate` with the admin connection; create login role `vortex_app`
   as a member of `vortex_writer`; confirm the `vortex` ledger token allows `memory.write`.
3. **Agent keys:** from your PC, `python -m vortex keys add <agent>` for each of the six agents; store each key in that
   agent's own secret store, never in chat or a repo.
4. **Google Cloud:** Compute Engine → Create instance: `e2-micro`, region `us-central1` (or `us-west1` / `us-east1`),
   Ubuntu 24.04 LTS x86_64, 30 GB standard persistent disk, no external HTTP/HTTPS firewall rules; under Advanced →
   Management → Metadata add key `user-data` with the contents of `cloud-init.yaml`. Note the free-tier limits.
5. **On the VM** (SSH from the console): `sudo /opt/forge/forge-setup.sh`, answer the prompts.
6. **Close public SSH:** remove the default SSH firewall rule; use `tailscale ssh` from then on.

- [ ] **Step 1: Write the failing test** (each heading present; no secret values). **Step 2: Run.** Expected: FAIL.
- [ ] **Step 3: Write the runbook.** **Step 4: Run** the repo checks. Expected: PASS.
- [ ] **Step 5: Commit** `docs(deploy): Forge VM runbook`.

### Task 4: 🔒 Build the VM and verify (owner and Claude together)

- [ ] **Step 1:** Owner follows runbook steps 1–5; Claude reads the output at each step.
- [ ] **Step 2:** `sudo /opt/forge/forge-check.sh`. Expected, all PASS: `vortex healthy`; `relay running`;
  `funnel publishes only /mcp`; `claude-1 key accepted`; `hermes-1 key accepted`; `round trip claude-1 -> hermes-1`;
  `outbox relayed to ledger`; `second vortex refused`; `revoked key refused within 30 s` (issue and revoke a throwaway
  key); `no secrets in journal`; `swap active`.
- [ ] **Step 3:** `sudo reboot`; after 3 minutes rerun the check. Expected: all PASS.
- [ ] **Step 4:** Rerun `sudo /opt/forge/forge-setup.sh`. Expected: no prompts, "nothing to change", check still passes.
- [ ] **Step 5:** Owner closes public SSH (runbook step 6). Claude ticks P1b in `docs/BACKLOG.md` with the check output.

## Done when (P1b exit)

Claude and Hermes each complete a Vortex message round trip with their own API keys against the Supabase-backed store
at `https://<forge-host>.ts.net/mcp`; the relay writes those events to the ledger; a revoked key stops working within
30 seconds; the VM survives a reboot and a re-run of setup unchanged.

## Out of scope

Dashboard login (P1c); Keycloak/OAuth (later upgrade); an owned domain with a Cloudflare Tunnel (later upgrade: one URL
per agent changes); hosting any agent other than Vortex on the VM.
