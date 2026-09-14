<h1 align="center">Quintrix ITSO — v13</h1>

<p align="center">
  <b>Intelligent Tiered Storage Optimization for smart surveillance</b><br>
  Context-aware anomaly detection that decides what CCTV footage is worth keeping.
</p>

<p align="center">
  <img alt="pipeline" src="https://img.shields.io/badge/pipeline-R3D--18%20%2B%20YOLOv8s%20%C3%972%20%2B%20MLP-4c8dff">
  <img alt="tests" src="https://img.shields.io/badge/tests-152%20passing-26d07c">
  <img alt="saving" src="https://img.shields.io/badge/measured%20saving-59.8%25-26d07c">
  <img alt="license" src="https://img.shields.io/badge/license-MIT-7dd3fc">
</p>

---

## What it does

Surveillance generates far more video than anyone can store or watch. Quintrix
ITSO scores every 15-second segment for how *significant* it is, then stores
each segment at a quality matched to that score — lossless for a weapon, a
re-encode for ordinary activity, a single keyframe for an empty corridor.

Measured across 418 MB of real footage: **418.2 MB → 168.2 MB, a 59.8% saving**,
with the segments that mattered kept at full quality.

```
Upload → [1] Ingestion        ~15s segmentation (ffmpeg stream copy, lossless)
       → [2] Invaligator      GMM/MOG2 motion gate — static segments skip analysis
       → [3] Frame sampling   16 frames for action, ~1 fps for the detectors
       → [4] Inference+fusion R3D-18 + Track 1 + Track 2 → 16-D features →
                              trained MLP → significance score + tier
       → [5] Tiered storage   HIGH lossless / MEDIUM transcode / LOW keyframes
                              (+ grace queue before irreversible deletion)
```

## The models

**R3D-18 fine-tuned on UCF-Crime is the deployed action model** — not the
Kinetics-400 X3D-S that earlier drafts described. That distinction is the
point: Kinetics labels (*"playing cricket"*, *"washing dishes"*) carry no
security meaning, so a score built on them could use the model's *confidence*
but never its *label*. UCF-Crime's 14 classes (`Assault`, `Robbery`,
`Shooting`, `Arson`, …) are the semantics, which is what makes the fusion stage
possible at all. Full reconciliation in **[docs/MODELS.md](docs/MODELS.md)**;
the running service reports the same answer at `GET /api/models`.

| Stage | Role | Model |
|---|---|---|
| 2 | Motion gate | MOG2 (Gaussian-mixture background subtraction) |
| 4 | Action / anomaly | **R3D-18**, UCF-Crime, 14 classes |
| 4 | Track 1 — threat objects | YOLOv8s fine-tuned: `Gun, Knife, Rifle, Fire, Smoke, Unattended_Bag, Mask, Broken_Glass` |
| 4 | Track 2 — context | YOLOv8s (COCO), filtered to 7 scene classes |
| 4 | Significance scoring | Trained MLP over a 16-D fusion vector |

## Highlights

- **Auditable scoring** — every tiering decision records the weighted channels
  and context modifiers that produced it. Deletion is irreversible, so the
  score that causes it is inspectable.
- **Tier-3 grace queue** — LOW-tier video is held before destruction, with a
  before/after preview of exactly what would be lost.
- **Five operating modes** — Forensic / High Security / Balanced / Economy /
  Edge, each with its measured trade-off stated, not asserted
  ([docs/MODES.md](docs/MODES.md)).
- **Detection playback** — bounding boxes drawn from the models' own output,
  synced to a cinematic player with a per-class presence strip.
- **Measured, not claimed** — [docs/VALIDATION.md](docs/VALIDATION.md) reports
  the experiments, *including the one that failed first*.

## Documentation

- [docs/MODES.md](docs/MODES.md) — the **five operating modes**, what each is for, and what each costs
- [docs/MODELS.md](docs/MODELS.md) — **which model is deployed where**, and why (single source of truth)
- [docs/IMPLEMENTATION.md](docs/IMPLEMENTATION.md) — how the CCTV pipeline was integrated, and the defects that surfaced
- [docs/VALIDATION.md](docs/VALIDATION.md) — measured evidence for the deployment targets
- [docs/MODEL_CARD_sentiment.md](docs/MODEL_CARD_sentiment.md) — the trained scoring model, its corpus and its limits
- [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) — pipeline stages, data model, request flow
- [docs/API.md](docs/API.md) — every REST endpoint, roles, and parameters
- [docs/FUNCTIONAL_REQUIREMENTS.md](docs/FUNCTIONAL_REQUIREMENTS.md) — FR-by-FR verification status
- [docs/DEPLOYMENT.md](docs/DEPLOYMENT.md) — Docker and GCP deployment options
- [docs/CI_CD.md](docs/CI_CD.md) — GitHub Actions pipeline: build/test on every PR, auto-redeploy to GCP on push to main
- [docs/FR_SPECIFICATION.md](docs/FR_SPECIFICATION.md) — the original university project brief (all 60 FRs as specified)

## Run it

### Option A — Docker (recommended)

```bash
docker compose up --build
```

Open **http://localhost:8000**. Sign in with **`admin` / `admin123`**.

### Option B — locally (Mac M2 Pro, native = faster)

```bash
pip install -r requirements.txt
uvicorn server:app --reload
```

Open **http://localhost:8000**.

> **First upload is slow.** The deployed models load on first use — R3D-18
> (~130MB) and the two YOLOv8s detectors (~22MB each) ship in `weights/`; the
> COCO context model is fetched on first run if absent. Give it a minute;
> everything is cached afterward. Under Docker on macOS inference is CPU-only
> (the Linux VM can't reach the M2 GPU) — run natively for MPS/GPU. The app
> auto-selects the best device (FR60).
>
> **ffmpeg is required** for lossless stream-copy segmentation. Without it the
> pipeline falls back to an OpenCV re-encode that inflates storage before
> tiering — it still runs, but the savings figures will be much worse.

## Using the system

1. **Sign in** as `admin` / `admin123`.
2. **Projects** — a project is a container for one site, camera or
   investigation. Create one, then upload footage (MP4/AVI/MOV/MKV) into it;
   the pipeline runs automatically and the view live-updates. The list stays a
   list of *projects*: open one to see the recordings inside it.
3. Open a footage to see per-segment objects, actions, threat, hazard,
   significance score, and storage tier, with thumbnails and playback.
4. **Search** across all segments by time, object, action, tier, and Ssig.
5. **Dashboard** shows storage savings, tier/threat distribution, and AI
   recommendations.
6. **Workflow** walks the pipeline stage by stage on real processed footage —
   per-stage reduction, model registry, score traces, savings projections and
   threshold evidence.
7. **Analytics** breaks down what the models found (by track), the action mix,
   the decision logic (modifier fire rates, channel contribution, Ssig
   distribution against the live thresholds), alerts, and a per-segment timeline.
8. **Playback** opens a cinematic detection player: the segment's video with
   bounding boxes drawn from the models' own output, a per-class presence strip,
   detection ticks on the scrubber, a master timeline across the whole
   recording, continuous playback, and the score trace that tiered it.
   Keyboard: `space` `← →` `, .` `b` `a` `f` `[ ]` `c`.
9. **Degradation Review Queue** (admin/operator) holds LOW-tier footage before
   it is destroyed, showing a before/after preview of exactly what each
   degradation would discard, with restore, degrade-now and bulk rescue.
10. **Alerts** (admin/operator) lists high-significance events to acknowledge.
11. **Configuration** offers five documented **operating modes** — Forensic
    Hold, High Security, Balanced, Storage Economy, Edge/Low Power — each a
    coherent preset with its measured trade-off stated, plus a manual tab for
    every individual parameter. See [docs/MODES.md](docs/MODES.md).
12. **Logs** (admin) shows the audit trail with a live SSE stream.
13. **Users** (admin) approves new registrations and assigns roles.

New users register from the sign-in screen and appear as **pending** until an
admin approves them (FR03).

## Data model

```
project ──▶ footage ──▶ segment ──▶ events / alerts
  (a site,   (one        (~15s of
   camera,    uploaded     video)
   case)      recording)
```

A **project** is a container and owns no media. A **footage** is one uploaded
recording and everything derived from it — segments, stage telemetry, alerts
and held purge files all hang off a footage. Databases from before this split
are migrated in place on startup, with existing recordings grouped into
projects by name.

## Architecture — the 5-stage ITSO pipeline

```
Upload → [1] Ingestion        metadata + ~15s segmentation (ffmpeg stream copy, lossless)
       → [2] Invaligator      MOG2 motion gate — static segments skip deep analysis
       → [3] Frame sampling   16 frames for action, ~1 fps for the detectors
       → [4] Inference+fusion R3D-18 + Track 1 + Track 2 → 16-D features → trained
                              sentiment MLP → Ssig + tier, with a full score trace
       → [5] Tiered storage   HIGH lossless / MEDIUM transcode / LOW keyframes
                              (+ Tier-3 grace queue before irreversible deletion)
```

Every stage records what went in and what came out into `stage_stats` during
the real run. The **Workflow Simulator** page replays that measured funnel
stage by stage, alongside the model registry, the per-segment score trace, a
what-if scorer, savings projections, and the threshold-calibration evidence.

### Pipeline profiles

`pipeline_profile` selects which pipeline runs:

- **`fusion`** (default, deployed) — the pipeline above.
- **`legacy`** — the original X3D-S + COCO YOLOv8n + MobileNetV3 path, kept
  runnable so the two can be compared on identical footage.

The deployed action model is **R3D-18 fine-tuned on UCF-Crime**, not X3D-S.
`GET /api/models` reports this from the live config and real checkpoint paths,
so the running system is the authority — see [docs/MODELS.md](docs/MODELS.md).

## Project layout

```
quintrix-itso/
├── server.py                 # FastAPI app — all REST endpoints, serves UI
├── itso_system.py            # ITSOIntegratedSystem master facade
├── requirements.txt
├── Dockerfile
├── docker-compose.yml
├── engine/
│   ├── storage_manager.py    # SQLite schema, persistence, config, logging, metrics
│   ├── itso_engine.py        # 5-stage pipeline, Invaligator, tiering, events, alerts
│   ├── fusion.py             # Stage 4 — multi-model fusion, scoring, tier assignment
│   ├── analytics.py          # reduction funnel, savings projection, model registry
│   ├── modes.py              # five documented operating presets
│   └── jobs.py               # bounded pipeline worker pool
├── models/
│   ├── action_recognizer_r3d18.py  # DEPLOYED action model — R3D-18 / UCF-Crime
│   ├── suspicious_detector.py      # Track 1 — 8-class threat detector
│   ├── context_detector.py         # Track 2 — COCO context objects
│   ├── sentiment_model.py          # trained scoring MLP + rule fallback
│   ├── object_detector.py    # YOLOv8n (legacy profile)
│   ├── action_recognizer.py  # X3D-S (legacy profile fallback)
│   ├── sentiment_analyzer.py # MobileNetV3 (legacy profile)
│   ├── tier_segmenters.py    # FFmpeg/cv2 tier processors (FR18-22)
│   └── kinetics_labels.json
├── training/                 # corpus, trainer, threshold calibration sweep
├── benchmarks/               # availability + concurrency experiments
├── tests/                    # 152 unit/integration tests incl. regressions
├── weights/                  # model checkpoints + trained metrics
└── static/
    ├── index.html            # UI shell
    ├── app.css               # forensic console theme
    └── app.js                # all views + API wiring (no framework)
```

`docs/` holds the architecture, API reference, deployment guide, and the
full FR-by-FR coverage table — see [Documentation](#documentation) above.

## Reprocessing

The pipeline changed underneath earlier data — projects analysed before the
lossless-segmentation and motion-filter fixes carry inflated segment sizes, no
stage telemetry, no fusion trace and no detection overlay. **Project
Management → Reprocess** (or `POST /api/projects/reprocess-all`) re-runs
footage through the current pipeline; derived artefacts are deleted and
rebuilt, the uploaded source is kept, so it is repeatable.

Re-running the full corpus under **Balanced** gave **418.2 MB → 168.2 MB, a
59.8% fleet saving** across 180 segments — against a measured baseline of the
files actually uploaded.

## Tests and experiments

```bash
python -m pytest tests/ -q                      # 152 unit + integration tests
python -m training.train_sentiment              # retrain the scoring model
python -m training.calibrate_thresholds         # tier-boundary generalisation sweep
python -m benchmarks.run_all                    # availability + concurrency evidence
```

The test suite includes named regression tests for every defect found during
the pipeline integration — see [docs/IMPLEMENTATION.md](docs/IMPLEMENTATION.md).

## Known issues

- **`/thumb` and `/playback` have no auth check** — fine for local/demo use,
  add one before exposing this beyond localhost.
- **Session tokens don't expire.** Fine for a prototype; add TTL/refresh
  before any real deployment.
- **The scoring model is trained on an expert rubric, not annotated video.**
  It generalises smoothly and is measurable, but its ceiling is the quality of
  that rubric. See [docs/MODEL_CARD_sentiment.md](docs/MODEL_CARD_sentiment.md).
- **Segment boundaries are keyframe-aligned**, a consequence of lossless
  stream-copy splitting, so they land near rather than exactly on
  `segment_seconds`.

## Notes on design decisions

- **Purpose-trained where it matters.** Action recognition and suspicious-object
  detection use checkpoints fine-tuned on security data (UCF-Crime and an
  8-class threat set), because Kinetics-400 and COCO labels carry no security
  semantics — a significance score built on them could use confidence but never
  the label. The scoring head is trained rather than hand-written; the
  arithmetic it replaced is retained as a fallback and shown beside it in the
  simulator so the contribution of training is visible rather than asserted.
- **Irreversible actions get a reversal path.** Tier 3 destroys the moving
  footage, so LOW segments are held in a review queue before deletion and every
  score records the reasons that produced it.
- **SQLite**, not Postgres — matches the system design doc's WAL-tuned schema and
  needs zero external services.
- **Graceful degradation:** if a model fails to load, that stage returns empty
  results and the pipeline continues, so the app is always runnable.
- Passwords are hashed (SHA-256) and never stored in plaintext; sessions use
  random tokens.
