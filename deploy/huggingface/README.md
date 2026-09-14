---
title: Quintrix ITSO v13
emoji: 🎥
colorFrom: red
colorTo: indigo
sdk: docker
app_port: 8000
pinned: false
license: mit
short_description: Context-aware tiered storage for CCTV — R3D-18 + YOLOv8s fusion
---

# Quintrix ITSO — v13

**Intelligent Tiered Storage Optimization for smart surveillance.**

Surveillance produces far more video than anyone can store or watch. This
system scores every 15-second segment for how *significant* it is, then stores
each at a quality matched to that score — lossless for a weapon, a re-encode
for ordinary activity, a single keyframe for an empty corridor.

Measured on 418 MB of real footage: **418.2 MB → 168.2 MB, a 59.8% saving**,
with the segments that mattered kept at full quality.

## Try it

1. Sign in with **`admin`** / the password set for this Space.
2. **Projects → create a project → upload a clip** (MP4/AVI/MOV/MKV).
3. Watch the **live processing charts** build as each segment is scored.
4. Open a footage → **Playback** for detection boxes drawn from the models'
   own output, on a timeline you can scrub.
5. **Workflow** walks the pipeline stage by stage showing what each stage
   discarded; **Analytics** breaks down detections, actions and decision logic.

Short clips work best here — see the limits below.

## ⚠️ Limits of this Space

- **Storage is ephemeral.** This runs on a free CPU Space with no persistent
  disk. Every upload, analysis result and held recording is **lost** when the
  Space rebuilds or sleeps. Do not put anything you need here.
- **CPU only, 2 vCPU.** Inference is slow: expect roughly a minute per
  15-second segment. Upload short clips.
- **It sleeps** after inactivity, and the first request afterwards pays a cold
  start while ~170 MB of model weights load.
- **Anyone can sign in.** Treat everything here as public and disposable.

## The pipeline

```
Upload → [1] Ingestion        ~15s segmentation (ffmpeg stream copy, lossless)
       → [2] Invaligator      MOG2 motion gate — static segments skip analysis
       → [3] Frame sampling   16 frames for action, ~1 fps for the detectors
       → [4] Inference+fusion R3D-18 + Track 1 + Track 2 → 16-D features →
                              trained MLP → significance score + tier
       → [5] Tiered storage   HIGH lossless / MEDIUM transcode / LOW keyframes
```

| Stage | Role | Model |
|---|---|---|
| 2 | Motion gate | MOG2 (Gaussian-mixture background subtraction) |
| 4 | Action / anomaly | **R3D-18**, UCF-Crime, 14 classes |
| 4 | Track 1 — threat objects | YOLOv8s fine-tuned: `Gun, Knife, Rifle, Fire, Smoke, Unattended_Bag, Mask, Broken_Glass` |
| 4 | Track 2 — context | YOLOv8s (COCO), filtered to 7 scene classes |
| 4 | Significance scoring | Trained MLP over a 16-D fusion vector |

**R3D-18 fine-tuned on UCF-Crime is the deployed action model**, not
Kinetics-400 X3D-S. Kinetics labels (*"playing cricket"*) carry no security
meaning, so a score built on them could use the model's confidence but never
its label. UCF-Crime's classes are the semantics — which is what makes the
fusion stage possible at all.

## Source

Full source, documentation and test suite:
**https://github.com/2001sithum/quintrix-itso**

MIT licensed.
