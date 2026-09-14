# Cost controls and the free-tier question

> **Short answer: this application cannot run inside GCP's Always Free tier.**
> Not for want of tuning — the arithmetic does not fit. This document shows the
> numbers, then gives you the guardrails for a billed deployment and a genuinely
> free alternative that *does* fit.

## Why it does not fit

| Always Free allowance | What this app needs | Verdict |
|---|---|---|
| **Compute Engine:** 1× `e2-micro` — **1 GB RAM**, shared vCPU, in `us-west1`/`us-central1`/`us-east1` | PyTorch resident (~1 GB) **plus** R3D-18 (33M params) **plus** 2× YOLOv8s (11M params each) **plus** 48-frame decode buffers → **~2.5–4 GB** | ❌ OOM on model load |
| **Artifact Registry:** 0.5 GB storage | CPU-only torch wheels alone are ~800 MB installed; with OpenCV, ffmpeg and 170 MB of weights the image is **~4–6 GB** | ❌ ~10× over |
| **Cloud Build:** 2,500 build-min/month | ~8–12 min per image build | ✅ fits |
| **Cloud Storage:** 5 GB | Weights (170 MB) + archive growth | ⚠️ archive outgrows it |
| **Egress:** 1 GB/month from North America | Video playback is the whole point of the UI | ⚠️ a few hours of review exceeds it |

There is also a hard gate before any of that: **Compute Engine requires a
billing account to be attached, even to use the free `e2-micro`.** "No billing
account at all" and "a GCP VM" are mutually exclusive.

**Realistic minimum on GCP:** an `e2-medium` (4 GB) is marginal and an
`e2-standard-4` (16 GB, what this project previously used) is comfortable —
roughly **$25–100/month** depending on machine and uptime.

---

## If you do enable billing: hard guardrails

`scripts/gcp-guardrails.sh` applies all of the following. It is written to be
run once, and is safe to re-run.

1. **A budget with a hard cap** and alerts at 50 / 90 / 100 %.
2. **Budget → Pub/Sub → Cloud Function** that, on breaching the cap,
   **stops the VM** — the auto-destroy behaviour, implemented as *stop* rather
   than *delete* so the persistent disk (and therefore the footage) survives.
3. **A daily auto-shutdown schedule**, so an instance left running overnight
   cannot quietly accrue cost.
4. **Artifact Registry cleanup policy** — keep the 3 most recent images, delete
   the rest, which is what stops registry storage growing without bound.

> The function **stops**, it does not **delete**. Deleting the instance would
> destroy the boot disk and every recording held in the Tier-3 review queue.
> A stopped VM costs only its disk (~$0.04/GB/month), which for a 30 GB disk is
> about **$1.20/month** — near-zero, and recoverable with one `gcloud` command.
> If you genuinely want deletion, set `GUARDRAIL_ACTION=delete`, but understand
> that it is not reversible.

```bash
./scripts/gcp-guardrails.sh --project YOUR_PROJECT --budget-usd 5 --vm quintrix-itso-vm --zone us-central1-b
```

---

## A free deployment that actually works

If the requirement is *"publicly reachable, genuinely £0, no card"*, the honest
recommendation is **Hugging Face Spaces**, not GCP:

| | HF Spaces (free CPU) | GCP Always Free |
|---|---|---|
| RAM | **16 GB** | 1 GB |
| vCPU | 2 | shared/burst |
| Disk | 50 GB | 30 GB |
| Card required | **no** | **yes** |
| Runs this app | **yes** | no |

It is built for exactly this — an ML demo with a web UI and chunky model
weights. Caveats worth knowing: the filesystem resets on rebuild (so treat it
as a demo, not a system of record), and free Spaces sleep after inactivity.

Other genuinely-free options that can carry the workload: **Oracle Cloud Always
Free** (4 ARM cores / 24 GB RAM, free indefinitely, but a card is required for
identity verification) and self-hosting on any machine you already own.

---

## Reading your current spend

```bash
gcloud billing projects describe "$PROJECT"                 # is billing attached?
gcloud billing budgets list --billing-account="$BILLING_ID" # existing caps
gcloud compute instances list --format='table(name,machineType.basename(),status)'
```

A **stopped** instance still bills for its disk. To stop paying entirely, you
must delete the instance *and* its disks — which destroys the archive. Snapshot
first if the footage matters.
