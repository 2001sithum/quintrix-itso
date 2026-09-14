# API reference

Base URL: `http://localhost:8000/api`. Every endpoint except `/login`,
`/register`, `/password-strength`, and `/health` requires the session token
returned by `/login` in an `x-session` header.

Roles: `Administrator`, `SecurityOperator`, `User`. An endpoint with no
**Roles** column entry just requires *any* active session.

## Auth

| Method | Path | Roles | Body | Notes |
|---|---|---|---|---|
| POST | `/password-strength` | — | `password` | Live scoring while typing; returns `{level, color, score, checks}` |
| POST | `/register` | — | `username, full_name, password` | Rejects weak passwords; new account starts `pending` |
| POST | `/login` | — | `username, password` | 403 if account is `pending`/`rejected`; returns session token |
| POST | `/logout` | any | — | Deletes the session row (`x-session` header) |
| GET | `/me` | any | — | Current user's `username, role, full_name` |

## Admin — users

| Method | Path | Roles | Body |
|---|---|---|---|
| GET | `/admin/users` | Administrator | — |
| POST | `/admin/users/{uid}/approve` | Administrator | `action=approve\|reject, role` |
| PATCH | `/admin/users/{uid}` | Administrator | `full_name?, role?` — edits any active/pending user |
| DELETE | `/admin/users/{uid}` | Administrator | Blocks self-deletion and deleting the last active Administrator |

## Projects & upload

| Method | Path | Roles | Notes |
|---|---|---|---|
| POST | `/projects` | any | `multipart/form-data` with `file` (mp4/avi/mov/mkv, ≤500MB). Returns immediately; pipeline runs in the background |
| GET | `/projects` | any | List, newest first, with live segment counts |
| GET | `/projects/{pid}` | any | Project + all its segments |
| PATCH | `/projects/{pid}` | Administrator, SecurityOperator | `name` — rename the project (defaults to the uploaded filename) |
| DELETE | `/projects/{pid}` | Administrator, SecurityOperator | Cascades segments/events/alerts and deletes archived files |
| DELETE | `/segments/{sid}` | Administrator, SecurityOperator | Deletes one segment + its files |
| POST | `/segments/{sid}/tier` | Administrator, SecurityOperator | `tier=HIGH\|MEDIUM\|LOW`, sets `tier_manual=1` |

## Search & playback

| Method | Path | Roles | Query params |
|---|---|---|---|
| GET | `/search` | any | `start, end` (ISO), `objects, actions` (substring), `tier`, `min_ssig, max_ssig`, `page, page_size` |
| GET | `/segments/{sid}` | any | Full metadata for one segment |
| GET | `/thumb/{sid}` | none¹ | JPEG thumbnail |
| GET | `/playback/{sid}` | none¹ | Video (or keyframe JPEG for LOW-tier segments with no video) |

¹ These two are served without the `require()` auth check — fine for a local
demo, but see [DEPLOYMENT.md](DEPLOYMENT.md) before exposing this publicly.

## Analytics

| Method | Path | Roles | Returns |
|---|---|---|---|
| GET | `/analytics` | any | Storage report, project/tier/threat breakdowns, device |
| GET | `/status` | any | Active/completed project counts — meant for 2s polling |
| GET | `/storage` | any | Same shape as `analytics.storage` |
| GET | `/events` | any | Last 200 significant events |
| GET | `/recommendations` | any | Last 50 auto-generated recommendations |

## Alerts

| Method | Path | Roles | Body |
|---|---|---|---|
| GET | `/alerts` | Administrator, SecurityOperator | Last 200, newest first |
| POST | `/alerts/{aid}/ack` | Administrator, SecurityOperator | — (acks with current user + timestamp) |
| DELETE | `/alerts/{aid}` | Administrator | Permanently removes an alert record |

## Configuration

| Method | Path | Roles | Body |
|---|---|---|---|
| GET | `/config` | Administrator | — |
| POST | `/config` | Administrator | JSON body of any config keys; rejects `threshold_low >= threshold_high` |

Config keys:

| Key | Default | Meaning |
|---|---|---|
| `pipeline_profile` | `fusion` | `fusion` (deployed) or `legacy` — see [MODELS.md](MODELS.md) |
| `threshold_high` / `threshold_low` | `0.7` / `0.4` | Tier boundaries on Ssig |
| `alert_threshold` | `0.8` | Ssig at which an alert is raised |
| `motion_sensitivity` | `0.001` | Invaligator foreground-ratio gate |
| `object_conf_threshold` | `0.35` | Legacy-profile detector confidence |
| `track1_conf_threshold` / `track2_conf_threshold` | `0.35` | Fusion-profile detector confidence |
| `detection_stride_seconds` | `1.0` | Detector frame sampling rate |
| `action_min_frames` | `16` | Minimum frames for action recognition |
| `segment_seconds` | `15` | Target segment length (keyframe-aligned in practice) |
| `tier3_grace_hours` | `24` | Hold period before LOW footage is destroyed; `0` disables |
| `max_concurrent_jobs` | derived | Pipeline worker pool size; empty derives from core count |
| `action_model_backend` | `x3d` | Legacy profile only (`x3d` \| `r3d18`) |
| `context_rules` | JSON | `night_boost`, `weapon_boost`, `crowd_boost`, `fire_boost`, `unattended_boost` |

## Models & pipeline introspection

| Method | Path | Roles | Notes |
|---|---|---|---|
| GET | `/models` | any | **Which models are actually deployed**, read from live config and real checkpoint paths — the authoritative answer to the X3D-S vs R3D-18 question. Includes the 16-D feature contract and fusion channel weights. |
| GET | `/projects/{pid}/funnel` | any | Measured per-stage reduction for one project, plus end-to-end savings against the uploaded file |
| GET | `/projects/{pid}/simulate` | any | Everything the Workflow Simulator needs in one request: funnel + every segment with its fusion trace + model registry |
| GET | `/segments/{sid}/explain` | any | Why one segment got its score: features, weighted channels, context modifiers, model timings |
| POST | `/simulate/score` | any | JSON partial feature dict → full scoring decision through the real trained model and real fusion arithmetic. Missing features default to 0. Returns `rule_score` and `delta_vs_rule` alongside. |

## Projects and footages

A **project** is a container — a site, a camera, an investigation. A **footage**
is one uploaded recording and everything derived from it. Segments, events,
alerts, stage telemetry and the purge queue all hang off a *footage*.

```
project ──▶ footage ──▶ segment ──▶ events / alerts
                     └─▶ stage_stats / purge_queue
```

Earlier versions had a single `projects` table where each row was one
recording. `storage_manager.init_db()` migrates that shape in place: the old
table becomes `footages`, child tables' `project_id` is renamed `footage_id`,
and a new `projects` table is created with existing recordings grouped by name.

| Method | Path | Roles | Notes |
|---|---|---|---|
| GET | `/projects` | any | Projects with rolled-up totals. Deliberately does **not** include footage rows — the list page stays a list of projects |
| POST | `/projects` | Administrator, SecurityOperator | Create a project (`name`, `description`). Duplicate names are rejected |
| GET | `/projects/{pid}` | any | The project plus the footages inside it, each with its stage funnel |
| PATCH | `/projects/{pid}` | Administrator, SecurityOperator | Rename / re-describe |
| DELETE | `/projects/{pid}` | Administrator, SecurityOperator | Deletes the project, every footage in it, and their media |
| POST | `/footages` | any | Upload. `project_id` targets an existing project, `project_name` finds-or-creates one, neither sends it to **Unsorted** — a footage always has a parent |
| GET | `/footages` | any | Flat list; `?project=` scopes it |
| GET | `/footages/{fid}` | any | Footage + its segments + measured funnel |
| PATCH | `/footages/{fid}` | Administrator, SecurityOperator | Rename, or **move** to another project via `project_id` |
| DELETE | `/footages/{fid}` | Administrator, SecurityOperator | Cascades to segments/events/alerts and removes media |
| GET | `/footages/{fid}/funnel` | any | Measured per-stage reduction |
| GET | `/footages/{fid}/simulate` | any | Everything the Workflow Simulator needs, in one request |

## Reprocessing

| Method | Path | Roles | Notes |
|---|---|---|---|
| GET | `/overview/footages` | any | One row per footage: status, tier mix, per-stage reduction summary, reprocessability. Assembled server-side to avoid N+1 funnel requests. `?project=` scopes it |
| POST | `/footages/{fid}/reprocess` | Administrator, SecurityOperator | Deletes derived artefacts and re-queues. The uploaded source is kept, so it is repeatable |
| POST | `/footages/reprocess-all` | Administrator | Bulk re-run; `only_legacy=true` limits it to footage not on the fusion profile, `project=` scopes it |

Reprocessing exists because the pipeline changed underneath the data: anything
analysed before the lossless-segmentation and motion-filter fixes has inflated
segment sizes, no stage telemetry, no fusion trace and no detection overlay.

> The overview route sits under `/api/overview/` rather than `/api/projects/`:
> Starlette matches in registration order, so `/api/projects/overview` would be
> swallowed by the earlier `/api/projects/{pid}` route.

## Operating modes

| Method | Path | Roles | Notes |
|---|---|---|---|
| GET | `/modes` | any | All five modes, which one the live config matches (or `custom`), and the specific keys differing from each |
| GET | `/modes/{id}/preview` | any | What applying the mode would change — shown before confirming |
| GET | `/modes/{id}/rationale` | any | **Why** the mode behaves as it does — replays every real Ssig through its boundaries and reports the tier split, storage index, and how many segments would newly lose their video |
| POST | `/modes/{id}/apply` | Administrator | Writes the mode's keys; audited |

Modes are `forensic`, `security`, `balanced`, `economy`, `edge`. They own 12
config keys and leave everything else untouched; editing any owned key puts the
system into `custom`. Modes affect **future uploads only**. Full write-up,
including the measured trade-off for each, in [MODES.md](MODES.md).

## Deep analytics

| Method | Path | Roles | Query | Notes |
|---|---|---|---|---|
| GET | `/analytics/detections` | any | `project?`, `profile?` | Per-class prevalence and confidence, split by **track**, plus co-occurrence pairs and confidence histograms |
| GET | `/analytics/decisions` | any | `project?`, `profile?` | Modifier fire rates, per-channel contribution share, Ssig/sentiment histograms, tier + hazard mix, boundary sensitivity |
| GET | `/analytics/alerts` | Administrator, SecurityOperator | `project?` | Severity mix, top triggers, volume by day, acknowledgement latency |
| GET | `/analytics/timeline` | any | `project?`, `limit`, `profile?` | Parallel arrays of Ssig, sentiment, motion, tier and bytes in capture order |
| GET | `/segments/{sid}/overlay` | any | — | Per-frame detection boxes for the playback overlay |

`profile=fusion` scopes the corpus to the deployed pipeline. Without it, legacy
segments are included and the action labels come from two different class
vocabularies (Kinetics-400 vs UCF-Crime), which the Analytics UI warns about.

**Track attribution.** Fusion-written segments carry an explicit `track` on each
object. Legacy segments do not, and the legacy COCO detector marks `person`,
`car` and `truck` as *critical* — so attribution falls back to Track 1 class
membership rather than the `critical` flag, which would otherwise file
pedestrians under the purpose-trained threat detector.

### Detection overlay

`GET /segments/{sid}/overlay` returns:

```json
{
  "segment_id": "...", "duration": 15.0, "tier": "HIGH", "ssig": 0.91,
  "available": true,
  "frames": [
    { "t": 3.0, "boxes": [
        { "label": "Rifle", "conf": 0.37, "track": 1, "critical": true,
          "box": [0.31, 0.22, 0.58, 0.71] } ] }
  ]
}
```

Boxes are **normalised to 0..1** of the frame, because the overlay is drawn over
a `<video>` element whose rendered size is unrelated to the frame size the model
saw. Frames are sampled at the detector stride (~1 fps), not per video frame.

Segments processed before overlay capture existed return `available: false`
with an empty `frames` list and a note — the player degrades to plain video
rather than erroring.

## Savings projection

| Method | Path | Roles | Query |
|---|---|---|---|
| GET | `/savings/projection` | any | `months` (≤120), `growth` (%/month), `cost_per_gb`, `retention_months`, `profile` |

Returns two series: `flat_fleet` (constant camera count — saving grows
**linearly**) and `growing_fleet` (camera count compounds — saving grows
**exponentially**). The exponential shape comes from fleet growth compounding,
not from the pipeline improving; `assumptions.why_exponential` states this in
the payload. `profile=fusion` measures the savings rate on the deployed
pipeline only, excluding legacy runs.

## Tier-3 deletion safeguard

| Method | Path | Roles | Notes |
|---|---|---|---|
| GET | `/review-queue` | Administrator, SecurityOperator | `status` (default `pending`), `limit`. LOW footage held before destruction |
| POST | `/review-queue/{qid}/restore` | Administrator, SecurityOperator | `tier=HIGH\|MEDIUM` — re-tiers and keeps the footage |
| POST | `/review-queue/{qid}/purge` | Administrator, SecurityOperator | Confirms deletion now |
| POST | `/review-queue/sweep` | Administrator | Runs the expiry sweep immediately (also runs on a background timer) |

An entry can be actioned once; a second attempt returns 400 with its current
state.

## Calibration & validation evidence

| Method | Path | Roles | Notes |
|---|---|---|---|
| GET | `/calibration` | any | Tier-boundary generalisation sweep across 4 distributions. 404 with a hint if `python -m training.calibrate_thresholds` has not been run |
| POST | `/calibration/apply` | Administrator | Adopts the recommended boundaries; audited. The sweep never changes live thresholds on its own |
| GET | `/validation` | any | Measured availability / concurrency / platform results. 404 if `python -m benchmarks.run_all` has not been run |

## Logs

| Method | Path | Roles | Notes |
|---|---|---|---|
| GET | `/logs` | Administrator | `type, severity, limit` filters |
| GET | `/logs/stream` | any | Server-Sent Events, polls every 1s for ~10 minutes. `EventSource` can't send custom headers, so auth is passed as `?session=TOKEN` instead of `x-session` here — the only endpoint that accepts a token this way. Powers both the Logs page's live stream and the Projects page's animated pipeline tracker. |

## Misc

| Method | Path | Notes |
|---|---|---|
| GET | `/` | Serves `static/index.html` |
| GET | `/health` | `{ok: true, device: "mps"\|"cuda"\|"cpu"}` — no auth, used for container health checks |
| GET | `/status` | Processing counters plus `queue` (`queued`, `running`, `workers`) — uploads are queued onto a bounded worker pool, so a project reports `queued` before `processing` |
