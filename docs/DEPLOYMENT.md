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

### Option A — Compute Engine VM (closest match to current design)

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

### Option B — Cloud Run (serverless, but storage is ephemeral)

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

### Either way

- Build the container once and push it: `gcloud builds submit --tag
  gcr.io/YOUR_PROJECT_ID/quintrix-itso`
- Restrict `ALLOWED_ORIGINS`/firewall to what you actually need — `/thumb` and
  `/playback` currently have no auth check (see [API.md](API.md)); don't put
  this on the open internet without adding one first
- Set `SECRET_KEY`/session handling review before treating this as
  production — the current session token has no expiry
