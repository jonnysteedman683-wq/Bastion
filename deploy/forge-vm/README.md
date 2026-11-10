# Forge VM bundle

Everything needed to run Vortex (the Forge's MCP server) on one Google Cloud Always Free e2-micro VM, published
through Tailscale Funnel. The owner's step-by-step guide is [docs/FORGE_VM.md](../../docs/FORGE_VM.md); this page
says what each file is for.

**The VM holds no state.** Forge data lives in Supabase. If the VM is lost, build a new one the same way.

| File | Runs where | What it does |
| --- | --- | --- |
| `cloudshell-oneliner.md` | Owner, Google Cloud Shell | The one block the owner pastes to create the VM |
| `create-vm.sh` | Owner, Google Cloud Shell | Creates the VM with the free-tier settings; refuses anything else; safe to run again; `--dry-run` |
| `cloud-init.yaml` | VM, first boot | Packages, 1 GB swap, user `forge`; then fetches and runs `forge-bootstrap.sh` |
| `forge-bootstrap.sh` | VM, first boot | Fetches this bundle from instance metadata; installs Tailscale and `uv`; installs the units; starts nothing |
| `forge-setup.sh` | VM, by the owner | Generates the secrets on the VM, joins Tailscale, publishes `/mcp`, starts the services; `--dry-run` |
| `forge-keys.sh` | VM, by the owner | Issues, revokes and lists agent API keys |
| `forge-check.sh` | VM, by the owner | PASS/FAIL checks; non-zero exit on any FAIL |
| `scram.py` | VM | Makes a random secret, and the two hashes that are safe to share (standard library only) |
| `vortex.service`, `vortex-relay.service` | VM, systemd | Vortex and its ledger relay, as user `forge`, memory-capped, restarted if they stop |
| `forge.env.example` | VM | Setting names and non-secret pins. Never a secret value |

## How secrets are handled

- No secret is in this repository, in instance metadata, or in cloud-init. Instance metadata carries only the files
  in this folder, which hold names and non-secret pins.
- The database password and the ledger relay token are **generated on the VM** and never leave it.
  `forge-setup.sh` prints only a SCRAM-SHA-256 verifier of the password and a SHA-256 of the token. Those two hashes
  are safe to share: Claude registers them in Supabase, and neither can be turned back into the secret.
- The Tailscale auth key is typed at a hidden prompt, handed to `tailscale` through a root-only file in memory
  (`/run`), used once, and deleted. It is not stored.
- Secrets live only in `/etc/forge/forge.env` (mode `0600`, owner `root`), which systemd reads for the two services.
- Agent API keys are printed once, by Vortex, to the owner's terminal. Vortex keeps only their hashes.
- No script prints a secret, traces its commands, or puts a secret on a command line.

## Things to know before building the VM

- **`VORTEX_REF`** in `forge.env.example` pins the Vortex-MCP commit. It currently names `main` before `apikey` mode
  exists. It must be bumped to the apikey merge commit before the VM is built.
- **Vortex-MCP is a private repository**, so the VM cannot fetch it on its own. `forge-setup.sh` tries without
  credentials and, if that fails, asks once (hidden) for a read-only GitHub token: fine-grained, that repository
  only, "Contents: read". The token is used for that fetch and not stored. Making the repository public removes
  the prompt.
- **The Funnel flags** in `forge-setup.sh` (`funnel_on`, near the top) and the shape of
  `tailscale funnel status --json` are confirmed on the VM in plan Task 4. If Tailscale has changed them, that
  function and `funnel_state` are the two places to adjust.
- **Free tier:** one e2-micro in `us-west1`, `us-central1` or `us-east1`, a 30 GB standard disk, and about 1 GB a
  month of outbound traffic. `create-vm.sh` refuses other zones and has no option for another machine type or disk.

## Tests

`tests/test_forge_vm.py` (standard library only) runs every script against stubs: `gcloud`, `tailscale`,
`systemctl`, `git` and `uv` are replaced by fakes first on `PATH`, and `forge-setup.sh` runs inside a throwaway
`FORGE_ROOT`. It checks, among other things, that a dry run changes nothing, that a second run changes nothing,
that no secret reaches the output or a command line, and that only `/mcp` is published.
