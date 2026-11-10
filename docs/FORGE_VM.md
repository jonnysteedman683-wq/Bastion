# Forge VM runbook

How to build the Forge VM: one Google Cloud Always Free e2-micro that runs Vortex (the Forge's MCP server) and
publishes it at `https://<name>.ts.net/mcp` through Tailscale Funnel. Every agent connects with its own API key.

- You (the owner) do the steps below. They take about half an hour.
- Claude does the Supabase side through the Supabase connector, and reads the output with you at each step.
- **No secret ever goes into a chat or a repository.** The database password and the ledger token are made on the
  VM and never leave it. What you paste to Claude are two hashes, which are safe to share.
- The VM holds no state. If it is ever lost, repeat this page; nothing is lost with it.

What each file in the bundle does: [deploy/forge-vm/README.md](../deploy/forge-vm/README.md).

## Before you start

- The pull request that adds `deploy/forge-vm/` is merged into `main` (step 3 uses `main`).
- `VORTEX_REF` in `deploy/forge-vm/forge.env.example` names the Vortex-MCP commit to run. It is already set to the
  commit that added per-agent API keys; nothing to do unless you want a newer Vortex.
- Vortex-MCP is a private repository. Either make it public, or have a read-only GitHub token ready for step 4:
  github.com → Settings → Developer settings → Fine-grained tokens → only the Vortex-MCP repository →
  Repository permissions → Contents: Read-only → expiry 7 days.

## 1. Tailscale: account, Funnel, and a one-off key

1. Create a free account at [tailscale.com](https://tailscale.com) and install Tailscale on your PC.
2. In the admin console, open **DNS**: turn on **MagicDNS**, then turn on **HTTPS Certificates**.
3. Open **Access controls** and add Funnel to the policy (the page offers an "Add Funnel to policy" button; it adds
   a `nodeAttrs` entry with `funnel`). Save.
4. Open **Settings → Keys → Generate auth key**: Reusable **off**, Ephemeral **off**, Pre-approved **on**, expiry
   1 day. Keep the page open; you will type the key into the VM in step 4. Do not paste it anywhere else.

You'll see the new key listed under Keys as "one-off", and MagicDNS and HTTPS shown as enabled on the DNS page.

## 2. Supabase: what Claude has done, and what waits for the VM

Nothing for you to type here. Claude works on project `dcjqwnapxgocitvntzzm` through the Supabase connector.

Already done:

- The `forge` schema and its tables, including `agent_keys` (the same thing `python -m vortex migrate` creates).
- The `vortex_writer` role: it can write `forge.*`, cannot log in, and has no password.

Done during step 4, when the VM prints its two hashes and you paste them to Claude:

- Claude creates the login role `vortex_app`, a member of `vortex_writer`, from the password **verifier**. Postgres
  stores exactly that verifier, so the password itself never leaves the VM.
- Claude registers the ledger token `vortex` from its **SHA-256**, and confirms it allows `memory.write`.

To check it yourself: Supabase → Database → Roles lists `vortex_writer` (and `vortex_app` after step 4); Table
Editor → schema `forge` lists `agent_keys`.

If the connector is ever unavailable, the same can be done by hand from your PC, in the Supabase SQL editor or with
`psql`. Read the admin connection string into the environment without showing it, then migrate:

```bash
read -rs VORTEX_MIGRATE_URL && export VORTEX_MIGRATE_URL
python -m vortex migrate
```

and, with the verifier the VM printed in step 4 in place of `<verifier>`:

```sql
CREATE ROLE vortex_app LOGIN IN ROLE vortex_writer PASSWORD '<verifier>';
```

The ledger token is registered by storing the SHA-256 the VM printed as the hash of the token named `vortex`; ask
Claude for the exact statement for the ledger's token table rather than guessing it.

You'll see, in Supabase → Database → Roles, `vortex_writer` now, and `vortex_app` once step 4 is done.

## 3. Google Cloud: create the VM from Cloud Shell

Open [deploy/forge-vm/cloudshell-oneliner.md](../deploy/forge-vm/cloudshell-oneliner.md), copy its one block, paste
it into [Google Cloud Shell](https://shell.cloud.google.com/) and press Enter. You run this, not Claude: agents do
not call `gcloud` in this repository.

The block downloads this repository and runs `create-vm.sh`, which creates exactly one VM: `e2-micro`, zone
`us-central1-a`, Ubuntu 24.04 LTS, a 30 GB standard disk, label `app=forge`, and no HTTP or HTTPS firewall tags.
It refuses any zone outside `us-west1`, `us-central1` and `us-east1`, and has no option for a bigger machine or
disk. It contains no secret.

Free-tier limits to know: one e2-micro per billing account in those three regions, 30 GB of standard disk, and
about 1 GB a month of outbound traffic. Agent traffic stays far inside that. A second VM would be billed.

You'll see `created 'forge-vm' in us-central1-a`. Run it twice and you'll see `instance 'forge-vm' already
exists` instead; nothing changes.

## 4. On the VM: run setup and hand Claude the two hashes

1. In the Google Cloud console open **Compute Engine → VM instances** and click **SSH** next to `forge-vm`.
2. Wait for first boot to finish (about five minutes after step 3):

   ```bash
   cloud-init status --wait
   ```

   It prints `status: done`.
3. Run setup:

   ```bash
   sudo /opt/forge/forge-setup.sh
   ```

   It asks, in this order:
   - **FORGE_PG_HOST**: the Supabase "Session pooler" host. Supabase → Connect → Session pooler; copy only the host,
     which looks like `aws-0-<region>.pooler.supabase.com`. It is not a secret.
   - **GITHUB_TOKEN** (only if Vortex-MCP is still private): the read-only token from "Before you start". Typing is
     hidden. It is used once and not stored.
   - **TS_AUTHKEY**: the one-off Tailscale key from step 1. Typing is hidden. It is used once and not stored.
4. Setup then prints two lines under **"paste these two hashes to Claude; they are safe to share"**: a line
   starting `SCRAM-SHA-256$4096:` and a 64-character hex line. Copy both into the chat with Claude.
5. Claude registers them through the Supabase connector (step 2) and tells you so. Only then press **Enter** in
   the VM window. Setup starts Vortex and its relay.
6. In the Tailscale admin console → **Machines** → `forge-vm` → **Disable key expiry**, so the VM does not drop off
   the tailnet after six months.

If you close the window before pressing Enter, nothing is started; run the same command again and it picks up
where it stopped, showing the hashes again for the same password. To see what setup would do without doing it:
`sudo /opt/forge/forge-setup.sh --dry-run`.

You'll see `done. Vortex is at https://forge-vm.<your-tailnet>.ts.net/mcp`. Run setup again any time and you'll
see no questions and `nothing to change`.

## 5. Agent keys: issue one per agent, then run the check

Still in the VM window, issue a key for each agent: `claude-1`, `hermes-1`, `hermes-2`, `sebastian-1`,
`lestinxor-1`, `jules-1`.

```bash
sudo /opt/forge/forge-keys.sh claude-1
```

Each key is printed **once**. Put it straight into that agent's own secret store, as the bearer key for the
Vortex MCP server at the address from step 4. Never paste a key into a chat or a repository. Vortex keeps only a hash, so a lost key cannot be shown again: issue a new one.

- See who holds keys (never the keys): `sudo /opt/forge/forge-keys.sh --list`
- Revoke every key an agent holds (it stops working within 30 seconds): `sudo /opt/forge/forge-keys.sh --revoke hermes-2`

Then run the check. It asks, hidden, for the `claude-1` and `hermes-1` keys you just issued. `--revoke-agent` names
one agent whose keys the check may revoke to prove revocation works; use `jules-1` and issue Jules a new key
straight afterwards.

```bash
sudo /opt/forge/forge-check.sh --revoke-agent jules-1
sudo /opt/forge/forge-keys.sh jules-1
```

Finally prove it survives a restart: `sudo reboot`, wait three minutes, SSH in again and run the check again.

You'll see one `PASS` line for each of the eleven checks and `all checks passed`. Any `FAIL` line says what is
wrong; show it to Claude.

## 6. Close public SSH: use Tailscale from now on

1. From your PC, check that Tailscale SSH works **before** closing anything:

   ```bash
   tailscale ssh ubuntu@forge-vm
   ```

2. In the Google Cloud console open **VPC network → Firewall** and delete the rule `default-allow-ssh` (and
   `default-allow-rdp`, which nothing uses). This is done in the console, by you.
3. From then on reach the VM only with `tailscale ssh`. The console's SSH button will stop working; that is the
   point.

You'll see the console's SSH button time out, while `tailscale ssh ubuntu@forge-vm` still opens a shell.

## Later

- **Add or replace a key:** step 5. **Revoke:** `forge-keys.sh --revoke <agent>`.
- **Newer Vortex:** change `VORTEX_REF`, merge, then on the VM replace `/opt/forge/forge.env.example` with the new
  file and run `sudo /opt/forge/forge-setup.sh`. It fetches the new commit and restarts the services.
- **Lost VM:** delete it in the console, then repeat steps 3 to 6 with a new Tailscale key. The new VM makes a new
  password and token, so Claude registers two new hashes. Agent keys live in Supabase and keep working.
- **Something leaked:** a leaked agent key is revoked with `forge-keys.sh --revoke`. If `/etc/forge/forge.env`
  itself may have been read, rebuild the VM: that replaces the database password and the relay token.
