# Forge P1b: the Forge VM Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A fresh Oracle Cloud Always Free ARM VM, built from one pasted cloud-init file plus one setup command, serves
Vortex (on Supabase, from P1a) and Keycloak publicly over Tailscale Funnel, so Claude and Hermes each connect with
their own identity.

**Architecture:** Everything lives in `Bastion/deploy/forge-vm/`. Cloud-init installs Docker, Tailscale and `uv`,
creates an unprivileged `forge` user, and checks out Vortex-MCP at a pinned commit. The owner then runs
`forge-setup.sh` once on the VM: it joins the tailnet, writes secrets to a root-only env file, starts Keycloak (Docker,
data in a Supabase `keycloak` schema) and Vortex plus its relay (systemd), and publishes exactly two paths through
Funnel. **The VM holds no state**: rebuilding it is "create a VM, paste the file, run the script".

**Tech Stack:** Ubuntu 24.04 aarch64, cloud-init, Docker Engine, `quay.io/keycloak/keycloak:26.8.0`, Tailscale
(Funnel), `uv`, systemd, Vortex-MCP (P1a), Supabase Postgres. Tests: Python standard library only (Bastion rule).

**Spec:** `docs/superpowers/specs/2026-11-10-forge-design.md` (phase P1; D11 as amended 2026-11-10: Tailscale Funnel,
not a Cloudflare Tunnel; "Hosting and credentials").

**Repos:** files in Bastion `deploy/forge-vm/` (not guarded; `infra/` is). Vortex-MCP is consumed unchanged.

## Global Constraints

- No secret, token, password or connection string is ever committed; templates hold variable **names** only.
- Secrets on the VM live only in `/etc/forge/forge.env`, mode `0600`, owner `root`; services read it via
  `EnvironmentFile=`. Nothing echoes them.
- Vortex and the relay run as user `forge` (no sudo); Keycloak runs in Docker bound to `127.0.0.1` only.
- Public through Funnel: **only** `/mcp` (Vortex) and `/auth/realms/forge/` (Keycloak token endpoints). The Keycloak
  admin console is reachable only inside the tailnet.
- The VM opens no inbound ports except SSH during first setup; the runbook closes SSH to the internet afterwards.
- Pinned versions: Keycloak `26.8.0`; Vortex-MCP at the P1a merge commit, recorded in `forge.env.example` as
  `VORTEX_REF`.
- Agent IDs: `claude-1`, `hermes-1`, `hermes-2`, `sebastian-1`, `lestinxor-1`, `jules-1`; OAuth client IDs equal agent
  IDs; plus `vortex-introspection`.
- Supabase connections use the direct or session-mode connection string (P1a requirement), never the
  transaction-mode pooler.

## Review Focus

1. **Re-running `forge-setup.sh`** on a working VM must change nothing and break nothing (idempotent), including not
   regenerating client secrets agents already hold. Test in Task 1 (dry-run mode).
2. **A secret typed at the prompt** must never appear in shell history, logs, `ps`, or the Funnel config. Test in
   Task 1 (static checks) and Task 3 (on the VM).
3. **The Keycloak admin console** must not be reachable from the public internet. Checked in Task 3.
4. **The VM reboots** (Oracle maintenance): everything comes back without the owner. Checked in Task 3.
5. **Oracle "out of capacity"** when creating the instance: the runbook gives the retry and region fallback. Task 2.

---

### Task 1: The `deploy/forge-vm/` bundle and its static tests

**Files:**
- Create: `deploy/forge-vm/cloud-init.yaml`, `deploy/forge-vm/forge-setup.sh`, `deploy/forge-vm/forge-check.sh`,
  `deploy/forge-vm/compose.yaml`, `deploy/forge-vm/vortex.service`, `deploy/forge-vm/vortex-relay.service`,
  `deploy/forge-vm/realm-forge.json`, `deploy/forge-vm/forge.env.example`, `deploy/forge-vm/README.md`
- Test: `tests/test_forge_vm.py`

**Interfaces:**
- Produces:
  - `forge.env.example`: one `NAME=` per line, no values except non-secret pins (`VORTEX_REF`, `KEYCLOAK_IMAGE`,
    `VORTEX_SCHEMA=forge`, `FORGE_HOSTNAME=` left blank). Names: `TS_AUTHKEY`, `VORTEX_STORE_URL`,
    `KC_DB_URL`, `KC_DB_USERNAME`, `KC_DB_PASSWORD`, `KC_BOOTSTRAP_ADMIN_USERNAME`, `KC_BOOTSTRAP_ADMIN_PASSWORD`,
    `VORTEX_OAUTH_CLIENT_SECRET`, `UNISYNC_GATEWAY_URL`, `UNISYNC_VORTEX_TOKEN`.
  - `forge-setup.sh [--dry-run]`: idempotent; prompts (with `read -s`) only for values missing from
    `/etc/forge/forge.env`; never prints a secret; in `--dry-run` prints the actions it would take and exits 0 without
    touching the system.
  - Funnel layout (in `forge-setup.sh`): `tailscale funnel --bg --set-path /mcp http://127.0.0.1:8000/mcp` and
    `tailscale funnel --bg --set-path /auth/realms/forge http://127.0.0.1:8080/auth/realms/forge`; admin console via
    `tailscale serve --bg --https=8443 http://127.0.0.1:8080` (tailnet only). Exact flags are checked against
    `tailscale funnel --help` on the VM in Task 3.
  - `realm-forge.json`: realm `forge`, confidential `client_credentials` clients for the seven IDs above, no secrets
    in the file (Keycloak generates them on import); service accounts enabled; standard flow disabled.
  - `forge-check.sh`: prints PASS/FAIL lines for the checks in Task 3 and exits non-zero on any FAIL.

- [ ] **Step 1: Write the failing tests** in `tests/test_forge_vm.py` (standard library only):
  `test_bundle_files_exist`; `test_env_example_has_names_not_secret_values` (every line is `NAME=` or a pinned
  non-secret key from the list above); `test_no_credential_shaped_strings` (no `://user:pass@`, no `password=` with a
  value, no `tskey-`, no `sb_secret`, in any bundle file); `test_realm_clients_match_agent_ids` (parsed JSON client IDs
  == the seven IDs; no `secret` keys); `test_keycloak_image_is_pinned` (`compose.yaml` names exactly
  `quay.io/keycloak/keycloak:26.8.0` and binds `127.0.0.1:8080`); `test_services_run_as_forge_user` (both units have
  `User=forge`, `EnvironmentFile=/etc/forge/forge.env`, `Restart=always`); `test_only_two_public_paths` (the
  `tailscale funnel` lines in `forge-setup.sh` publish exactly `/mcp` and `/auth/realms/forge`);
  `test_scripts_parse` (`bash -n` on both scripts); `test_setup_dry_run_touches_nothing` (run
  `forge-setup.sh --dry-run` with `FORGE_ROOT` pointed at a temp dir: exit 0, temp dir unchanged).
- [ ] **Step 2: Run** `python -m unittest tests.test_forge_vm -v`. Expected: FAIL (files missing).
- [ ] **Step 3: Write the bundle** to satisfy the interfaces above.
- [ ] **Step 4: Run** the three repo checks from `AGENTS.md`. Expected: all pass.
- [ ] **Step 5: Commit** `feat(deploy): Forge VM bundle (cloud-init, setup, Keycloak, Vortex, Funnel)`.

### Task 2: Owner runbook `docs/FORGE_VM.md`

**Files:**
- Create: `docs/FORGE_VM.md`; Modify: `README.md` (one row in "What's here")
- Test: `tests/test_forge_vm.py` (`test_runbook_covers_every_owner_step`)

**Interfaces:**
- Produces a numbered runbook, each step with "you'll see" so the owner knows it worked:
  1. Tailscale: create account (free), enable MagicDNS, HTTPS certificates and Funnel; create a one-off, pre-approved
     auth key tagged `tag:forge`.
  2. Supabase: run `python -m vortex migrate` once from your PC with the admin connection; create login role
     `vortex_app` (member of `vortex_writer`) and role `keycloak_owner` owning schema `keycloak`, each with its own
     generated password; confirm the `vortex` ledger token allows `memory.write`.
  3. Oracle Cloud: create an Always Free account; create instance (Ubuntu 24.04, shape `VM.Standard.A1.Flex`, 2 OCPU,
     12 GB); paste `cloud-init.yaml` under Advanced → Initialization script; add your SSH key. If "out of host
     capacity": retry, switch availability domain, or try another home-region-compatible option.
  4. On the VM: `sudo /opt/forge/forge-setup.sh`, answer the prompts.
  5. Close SSH to the internet in the Oracle security list (use `tailscale ssh` from then on).
  6. Collect each agent's client secret from the tailnet-only Keycloak admin console and give each agent only its own.
- `test_runbook_covers_every_owner_step`: the runbook contains each of the headings above and no secret values.

- [ ] **Step 1: Write the failing test.** **Step 2: Run** it. Expected: FAIL.
- [ ] **Step 3: Write the runbook.** **Step 4: Run** the repo checks. Expected: PASS.
- [ ] **Step 5: Commit** `docs(deploy): Forge VM runbook`.

### Task 3: 🔒 Build the VM and verify (owner and Claude together)

Owner-run steps; Claude walks through them live and reads the output.

- [ ] **Step 1:** Owner follows `docs/FORGE_VM.md` steps 1–4.
- [ ] **Step 2:** On the VM, `sudo /opt/forge/forge-check.sh`. Expected, all PASS:
  `keycloak healthy`; `vortex healthy`; `relay running`; `token claude-1`; `token hermes-1`;
  `vortex round trip (claude-1 -> hermes-1)`; `outbox relayed to ledger`; `second vortex refused`;
  `admin console not public` (from outside the tailnet `https://<host>/auth/admin/` is not served);
  `no secrets in journal` (`journalctl -u vortex -u vortex-relay` contains no value from `/etc/forge/forge.env`).
- [ ] **Step 3:** `sudo reboot`; after 3 minutes rerun `forge-check.sh`. Expected: all PASS.
- [ ] **Step 4:** Rerun `sudo /opt/forge/forge-setup.sh`. Expected: no prompts, "nothing to change", check still
  passes, client secrets unchanged.
- [ ] **Step 5:** Owner closes public SSH (runbook step 5) and hands out client secrets (step 6). Claude records the
  result in `docs/BACKLOG.md` (new item P1b ticked) and the PR body.

## Done when (P1b exit)

Claude and Hermes each obtain a token with their own client credentials and complete a Vortex message round trip
against the Supabase-backed store over `https://<forge-host>.ts.net/mcp`; the relay writes those events to the ledger;
the VM survives a reboot and a re-run of setup unchanged.

## Out of scope

Dashboard login (P1c); moving to an owned domain with a Cloudflare Tunnel (a later one-URL change per agent); hosting
any agent other than Vortex on the VM.
