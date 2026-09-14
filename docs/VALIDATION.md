# Validation — measured evidence for the deployment targets

The project stated three deployment targets as goals. They were not validated
by experiment. This document reports what running the experiments actually
measured, including the one that failed.

Reproduce with:

```bash
python -m benchmarks.run_all --projects 100 --availability-seconds 45
```

Raw results: `benchmarks/results.json`, also served by `GET /api/validation`.

**Host for the figures below:** Darwin arm64, Python 3.12.1,
torch 2.5.1, device `mps`, ffmpeg
present, 8 cores.

---

## Summary

| Target | Result | Status |
|---|---|---|
| 99.5% availability | 100.0% over 15,576 requests | **PASS** |
| 100 concurrent projects | 100/100 completed, 0 lost | **PASS** (after a fix — see below) |
| Cross-platform Linux / macOS / Windows | macOS measured here; Linux + Windows run in CI | **PARTIAL** |

---

## 1. Availability — PASS

Sustained concurrent read load against the running application for
30.0s at 16 concurrent workers, cycling
across seven endpoints (health, analytics, projects, models, status, storage,
savings projection).

| Metric | Value |
|---|---|
| Requests | 15,576 |
| Succeeded | 15,576 |
| Failed | 0 |
| **Availability** | **100.0%** |
| Throughput | 518.9 req/s |
| Latency p50 | 26.44 ms |
| Latency p90 | 57.18 ms |
| Latency p99 | 92.13 ms |
| Latency max | 166.0 ms |

Errors observed: none.

**What this does and does not show.** It measures *application* availability
under concurrent load — no request was dropped or returned 5xx. It says nothing
about infrastructure availability: network partitions, host restarts, disk
exhaustion and deployment rollovers are not exercised. A 99.5% infrastructure
SLA would need sustained monitoring of a deployed instance over weeks, which is
outside what a benchmark run can claim. The honest statement is: *the
application does not itself become the cause of unavailability under this load.*

---

## 2. Concurrent projects — PASS, after fixing a real defect

This target failed on first measurement, and the failure was worth having.

### First run — FAIL

```
accepted 100/100 · completed 0 · errored 0 · stuck 100 · 903s · 0 segments
```

Every upload was accepted and **none** completed. Nothing errored; the work
simply never progressed, and all 100 projects sat in `processing` until the
timeout.

**Cause.** The upload endpoint started a bare `threading.Thread` per project.
Each of those threads independently spawns an ffmpeg subprocess for
segmentation and loads its own view of three torch models. 100 simultaneous
uploads therefore meant roughly 100 concurrent ffmpeg processes and 100 threads
contending for model-load locks, each with torch's own intra-op thread pool
underneath, on an 8-core machine. It thrashed instead of working.

Unbounded concurrency was never right for this workload: the pipeline is
CPU-bound, so running more jobs than cores cannot raise throughput — only
contention and memory pressure.

### The fix

`engine/jobs.py` — a single bounded `ThreadPoolExecutor` with a queue in front.
Uploads are still accepted immediately (the API returns straight away) but are
marked `queued` and picked up as workers free. `torch.set_num_threads()` is
capped so workers do not oversubscribe the machine. Pool size is
`max_concurrent_jobs`, defaulting to half the core count (min 2, max 6).

### Second run — PASS

```
accepted 100/100 · completed 100 · errored 0 · stuck 0 · 551.4s
pool 4 workers · peak queue depth 96 · 100 segments produced
```

| Metric | Before | After |
|---|---|---|
| Completed | 0 | **100** |
| Stuck / lost | 100 | **0** |
| Wall time | 903s (timeout) | **551.4s** |
| Throughput | 0/min | **10.9/min** |
| Segments produced | 0 | **100** |

Submission of all 100 uploads took 0.29s, so the API remained
responsive throughout; the queue absorbed the backlog (peak depth
96) and drained it.

**The pass condition is that no accepted work is lost.** A system that queues
100 projects and finishes them in 551.4s is serving the target;
one that accepts 100 and silently delivers none is not, however fast its
uploads return.

**Caveat.** The benchmark uses small synthetic clips, so these numbers measure
scheduling and completion under simultaneous load, not per-video inference
time. Real 15-second 1080p segments take substantially longer per project;
what generalises is the *shape* — bounded concurrency with a visible queue
rather than unbounded threads.

---

## 3. Cross-platform — PARTIAL

| Platform | Status | Source |
|---|---|---|
| macOS (arm64) | **Measured** — full suite + benchmarks pass, device `mps` | this run |
| Linux (ubuntu-latest) | Runs in CI on every push and PR | `.github/workflows/ci-cd.yml` |
| Windows (windows-latest) | Runs in CI on every push and PR | `.github/workflows/ci-cd.yml` |

The CI matrix runs the same test suite, the same platform probe and the
availability experiment on all three operating systems (plus Python 3.11 and
3.12 on Linux), and uploads each `results.json` as a build artifact.

**This is honest but incomplete.** At the time of writing, the macOS row is
measured directly; the Linux and Windows rows are *configured to be measured*
and will be populated by the next CI run. Until that run completes and its
artifacts are attached, cross-platform support should be described as
"supported and tested in CI", not "validated". Notable platform-specific risks
the matrix is there to catch:

- **ffmpeg availability.** Without it the pipeline falls back to an OpenCV
  re-encode that inflates storage. The workflow installs ffmpeg per-OS.
- **Path handling.** Windows path separators in archive and weight paths.
- **Device selection.** CUDA on Linux runners is absent, so all CI runs are
  CPU; MPS is macOS-only and only exercised locally.

---

## 4. Threshold generalisation

A separate experiment, reported in full on the Workflow Simulator's *Threshold
Evidence* tab and in `weights/threshold_calibration.json`:

```bash
python -m training.calibrate_thresholds
```

The tier boundaries were originally calibrated on a single dataset. The sweep
re-tests every candidate boundary pair across four distributions — `nominal`,
`low_light`, `busy_scene` and `weak_models` — and reports the **critical miss
rate**: the fraction of segments with expert severity ≥ 0.80 that get tiered
LOW, where only keyframes survive and the moving footage is destroyed.

| Boundaries | Worst-case critical miss | Mean storage index |
|---|---|---|
| 0.40 / 0.70 (original) | 6.43% | 0.269 |
| 0.30 / 0.90 (recommended) | **1.27%** | **0.227** |

The original boundaries hold up under nominal, low-light and busy conditions
(0.00–0.28% miss) but degrade sharply when model confidence drops
(`weak_models`: 6.43%). The recommended pair is better on **both** axes —
roughly five times less irreversible loss *and* less storage — which is
possible because raising the HIGH boundary while lowering the LOW boundary
moves ambiguous segments into MEDIUM, where video is kept at reduced quality
rather than discarded.

Adopting the recommendation is an explicit, admin-only, audited action
(`POST /api/calibration/apply`); the sweep never changes live thresholds itself.

---

## 5. What is still not validated

Stated plainly, because the gap between "tested" and "claimed" is what the
review flagged in the first place.

- **Real audio-visual threat scenarios.** Every model here is vision-only.
  Validating against genuine incident footage with audio would require both an
  annotated corpus and an audio branch that does not exist. This is the most
  significant outstanding gap, and it bounds what the accuracy figures mean.
- **The sentiment model's ground truth.** It is trained on a scenario-level
  expert rubric, not human-annotated video. Held-out metrics measure fidelity
  to that rubric, not to reality — see
  [MODEL_CARD_sentiment.md](MODEL_CARD_sentiment.md).
- **Long-horizon availability.** No sustained uptime measurement of a deployed
  instance.
- **Storage savings at scale.** The measured savings rate comes from a small
  corpus (under an hour of footage). The savings
  *projection* compounds that rate forward and labels every assumption in its
  own payload; the absolute figures should be read as indicative.
- **Tier-3 grace period sizing.** 24 hours is a default, not an empirically
  derived review window.

*Generated from `benchmarks/results.json` on 2026-09-11.*
