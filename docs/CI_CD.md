# CI/CD

Two workflows, deliberately separate:

| Workflow | Trigger | What it does |
|---|---|---|
| [`ci-cd.yml`](../.github/workflows/ci-cd.yml) — **CI** | every push + PR | Test matrix on Linux / macOS / Windows, platform probe, container build check. Touches no infrastructure. |
| [`deploy-gcp.yml`](../.github/workflows/deploy-gcp.yml) — **Deploy to GCP** | push to `main` (code only), or manual | Tests → builds the image → pushes to Artifact Registry → deploys → health-checks. |

They are split so a docs-only change cannot trigger a deploy, and so exactly
one workflow owns the deployed VM.

## What changed from the previous pipeline

The old deploy step SSH'd into the VM and ran `git pull && docker compose up
--build`, which meant the VM rebuilt the image (≈2 GB of torch wheels) on every
release, needed a source checkout and a build toolchain, and produced a release
that was only identifiable by commit.

Now the image is built **once in CI**, pushed to **Artifact Registry**, and the
VM only pulls it. Every release is an immutable tag, so a rollback is a
redeploy of an older tag rather than a revert commit.

---

## One-time GCP setup

Replace the placeholders and run once.

```bash
export PROJECT=your-gcp-project
export REGION=europe-west1
export REPO=quintrix
export SA=github-deployer

gcloud config set project "$PROJECT"
gcloud services enable \
  artifactregistry.googleapis.com \
  iamcredentials.googleapis.com \
  compute.googleapis.com \
  run.googleapis.com

# 1. Artifact Registry repository for the image
gcloud artifacts repositories create "$REPO" \
  --repository-format=docker --location="$REGION" \
  --description="Quintrix ITSO container images"

# 2. Deploy service account
gcloud iam service-accounts create "$SA" --display-name="GitHub deployer"
SA_EMAIL="$SA@$PROJECT.iam.gserviceaccount.com"

for ROLE in roles/artifactregistry.writer \
            roles/compute.instanceAdmin.v1 \
            roles/iap.tunnelResourceAccessor \
            roles/run.admin \
            roles/iam.serviceAccountUser \
            roles/storage.objectViewer; do
  gcloud projects add-iam-policy-binding "$PROJECT" \
    --member="serviceAccount:$SA_EMAIL" --role="$ROLE" --condition=None
done
```

### 3. Keyless auth (Workload Identity Federation) — recommended

A service-account JSON key in a repo secret is a standing credential: it does
not expire, and anyone who can read the secret holds it. WIF mints a
short-lived token per workflow run instead, scoped to this repository.

```bash
export GH_REPO=your-org/your-repo

gcloud iam workload-identity-pools create github \
  --location=global --display-name="GitHub Actions"

gcloud iam workload-identity-pools providers create-oidc github-provider \
  --location=global --workload-identity-pool=github \
  --issuer-uri="https://token.actions.githubusercontent.com" \
  --attribute-mapping="google.subject=assertion.sub,attribute.repository=assertion.repository" \
  --attribute-condition="assertion.repository=='${GH_REPO}'"

PROJECT_NUM=$(gcloud projects describe "$PROJECT" --format='value(projectNumber)')
gcloud iam service-accounts add-iam-policy-binding "$SA_EMAIL" \
  --role=roles/iam.workloadIdentityUser \
  --member="principalSet://iam.googleapis.com/projects/${PROJECT_NUM}/locations/global/workloadIdentityPools/github/attribute.repository/${GH_REPO}"

echo "projects/${PROJECT_NUM}/locations/global/workloadIdentityPools/github/providers/github-provider"
```

> The `attribute-condition` matters. Without it, **any** GitHub repository can
> impersonate the service account.

### 4. Model weights

The `.pt` files are gitignored (~170 MB), so a CI checkout contains none of
them. **A container built without them starts and serves normally but detects
nothing** — R3D-18 and the Track 1 threat detector both degrade to empty
results, which looks healthy and is not. The build therefore fails rather than
shipping a silently blind image.

Upload them once (and again after any retraining):

```bash
gsutil mb -l "$REGION" "gs://$PROJECT-weights"
./scripts/upload-weights.sh "gs://$PROJECT-weights/quintrix"
```

### 5. Repository configuration

Variables (`Settings → Secrets and variables → Actions → Variables`) — none are
sensitive:

| Variable | Required | Example |
|---|---|---|
| `GCP_PROJECT_ID` | yes | `my-project` |
| `GCP_WEIGHTS_URI` | yes | `gs://my-project-weights/quintrix` |
| `GCP_REGION` | no (default `europe-west1`) | `europe-west1` |
| `GCP_AR_REPO` | no (default `quintrix`) | `quintrix` |
| `GCP_WIF_PROVIDER` | recommended | `projects/123/locations/global/workloadIdentityPools/github/providers/github-provider` |
| `GCP_DEPLOY_SA` | with WIF | `github-deployer@my-project.iam.gserviceaccount.com` |
| `GCP_APP_URL` | no | `https://itso.example.com` — enables the post-deploy health check |
| `GCP_STATE_BUCKET` | Cloud Run only | `my-project-itso-state` |

Secrets:

| Secret | Required | Notes |
|---|---|---|
| `GCP_VM_NAME` | VM deploys | Compute Engine instance name |
| `GCP_VM_ZONE` | VM deploys | e.g. `europe-west1-b` |
| `GCP_SA_KEY` | only without WIF | Service-account JSON. Prefer WIF and leave this unset. |

```bash
gh variable set GCP_PROJECT_ID   --body "$PROJECT"
gh variable set GCP_REGION       --body "$REGION"
gh variable set GCP_AR_REPO      --body "$REPO"
gh variable set GCP_WEIGHTS_URI  --body "gs://$PROJECT-weights/quintrix"
gh variable set GCP_WIF_PROVIDER --body "projects/${PROJECT_NUM}/locations/global/workloadIdentityPools/github/providers/github-provider"
gh variable set GCP_DEPLOY_SA    --body "$SA_EMAIL"
gh secret   set GCP_VM_NAME      --body "itso-vm"
gh secret   set GCP_VM_ZONE      --body "${REGION}-b"
```

---

## Deploy targets

### Compute Engine VM — the default

Chosen because **this application is stateful**. It keeps a SQLite database in
WAL mode and writes the entire archive to disk: uploads, tier artefacts,
thumbnails, and footage held in the Tier-3 review queue awaiting a human
decision. All of that lives on a persistent disk mounted at `/data` via the
named volumes in [`docker-compose.deploy.yml`](../docker-compose.deploy.yml).

The deploy copies that compose file up, pulls the SHA-tagged image, restarts,
and prunes images older than a week.

### Cloud Run — opt-in, for demos only

Run the workflow manually with **target: cloudrun**. Read this before using it
for anything real:

- Cloud Run's filesystem is **per-instance and ephemeral**. Without a mounted
  volume, every upload, tier artefact and held-for-review recording disappears
  when a revision is replaced or the service scales to zero.
- With `GCP_STATE_BUCKET` set, `/data` is backed by a GCS volume — but
  **SQLite in WAL mode is not safe over FUSE with concurrent writers**, which
  is why the job pins `--max-instances=1`. That caps throughput at one
  instance and is a correctness constraint, not a tuning choice.
- The pipeline runs on background threads, so the job sets
  `--no-cpu-throttling`. Without it, CPU is withdrawn the moment a request
  returns and a processing job can stall indefinitely.

For production footage, use the VM, or GKE with a `PersistentVolumeClaim`.

---

## Rolling back

Every build is tagged with its commit SHA, so a rollback deploys an old tag —
no revert commit, no rebuild:

```bash
gh workflow run "Deploy to GCP" -f target=vm -f image_tag=<previous-sha>
```

List what is available:

```bash
gcloud artifacts docker tags list \
  "$REGION-docker.pkg.dev/$PROJECT/$REPO/quintrix-itso"
```

## Verifying a deploy

The workflow already fails if `/api/health` does not return 200 (when
`GCP_APP_URL` is set). Worth checking by hand after a release:

```bash
curl -s "$APP_URL/api/health"            # {"ok":true,"device":"cpu"}
curl -s "$APP_URL/api/models" -H "x-session: $TOKEN" | jq '.fusion[].available'
```

If any model reports `available: false`, the weights did not make it into the
image — see step 4.
