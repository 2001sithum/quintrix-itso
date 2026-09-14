# Functional requirement coverage

Status legend: **live** = verified with a real API call or browser session
against real uploaded video during development. **reviewed** = verified by
reading the implementation; not separately exercised. **limitation** = a
known gap, documented rather than silently left broken.

## Auth & users — FR01–07, 24, 56

| FR | Requirement | Status |
|---|---|---|
| 01–02 | Registration + real-time password strength | live |
| 03 | Admin approval gate blocks login until approved | live |
| 04–05, 24 | Login issues session token; token gates every request | live |
| 06 | Logout invalidates the session | reviewed |
| 07 | RBAC actually denies (not just hides UI) | live — 403 vs 200 tested with a real second account |
| 56 | Admin approves/assigns roles | live |

## Video pipeline — FR08–13, 28, 29, 33, 40, 55, 60

| FR | Requirement | Status |
|---|---|---|
| 08, 29 | Upload + async background processing | live |
| 09 | Fixed 15s segmentation | live |
| 10 | Invaligator motion filter | live |
| 11 | YOLOv8 object detection | live |
| 12 | X3D-S action recognition, top-5 | live — see defect #5 below |
| 13 | MobileNetV3 sentiment + threat head | live |
| 28 | Technical metadata (duration/fps/resolution/codec) | live |
| 33 | Thumbnails | live |
| 40 | Three-model ensemble feeding one decision | live |
| 55 | Frame downscaling for inference | reviewed |
| 60 | Device auto-select (CUDA/MPS/CPU) | live — selected `mps` with zero config |

## Scoring & tiered storage — FR14–23, 58

| FR | Requirement | Status |
|---|---|---|
| 14–15 | Ssig significance engine | live — separated a static shot (0.18) from a weapon-in-frame shot (1.00) in the same clip |
| 16–17 | Metadata + event extraction | live |
| 18–22 | HIGH lossless / MEDIUM transcode / LOW keyframe tiering | live — 60.9%–95.6% real storage reduction across four uploads |
| 23 | Hazard assessment | live — `hazard=critical` on knife detections |
| 58 | Manual tier override | reviewed |

## Search & playback — FR26, 32, 34–39

| FR | Requirement | Status |
|---|---|---|
| 26, 34–37 | Filterable, paginated forensic search | live (UI-verified) |
| 32 | Segment timeline | live — animated Quality Timeline (color-coded by tier, click-to-jump) added to the project detail view |
| 38 | Playback | **fixed** — see defect #4 below |
| 39 | Metadata drill-down | live — see UI refinement note below |

## Analytics & ops — FR25, 31, 49, 52, 59

| FR | Requirement | Status |
|---|---|---|
| 25, 31, 52 | Dashboard + storage reporting | live |
| 49 | Processing status polling | reviewed |
| 59 | Recommendations | reviewed — correctly empty on this dataset, no rule fired |

## Alerts — FR41–43

| FR | Requirement | Status |
|---|---|---|
| 41–42 | Alert generation + delivery to operators | live |
| 43 | Acknowledgment workflow | live — real ack with user + timestamp |

## Configuration — FR44–47

| FR | Requirement | Status |
|---|---|---|
| 44–47 | Processing/storage/alert thresholds, validated | live |

## Data & logging — FR27, 30, 48, 50, 51, 53, 54, 57

| FR | Requirement | Status |
|---|---|---|
| 30, 48 | Live SSE log stream + filtered log viewing | live |
| 27, 50, 51 | Project deletion with cascade | **fixed** — see defect #1 below |
| 53, 54, 57 | Work log, API logging, audit trail | live |

## Defects found and fixed during verification

1. **Orphaned alerts on project deletion (FR51 violation).** The `alerts`
   table had no foreign key, unlike `segments`/`events` which correctly
   cascade. Deleting a project left its alerts behind forever. Fixed with a
   proper `ON DELETE CASCADE` FK plus a migration that rebuilds the table and
   purges existing orphans on next boot (`engine/storage_manager.py`).
2. **First-run model downloads failed on macOS** with
   `CERTIFICATE_VERIFY_FAILED` — the standard python.org CA-bundle gap. Fixed
   by pointing `SSL_CERT_FILE` at `certifi`'s bundle at import time
   (`server.py`), added `certifi` to `requirements.txt`.
3. **Upload dropzone UI collapsed** (cosmetic) — a `<label>` wrapping a
   block-level `<div>` fragmented under the default inline display, causing
   the text to overlap the page heading. Fixed with `display:block` on
   `.dropzone` (`static/app.css`).
4. **Playback silently did nothing (FR38).** `playSeg()` called
   `window.open()` *after* an `await fetch()` — by the time the fetch
   resolved, the call was no longer inside the original click's user-gesture
   window, so browsers silently blocked it as a popup. Fixed by playing the
   video/keyframe inline in the existing metadata modal instead of a new
   window — no popup blocker involved, and better UX (`static/app.js`).
5. **Every action-recognition label was wrong, not just missing (FR12).**
   `models/kinetics_labels.json` had 44 entries, and cross-checking against
   the authoritative Kinetics-400 class list used by this exact `x3d_s`
   checkpoint showed **all 44 were mismatched** — e.g. index 214 rendered as
   "fighting" when the model's actual class 214 is "picking fruit". Looked
   fabricated rather than sourced. Replaced with the complete, verified
   400-class mapping from PyTorchVideo's own `kinetics_classnames.json`.
6. **`/logs/stream` had no auth check at all** — not even a basic session
   check, unlike every other endpoint. Found while wiring the live pipeline
   tracker (below) into it. Fixed with `require(request)`; since
   `EventSource` can't send the `x-session` header, the token is now also
   accepted via `?session=` query param for this one endpoint
   (`server.py`, `docs/API.md`).

## Features added beyond the original spec

- **Live animated pipeline tracker** — while a project is processing, the
  detail view shows a 3-node stepper (Ingest → Analyze & Score → Complete)
  driven live by the new per-stage/per-segment process logs over the existing
  SSE log stream, with a progress bar and streaming detail line. Replaces a
  2.5s dumb-polling refresh loop.
- **Interactive analysis timeline + inspector (FR32/38/39)** — the project
  detail view's segment list was replaced with a scrubber: a single
  horizontal bar spanning the video's real duration, each block sized by
  segment length, colored by tier, with an inner bar showing that segment's
  Ssig. Clicking a block selects it and populates an inspector panel
  (thumbnail, threat/hazard, object/action chips, playback, tier override,
  delete) without leaving the timeline — replaces scrolling a long vertical
  list of segment cards.
- **Full CRUD for users, projects, segments, and alerts** — see the CRUD
  section of `docs/API.md` (rename projects, edit/delete users with
  last-admin guards, delete segments/alerts from the inspector or search
  results).
- **In-app scoring documentation** (`Docs` nav item) — explains the 5-stage
  pipeline and the Ssig formula's weights/boosts using the config's live
  threshold values, not just static text.
- **Visual redesign** — glassmorphism cards (backdrop blur + gradient
  borders), an ambient grid/glow background, gradient brand/heading text,
  consistent hover/focus states across every button and nav item, and
  motion (fade-up on view change, pulsing live indicators) throughout
  `static/app.css`. No new dependencies — pure CSS, keeping the project's
  "no framework" design decision intact.

---

## CCTV pipeline integration — new capability

Added when the `new[pipelne}` Colab pipeline was integrated. Full detail in
[IMPLEMENTATION.md](IMPLEMENTATION.md); model reconciliation in
[MODELS.md](MODELS.md); measured evidence in [VALIDATION.md](VALIDATION.md).

| Capability | Status |
|---|---|
| Track 1 suspicious-object detector (8 threat classes) | live — verified detecting a Rifle in real footage, forcing HIGH via the hazard floor |
| Track 2 COCO context detector (7 classes, crowd counts) | live |
| R3D-18 / UCF-Crime as the deployed action model | live — reported by `GET /api/models` from real checkpoint paths |
| `engine/fusion.py` — the fusion/scoring stage the reference notebook never built | live |
| Trained sentiment model (16-D features → 0..1 score) | live — held-out R² 0.94, ρ 0.96, tier accuracy 0.86 |
| Per-stage reduction telemetry (`stage_stats`) | live — recorded during real runs, rendered by the Workflow Simulator |
| Workflow Simulator page (5 views) | live — verified in a browser session |
| Tier-3 grace queue + restore/purge | live — restore, confirm-delete, double-action rejection, and expiry sweep all exercised |
| Bounded job queue (`engine/jobs.py`) | live — 100 concurrent projects, 100 completed, 0 lost |
| Threshold generalisation sweep | live — 4 distributions × 6,000 segments |
| Availability + concurrency benchmarks | live — see VALIDATION.md |

### Reviewer feedback addressed

| Feedback | Response |
|---|---|
| *"Clarify which action-recognition model is actually used"* | [MODELS.md](MODELS.md) is now the single source of truth, and `GET /api/models` reports the answer from live config and real checkpoint paths so prose cannot drift from code. **R3D-18 is deployed**; X3D-S is a legacy-profile fallback. |
| *"Add test evidence for the stated deployment targets"* | [VALIDATION.md](VALIDATION.md) reports measured results. Availability **PASS** (100% over 15,576 requests). Concurrency **FAILED** on first measurement (0/100 completed) — the cause was unbounded thread-per-upload; fixed with a bounded pool and now **PASS** (100/100). Cross-platform is **PARTIAL**: macOS measured, Linux and Windows run in the CI matrix. |
| *"Tier 3 is irreversible — add a safeguard"* | LOW footage is parked in `purge_queue` for `tier3_grace_hours` (default 24) instead of being deleted at scoring time. Operators can restore (keeping the video) or confirm deletion; a background sweeper enforces the deadline. Every score also records an auditable trace of the reasons that produced it. |
| *"Tier boundaries were calibrated on one dataset — check they generalise"* | `training/calibrate_thresholds.py` sweeps every boundary pair across four distributions. The original 0.40/0.70 loses **6.43%** of critical segments under weak model confidence; the recommended **0.30/0.90** loses **1.27%** *and* uses less storage. Adopting it is an explicit, audited admin action. |
| *"Validate with real audio-visual threat scenarios"* | **Not done, and stated as such.** Every model here is vision-only; adding an audio branch is new capability rather than integration. Listed as the most significant outstanding gap in [VALIDATION.md](VALIDATION.md) §5. |
