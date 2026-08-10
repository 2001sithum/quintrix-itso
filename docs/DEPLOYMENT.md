# Deployment

## Local (no Docker)

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
uvicorn server:app --reload
```

Open http://localhost:8000, sign in with `admin` / `admin123`.

> **macOS note:** if the first upload logs `CERTIFICATE_VERIFY_FAILED` during
> model download, that's the standard python.org-on-macOS missing CA bundle —
> already worked around in `server.py` by pointing `SSL_CERT_FILE` at
> `certifi`'s bundle, as long as `certifi` is installed (it's in
> `requirements.txt`).

## Docker / docker-compose

```bash
docker compose up --build
```

Builds from the included `Dockerfile`, exposes port 8000, and persists
`/data` (the SQLite file + archive directory) and `/models` (downloaded model
weights) in named volumes so they survive container restarts.

Docker on macOS runs the container's Linux VM, which can't reach the host
GPU — inference falls back to CPU. Run natively (above) for MPS/CUDA.

## Google Cloud Platform

The app is a single container with **local, stateful storage** (SQLite file
+ archived video/thumbnail/tier files on disk). That shape determines which
GCP compute product actually fits — pick based on how much you're willing to
manage vs. how much persistence you need:

### Option A — Coolify (recommended if you already run a Coolify instance)

Coolify builds straight from the included `Dockerfile`, so no compose changes
are needed:

1. In Coolify: **New Resource → Application → Public/Private Repository**,
   point it at this repo (add it as a GitHub App inside Coolify first if the
   repo is private, so it can redeploy automatically on push).
2. Build pack: **Dockerfile** (auto-detected).
3. Set the exposed port to **8000**.
4. **Storage tab** — add persistent volumes so data survives redeploys:
   - container path `/app` → mount a named volume for the SQLite file, or
   - point `QUINTRIX_DB` / `QUINTRIX_ARCHIVE` env vars at a mounted path if
     you'd rather keep them out of `/app`
5. Deploy. Coolify's own Traefik proxy fronts the container — it does not
   conflict with the container's internal port 8000, and gives you a
   domain/HTTPS for free if one is configured on the resource.

### Option B — Compute Engine VM (if you'd rather run it standalone)

Matches the app's assumptions exactly: one process, one disk, state persists
across restarts, no code changes needed.

```bash
gcloud compute instances create quintrix-itso \
  --project=YOUR_PROJECT_ID \
  --zone=us-central1-a \
  --machine-type=e2-standard-4 \
  --image-family=debian-12 --image-project=debian-cloud \
  --boot-disk-size=50GB \
  --tags=http-server

gcloud compute firewall-rules create allow-quintrix \
  --allow=tcp:8000 --target-tags=http-server
```

Then SSH in, install Docker, `git clone` the repo, and `docker compose up -d
--build`. A persistent disk keeps `/data` and `/models` across VM stop/start;
it does **not** survive deleting the instance unless the disk is detached
first.

### Option C — Cloud Run (serverless, but storage is ephemeral)

Cloud Run containers have no persistent local disk between revisions/scale
events — every restart loses the SQLite database and every archived file.
Only reach for this if you first move state out of the container:

- SQLite → Cloud SQL (Postgres) — requires rewriting `engine/storage_manager.py`'s
  SQL layer (it currently uses SQLite-specific syntax like `INSERT OR IGNORE`)
- Local archive files → a GCS bucket mounted with Cloud Storage FUSE, or
  direct GCS upload/read calls in `models/tier_segmenters.py` and the
  thumbnail/playback endpoints in `server.py`

That's a real backend rewrite, not a deployment config change — worth doing
only if you need autoscaling/pay-per-request. Otherwise Option A is
substantially less work and behaves identically to running it locally.

### Optional — R3D-18 alternative action-recognition backend

`r3d18_best.pt` (a ResNet3D-18 fine-tuned on UCF-Crime's 14-class set) ships
as an **opt-in alternative** to the default X3D-S backend — see
[ARCHITECTURE.md](ARCHITECTURE.md#alternative-action-recognition-backend-opt-in).
It's toggled by config (`action_model_backend=r3d18`), not a code change, so
it runs inside the same container as everything else — no separate service
or deployment is required, and the default (`x3d`) path and resource
footprint are unaffected unless an admin flips the toggle.

If you do enable it, size for the heavier model rather than deploying it
separately:

- **CPU-only (cheapest, matches the default Option B sizing philosophy):**
  R3D-18 at 112×112/16-frame clips runs on CPU but is slower per segment
  than X3D-S. Bump the Compute Engine machine type from `e2-standard-4` to
  `e2-standard-8` (8 vCPU / 32GB) if you expect sustained upload volume with
  the alt backend enabled; `e2-standard-4` is still fine for occasional/manual
  use.
- **GPU (optimum for throughput, not required):** attach a T4 accelerator
  instead of scaling vCPUs — cheaper per unit of throughput than large CPU
  instances for this workload size:
  ```bash
  gcloud compute instances create quintrix-itso-gpu \
    --project=YOUR_PROJECT_ID \
    --zone=us-central1-a \
    --machine-type=n1-standard-4 \
    --accelerator=type=nvidia-tesla-t4,count=1 \
    --maintenance-policy=TERMINATE \
    --image-family=debian-12 --image-project=debian-cloud \
    --boot-disk-size=50GB \
    --tags=http-server
  ```
  Requires the NVIDIA driver + `nvidia-docker`/`--gpus all` on the container
  run step; `torch`/`torchvision` in `requirements.txt` already support CUDA,
  no rebuild needed. `select_device()` (FR60) picks up CUDA automatically —
  this only affects the object/sentiment/action models' device, not the app
  logic.
- **Don't co-locate with Cloud Run (Option C):** R3D-18's load time and
  per-request latency make it a poor fit for Cloud Run's cold-start/scale-to-zero
  model; keep this backend on Option A/B (persistent VM) regardless of which
  option you use for the default X3D-S path.

### Either way

- Build the container once and push it: `gcloud builds submit --tag
  gcr.io/YOUR_PROJECT_ID/quintrix-itso`
- Restrict `ALLOWED_ORIGINS`/firewall to what you actually need — `/thumb` and
  `/playback` currently have no auth check (see [API.md](API.md)); don't put
  this on the open internet without adding one first
- Set `SECRET_KEY`/session handling review before treating this as
  production — the current session token has no expiry
