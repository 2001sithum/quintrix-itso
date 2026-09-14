# Implementation plan — CCTV pipeline integration

How the `new[pipelne}` Colab pipeline was integrated into the running
application, what each piece does, and what was deliberately left out.

---

## Starting point

The drop contained three files:

| File | What it was |
|---|---|
| `cctv_pipeline_inference.py` | A Colab notebook export: 15s segmentation → R3D-18 action recognition → YOLOv8s Track 1 → COCO Track 2 → JSON |
| `best.pt` | YOLOv8s fine-tuned on 8 suspicious-object classes |
| `r3d18_best.pt` | R3D-18 with a 14-class UCF-Crime head (a *different* checkpoint from the one already at the project root) |

The notebook produced one structured record per segment and stopped there,
stating in its own documentation that fusion and scoring were not built.

The application already had the complementary half: segmentation, a motion
gate, significance scoring, tiering, search, alerting and a UI — but with
models that carried no security semantics.

**The integration is therefore a join, not a replacement:** the notebook's
models supply meaning, the application supplies the pipeline that acts on it,
and the missing fusion stage is the seam between them.

---

## What was built

### 1. Model modules

| Module | Role |
|---|---|
| `models/suspicious_detector.py` | Track 1 — 8 threat classes, per-class `max_conf` + `frame_hits` over ~1 fps sampling |
| `models/context_detector.py` | Track 2 — COCO filtered to 7 context classes, with peak instance counts |
| `models/action_recognizer_r3d18.py` | Rewritten: multi-format checkpoint loading, full 14-class distribution, per-class severity |
| `models/sentiment_model.py` | Inference for the trained scoring model, with contract enforcement and a rule fallback |

`frame_hits` is carried through deliberately. It is what separates a one-frame
false positive from an object genuinely present for the whole segment, and the
fusion stage weights it — a weapon detected in 1 of 15 sampled frames does not
score like one detected in 14.

### 2. The fusion stage — `engine/fusion.py`

The stage the notebook never built. It takes the three model outputs for one
segment and produces a single decision:

```
R3D-18 action distribution  ┐
Track 1 suspicious objects  ├─► 16-D feature vector ─► sentiment (trained MLP)
Track 2 context objects     │                              │
motion ratio / clock        ┘                              ▼
                                                  Ssig ─► HIGH / MEDIUM / LOW
```

Two rules shape it:

- **The feature vector is defined once**, in `FEATURE_NAMES`, imported by both
  the trainer and the live pipeline. A feature cannot drift between them.
- **Every decision carries its reasons.** `fuse()` returns a `trace` of each
  weighted channel and each context modifier with a plain-language
  explanation. Tiering is irreversible; an auditable score is the minimum
  defence against a wrong one.

### 3. The trained sentiment model

`training/sentiment_dataset.py` + `training/train_sentiment.py` →
`weights/sentiment_mlp.pt`.

The honest description is **scenario-level expert labelling with
distributional sampling**: 18 surveillance scenarios, each with a severity
assigned from its harm profile and a generator that samples plausible model
outputs — including weak detections, one-frame false positives and undecided
action predictions. Labels carry annotator-style noise.

Held-out test results are in [MODEL_CARD_sentiment.md](MODEL_CARD_sentiment.md)
and `weights/sentiment_metrics.json`; curves and per-scenario error in
`docs/assets/sentiment_training.png`.

What training buys over the rule it replaced: smooth interpolation between
scenarios, learned feature *interactions*, and measurability. What it does not
buy: ground truth. Its ceiling is the quality of the rubric, which is stated
in the model card rather than glossed over.

### 4. Per-stage reduction telemetry

A `stage_stats` row is written **during the real run** for each of the five
stages, recording items/bytes/frames in and out plus wall time. The Workflow
Simulator reads those rows, so the funnel it draws is a measurement of that
video, not an illustration.

Each stage reduces a different quantity, which is why the table records the
unit:

| Stage | Reduces | Typical |
|---|---|---|
| 1 Ingestion & Segmentation | nothing — decomposition | ~0% (lossless stream copy) |
| 2 Invaligator motion filter | **segments** entering deep analysis | 0–90% depending on footage |
| 3 Frame sampling | **frames** reaching the models | ~91–97% |
| 4 Inference & fusion | **pixels → one searchable record** | ~99.9% |
| 5 Tiered storage | **bytes on disk** | 30–95% |

### 5. Tier-3 deletion safeguard

LOW tier keeps keyframes only — the moving footage is destroyed. Rather than
deleting at scoring time, LOW originals are parked in `purge_queue` with a
deadline (`tier3_grace_hours`, default 24). An operator can **restore** a
segment (promoting it and keeping the video) or **confirm** deletion early; a
background sweeper enforces the deadline. Setting the grace to 0 restores the
previous delete-immediately behaviour.

### 6. Workflow Simulator

A new page with five views: the animated stage walkthrough with per-stage
reduction, the model registry, the scoring trace plus a what-if panel, savings
projections, and the threshold-calibration evidence.

---

## Defects found and fixed during integration

Running the pipeline end-to-end on real footage surfaced four real bugs. Each
has a regression test named after it in `tests/`.

### 1. Segmentation inflated storage 3.7×

`cv2.VideoWriter` writes MPEG-4 Part 2. Feeding it H.264 re-encoded 17 MB of
source into **63 MB** of segments. Every downstream savings figure was then
computed against that inflated number.

Fixed by splitting with ffmpeg's segment muxer using **stream copy** — no
re-encode, lossless, near-instant, and the HIGH tier now genuinely keeps
original quality instead of a generational re-encode. Trade-off stated in the
code: `-c copy` can only cut on keyframes, so boundaries land near
`segment_seconds` rather than exactly on it, and real durations are measured
per segment afterwards.

*Test:* `test_segmentation_is_lossless_and_does_not_inflate`

### 2. The savings baseline was self-generated

The pipeline overwrote `projects.original_bytes` with the sum of its own
segment files, so savings were measured against a number the pipeline had
produced. Now the upload size is preserved and the segment total lives in
`segment_bytes`. A one-time migration repairs historical rows from the
still-present upload files.

On the existing corpus the repair changed the fleet-wide figure from a claimed
**78.2% saving** to an actual **−2.9%** — the legacy pipeline was *increasing*
storage, because HIGH-tier stream-copied the inflated segments straight to
disk. That is the finding the first fix addresses.

*Test:* `test_original_upload_size_is_never_overwritten`

### 3. The motion filter never filtered

MOG2 classifies every pixel of its first frame as foreground, because it has no
background model yet. `invaligator` took `max()` across all frames, so a
**completely static clip reported a motion ratio of 1.0** and passed. Stage 2 —
whose entire purpose is skipping static footage — was a no-op, and the compute
saving credited to it was never realised.

Fixed with a warm-up (the first frames train the model and are not measured)
and a sustain requirement (the threshold must be crossed in ≥2 measured frames,
so a codec keyframe refresh does not trip it).

Measured effect on a static test clip: 3 segments → 1 analysed, processing time
18s → 10s, storage saving 85.8% → 95.3%.

*Tests:* `test_still_video_is_filtered_out`,
`test_warmup_frames_are_excluded_from_the_measurement`,
`test_single_frame_spike_does_not_trip_the_gate`

### 4. Detector sampling never saw past the first second

Only the first 32 frames of each segment were buffered, so on a 15s segment the
~1 fps detector sampling covered roughly the first second. Now frames are
decimated across the whole segment within a bounded budget.

*Test:* `test_segment_frames_span_the_whole_segment`

Two smaller ones: a division by zero in the entropy feature when the action
model returned a degenerate distribution, and a foreign-key violation from
parking a segment for purge before its row existed.

---

## Deliberately not done

- **No retraining of R3D-18 or the YOLO detectors.** The supplied checkpoints
  are used as given. Re-training them needs the original datasets, which were
  not part of the drop.
- **No audio.** The reviewer's suggestion to validate against real
  audio-visual threat scenarios is sound, but every model here is
  vision-only; adding an audio branch is new capability, not integration, and
  is listed as future work in [VALIDATION.md](VALIDATION.md).
- **The calibration sweep does not change live thresholds on its own.**
  Adopting its recommendation is an explicit, admin-only, audited action.
- **Historical segments are not rescored.** Projects processed by the legacy
  pipeline keep their tiers and report an empty fusion trace; the UI says so
  rather than pretending otherwise.
