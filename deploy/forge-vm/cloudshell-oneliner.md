# Create the Forge VM from Google Cloud Shell

Open [Google Cloud Shell](https://shell.cloud.google.com/) signed in as the owner, paste this one block and press
Enter. It holds no secret.

```bash
rm -rf ~/bastion-forge && git clone --depth 1 https://github.com/jonnysteedman683-wq/Bastion ~/bastion-forge && GCP_PROJECT=bastion-471203 bash ~/bastion-forge/deploy/forge-vm/create-vm.sh
```

You'll see `created 'forge-vm' in us-central1-a`. If you run it again you'll see
`instance 'forge-vm' already exists`, and nothing changes.

What it does: takes a fresh copy of this (public) repository, then runs `create-vm.sh`, which creates one e2-micro
VM with the free-tier settings and hands it `cloud-init.yaml` and the rest of this folder as instance metadata.

- To see the exact `gcloud` command without running it, add ` --dry-run` to the end of the line.
- Cloud Shell is already signed in to Google Cloud, so there is nothing to install or log in to.
- If Google asks you to "Authorize" Cloud Shell, click Authorize.
- If it says the Compute Engine API is not enabled, open the link it prints, click Enable, and paste the block again.
- The block uses the `main` branch, so the pull request that adds or changes this folder must be merged first.
