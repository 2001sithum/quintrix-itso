# Quintrix ITSO Engine

**Intelligent Tiered Storage Optimization for Smart Surveillance Systems using
Context-Aware Anomaly Detection.**

A complete working prototype implementing all 60 functional requirements: user
management with RBAC, the 5-stage ITSO video pipeline (ingestion → Invaligator
motion filter → ensemble deep analysis → significance scoring → tiered storage),
forensic search, alerting, analytics, configuration, and full audit logging.

- **Backend:** FastAPI (`server.py`) — one REST API, 30 endpoints
- **Framework:** the `ITSOIntegratedSystem` facade + modular `engine/` and `models/`
- **Models:** YOLOv8, X3D-S, MobileNetV3 — pretrained, open weights, auto-downloaded on first run (no training)
- **Database:** SQLite with WAL tuning (per the system design doc) — zero setup
- **UI:** plain HTML/CSS/JS (no React), served by FastAPI — dark forensic console

> Verified end-to-end on real uploaded video (not sample/mock data) — see
> [docs/FUNCTIONAL_REQUIREMENTS.md](docs/FUNCTIONAL_REQUIREMENTS.md) for what
> was actually exercised, including two real defects found and fixed.

## Documentation

- [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) — pipeline stages, data model, request flow
- [docs/API.md](docs/API.md) — every REST endpoint, roles, and parameters
- [docs/FUNCTIONAL_REQUIREMENTS.md](docs/FUNCTIONAL_REQUIREMENTS.md) — FR-by-FR verification status
- [docs/DEPLOYMENT.md](docs/DEPLOYMENT.md) — Docker and GCP deployment options
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

> **First upload is slow.** On the first video the app downloads model weights
> (YOLOv8n ~6MB, X3D-S ~15MB, MobileNetV3 ~10MB) and loads them. Give it a
> minute; weights are cached afterward. Under Docker on macOS inference is
> CPU-only (the Linux VM can't reach the M2 GPU) — run natively for MPS/GPU.
> The app auto-selects the best device (FR60).

## Using the system

1. **Sign in** as `admin` / `admin123`.
2. **Projects → Upload footage** (MP4/AVI/MOV/MKV). The pipeline runs
   automatically and the project list live-updates.
3. Open a project to see per-segment objects, actions, threat, hazard,
   significance score, and storage tier, with thumbnails and playback.
4. **Search** across all segments by time, object, action, tier, and Ssig.
5. **Dashboard** shows storage savings, tier/threat distribution, and AI
   recommendations.
6. **Alerts** (admin/operator) lists high-significance events to acknowledge.
7. **Configuration** (admin) tunes thresholds, motion sensitivity, and rules.
8. **Logs** (admin) shows the audit trail with a live SSE stream.
9. **Users** (admin) approves new registrations and assigns roles.

New users register from the sign-in screen and appear as **pending** until an
admin approves them (FR03).

## Architecture — the 5-stage ITSO pipeline

```
Upload → [1] Ingestion (metadata + 15s segmentation)
       → [2] Invaligator motion filter (MOG2, static frames skipped)
       → [3] Ensemble deep analysis (YOLOv8 + X3D-S + MobileNetV3)
       → [4] Prioritization engine (Ssig + context modifiers)
       → [5] Tiered storage (HIGH lossless / MEDIUM transcode / LOW keyframes)
```

## Project layout

```
quintrix-itso/
├── server.py                 # FastAPI app — all 30 endpoints, serves UI
├── itso_system.py            # ITSOIntegratedSystem master facade
├── requirements.txt
├── Dockerfile
├── docker-compose.yml
├── engine/
│   ├── storage_manager.py    # SQLite schema, persistence, config, logging, metrics
│   └── itso_engine.py        # 5-stage pipeline, Invaligator, Ssig, tiering, events, alerts
├── models/
│   ├── object_detector.py    # YOLOv8 (FR11)
│   ├── action_recognizer.py  # X3D-S (FR12)
│   ├── sentiment_analyzer.py # MobileNetV3 + threat head (FR13)
│   ├── tier_segmenters.py    # FFmpeg/cv2 tier processors (FR18-22)
│   └── kinetics_labels.json
└── static/
    ├── index.html            # UI shell
    ├── app.css               # forensic console theme
    └── app.js                # all views + API wiring (no framework)
```

`docs/` holds the architecture, API reference, deployment guide, and the
full FR-by-FR coverage table — see [Documentation](#documentation) above.

## Known issues

- **Action labels incomplete.** `models/kinetics_labels.json` maps 44 of 400
  Kinetics-400 classes; unmapped predictions show as `action_N` instead of a
  name. Needs the full label list regenerated from an authoritative source
  matching the `x3d_s` checkpoint's class order.
- **`/thumb` and `/playback` have no auth check** — fine for local/demo use,
  add one before exposing this beyond localhost.
- **Session tokens don't expire.** Fine for a prototype; add TTL/refresh
  before any real deployment.

## Notes on design decisions

- **No training.** All three models ship pretrained (COCO / Kinetics-400 /
  ImageNet). Threat, hazard, and significance are rule-based scoring over model
  outputs, exactly as the spec describes ("weighting algorithm", "context
  modifiers", "evaluate triggers").
- **SQLite**, not Postgres — matches the system design doc's WAL-tuned schema and
  needs zero external services.
- **Graceful degradation:** if a model fails to load, that stage returns empty
  results and the pipeline continues, so the app is always runnable.
- Passwords are hashed (SHA-256) and never stored in plaintext; sessions use
  random tokens.
