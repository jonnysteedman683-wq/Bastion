# Notify: phone alerts through ntfy

`python -m tools.notify send` pushes one message to your phone. It is the alert half of backlog G1d:
anything that needs to wake you (a cap breach, a halted agent, a failed deploy) calls it.

| Piece | What it does |
| --- | --- |
| `python -m tools.notify send --message TEXT [--title T] [--priority P] [--tags a,b] [--click URL]` | One POST to your ntfy topic. Priorities: min, low, default, high, urgent |
| `NTFY_TOPIC` | The topic the phone subscribes to. Secret: never printed, never in arguments |
| `NTFY_URL` | Optional. Default `https://ntfy.sh`. https only (plain http for localhost) |
| `NTFY_TOKEN` | Optional access token for a protected topic or a self-hosted server |

Exit codes: 0 sent, 1 server or network failed, 2 bad configuration (nothing sent).

## One-time setup (owner)

1. Install the **ntfy** app: [Android](https://play.google.com/store/apps/details?id=io.heckel.ntfy) or [iPhone](https://apps.apple.com/app/ntfy/id1625396347).
2. In the app tap **+**, and subscribe to a long random topic name (for example 30 random letters and digits).
   On public ntfy.sh anyone who knows the topic can read and send to it, so treat the name as a secret.
3. Add it as `NTFY_TOPIC` wherever alerts are sent from: your PC's environment, and later the repository secret for the
   scheduled cap check (G1d-2). Names only in this repository, never the value.
4. Test from your PC:
   ```bash
   NTFY_TOPIC=<your topic> python -m tools.notify send --title "Bastion" --message "Phone is connected"
   ```

## Not done yet

G1d-2 (🔒) runs the cap check on a schedule, halts an agent over its cap and calls this tool. It needs a workflow under
`.github/`, which agents may not change.
