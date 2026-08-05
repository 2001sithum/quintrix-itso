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
| 12 | X3D-S action recognition, top-5 | live, with a labeling gap — see below |
| 13 | MobileNetV3 sentiment + threat head | live |
| 28 | Technical metadata (duration/fps/resolution/codec) | live |
| 33 | Thumbnails | live |
| 40 | Three-model ensemble feeding one decision | live |
| 55 | Frame downscaling for inference | reviewed |
| 60 | Device auto-select (CUDA/MPS/CPU) | live — selected `mps` with zero config |

**Limitation:** `models/kinetics_labels.json` maps only 44 of the 400
Kinetics-400 classes (and contains one out-of-range index). Unmapped
predictions render as a placeholder like `action_211` instead of a verb. Not
patched with a guessed mapping — the correct fix is regenerating this file
from an authoritative Kinetics-400 class list matching the `x3d_s` checkpoint's
label order.

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
| 32 | Segment timeline | live |
| 38 | Playback | reviewed |
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
