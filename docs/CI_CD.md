# CI/CD

`.github/workflows/ci-cd.yml` adds two GitHub Actions jobs on top of the
existing deployment flow in [DEPLOYMENT.md](DEPLOYMENT.md) — it doesn't
replace or change how the app runs, it automates the same manual steps that
doc already describes.

## What runs, and when

| Job      | Trigger                          | What it does |
|----------|-----------------------------------|--------------|
| `test`   | every push + every PR to `main`   | Installs deps, syntax-checks `server.py`/`engine/`/`models/`, validates `docker-compose.yml`, and does a build-only `docker build` to catch a broken `Dockerfile` before merge. No credentials needed, no infrastructure touched. |
| `deploy` | push to `main` only, after `test` passes | SSHes into your existing Compute Engine VM (docs/DEPLOYMENT.md Option B) and runs `git pull && docker compose up -d --build` — the same commands you'd run by hand. Does **not** create, resize, or delete any GCP resource. |

Nothing deploys on pull requests or other branches — only a direct push to
`main` triggers `deploy`, and only after `test` is green.

## One-time setup (you need to do this before `deploy` will work)

1. **A Compute Engine VM already running the app**, per
   [DEPLOYMENT.md Option B](DEPLOYMENT.md#option-b--compute-engine-vm-if-youd-rather-run-it-standalone) —
   this pipeline redeploys an existing VM, it doesn't create one.
2. **A GCP service account** with SSH access to that VM:
   ```bash
   gcloud iam service-accounts create quintrix-ci \
     --display-name="Quintrix CI/CD"
   gcloud projects add-iam-policy-binding YOUR_PROJECT_ID \
     --member="serviceAccount:quintrix-ci@YOUR_PROJECT_ID.iam.gserviceaccount.com" \
     --role="roles/compute.osAdminLogin"
   gcloud iam service-accounts keys create key.json \
     --iam-account=quintrix-ci@YOUR_PROJECT_ID.iam.gserviceaccount.com
   ```
3. **Confirm the VM's firewall allows SSH (tcp:22)** from GitHub's runner IPs —
   the default GCP `default-allow-ssh` rule covers this unless you've
   restricted it; if you have, allow `0.0.0.0/0` on port 22 or switch the
   workflow's `gcloud compute ssh` step to `--tunnel-through-iap` with IAP
   configured instead.
4. **Add these repo secrets** (Settings → Secrets and variables → Actions):

   | Secret | Value |
   |---|---|
   | `GCP_SA_KEY` | contents of `key.json` from step 2 |
   | `GCP_PROJECT_ID` | your GCP project ID |
   | `GCP_VM_NAME` | the VM's instance name (e.g. `quintrix-itso`) |
   | `GCP_VM_ZONE` | the VM's zone (e.g. `us-central1-a`) |

Until these secrets exist, `deploy` will simply fail on auth — `test` runs
fine regardless, so PRs and branch pushes are unaffected either way.

## R3D-18 / GPU deploys

This pipeline redeploys in place — it doesn't resize the VM or provision a
GPU instance. If you've opted into the R3D-18 backend and want the larger
CPU machine type or a T4 GPU described in
[DEPLOYMENT.md](DEPLOYMENT.md#optional--r3d-18-alternative-action-recognition-backend),
that's a one-time manual `gcloud compute instances create`/`set-machine-type`
step — intentionally not automated, since resizing requires stopping the
instance (real downtime), which shouldn't happen silently on every push.
