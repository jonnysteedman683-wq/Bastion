# Vortex MCP: connecting Claude to Bastion

Vortex is the Forge's MCP server ([FORGE_VM.md](FORGE_VM.md)). Connecting Claude Code to it gives Claude
Vortex's tools (inbox, messages to other agents, tasks, memory) under its own key, `claude-1`.

The repo's [`.mcp.json`](../.mcp.json) declares the server. It holds no address and no key: Claude Code fills both
from the environment when it starts.

## Connect

1. **Issue the key** on the VM (FORGE_VM.md step 5), if `claude-1` has none yet:
   `sudo /opt/forge/forge-keys.sh claude-1`. It is printed once.
2. **Set two environment variables** where Claude Code runs. Never in the repo, never in a chat:
   - `VORTEX_MCP_URL`: `https://forge-vm.<your-tailnet>.ts.net/mcp`, the address setup printed.
   - `VORTEX_API_KEY`: the `claude-1` key (`vtx_...`).

   On your PC, put them in your shell profile or a git-ignored `.env.local` you source. In a Claude Code cloud
   environment, set `VORTEX_MCP_URL` as an environment variable and store the key as a network secret for the
   Vortex host (header `Authorization`, prefix `Bearer`), never as a plain variable.
3. **Check** the connection before starting Claude:
   ```bash
   python -m tools.vortex check
   ```
   You'll see `connected to vortex as claude-1; N tools: ...`. Exit 2 means a variable is missing or malformed
   (nothing was sent); exit 1 means Vortex or the network refused, with the reason. The key is never printed.
4. **Start Claude Code** in the repo and approve the project server `vortex` when asked. `/mcp` then lists it as
   connected, and its tools show as `mcp__vortex__*`.

## Rotate or revoke

- Lost or leaked key: `sudo /opt/forge/forge-keys.sh --revoke claude-1`, then issue a new one and update
  `VORTEX_API_KEY`. The old key stops working within 30 seconds.
- Another Claude (a second PC, a cloud session) gets its own agent id and key; never share `claude-1`.
