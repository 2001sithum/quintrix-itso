# Models — what is deployed, and why

**This document is the single source of truth for which model runs where.**
Where prose and code could disagree, the running system reports the answer
itself: `GET /api/models` reads the live configuration and the real checkpoint
paths, and the Workflow Simulator's *Model Integration* tab renders it.

---

## The discrepancy this resolves

The main report described **X3D-S** as the action-recognition model. The
appendix documented a separate **R3D-18** trained on a different dataset. Both
models existed in the codebase, which made the question "which one is actually
deployed?" genuinely ambiguous.

**The answer: R3D-18, fine-tuned on UCF-Crime, is the deployed action model.**
X3D-S remains in the tree as a fallback backend for the legacy pipeline
profile. It is not what scores footage.

### Why R3D-18 and not X3D-S

This is not a preference — the fusion stage is impossible without it.

X3D-S is pretrained on **Kinetics-400**, whose classes are everyday activities:
*playing cricket*, *washing dishes*, *tying a tie*. None of them carry security
semantics. A significance score built on X3D-S can therefore only use the
model's **confidence**, never its **label** — "the model is 90% sure this is
*folding laundry*" tells a surveillance system nothing about risk.

R3D-18 is fine-tuned on **UCF-Crime**, whose 14 classes *are* the security
semantics: `Assault`, `Robbery`, `Shooting`, `Arson`, `Burglary`, `Normal`, and
so on. The fusion stage assigns each class a severity weight
(`models/action_recognizer_r3d18.py::SEVERITY`) and scores the **label itself**.
That is what makes `engine/fusion.py` possible at all.

---

## Deployed pipeline — the `fusion` profile

Selected by the `pipeline_profile` config key, which defaults to `fusion`.

| ITSO stage | Role | Model | Trained on | Weights |
|---|---|---|---|---|
| 2 | Motion gate | MOG2 background subtraction | not trained | — |
| 4 | Action / anomaly recognition | **R3D-18** (ResNet3D-18) | UCF-Crime, 14 classes | `weights/r3d18_ucfcrime.pt` |
| 4 | Track 1 — suspicious objects | **YOLOv8s** (fine-tuned) | 8 threat classes | `weights/yolov8s_suspicious.pt` |
| 4 | Track 2 — context objects | **YOLOv8s** (stock COCO) | COCO, filtered to 7 classes | `weights/yolov8s.pt` |
| 4 | Sentiment / threat scoring | **MLP 16→64→32→1** | 18-scenario expert rubric | `weights/sentiment_mlp.pt` |
| 5 | Tier execution | ffmpeg / OpenCV | — | — |

**Track 1 classes:** `Gun, Knife, Rifle, Fire, Smoke, Unattended_Bag, Mask,
Broken_Glass`
**Track 2 classes kept:** `person, backpack, handbag, suitcase, car,
motorcycle, bicycle` — the other ~73 COCO classes are dropped at the box level.

### Why two detectors

They answer different questions and must not be conflated:

- **Track 1** — *is there a threat object?* Purpose-trained, directly security-relevant.
- **Track 2** — *who and what else is in the scene?* A knife alone scores
  differently from a knife with six people around it, and an unattended bag is
  only unattended if nobody is near it.

Keeping them separate is what lets the fusion stage apply crowd and
unattended-object reasoning without treating a backpack as a threat.

---

## Available but inactive — the `legacy` profile

The original pipeline, retained so the two can be compared on identical
footage. Set `pipeline_profile` to `legacy` to run it.

| ITSO stage | Role | Model | Trained on |
|---|---|---|---|
| 4 | Action recognition | X3D-S | Kinetics-400 (400 everyday-activity classes) |
| 4 | Object detection | YOLOv8n | COCO, 80 classes |
| 4 | Scene sentiment | MobileNetV3-Small | ImageNet, used as a scene-activation proxy |

`action_model_backend` (`x3d` | `r3d18`) selects the action backend **for the
legacy profile only**. The fusion profile always uses R3D-18, for the reason
given above.

---

## Checkpoint resolution

`models/action_recognizer_r3d18.py` searches, in order:

1. `$R3D18_WEIGHTS`
2. `weights/r3d18_ucfcrime.pt` — the checkpoint integrated with this pipeline
3. `r3d18_best.pt` at the project root — an earlier checkpoint

Both are 14-class UCF-Crime heads but they are **distinct trainings** with
different SHA-256 hashes. `loaded_from()` reports which file is actually in
memory, and `/api/models` surfaces it, so "which weights are live?" is never a
matter of inference from filenames.

The loader accepts a bare `state_dict`, one wrapped under
`model_state_dict`/`state_dict`, and either saved from `nn.DataParallel` (a
`module.` prefix on every key). Partial loads log a loud warning rather than
failing silently.

---

## The trained sentiment model

Full detail in [MODEL_CARD_sentiment.md](MODEL_CARD_sentiment.md). Summary:

- Maps the 16-D fusion feature vector to a 0..1 threat score.
- Replaces hand-written arithmetic, which is kept as `rule_score()` for fresh
  clones and shown alongside the model in the simulator's what-if panel.
- Trained on a **scenario-level expert rubric corpus**, not human-annotated
  video — that distinction is stated rather than glossed, because no public
  dataset maps this pipeline's feature space to threat scores.
- The checkpoint stores its own `feature_names`. A checkpoint whose list
  disagrees with `engine.fusion.FEATURE_NAMES` is **refused**, not silently
  used — a permuted vector produces plausible numbers that are entirely wrong,
  which is the worst possible failure for a score that decides whether footage
  is destroyed.

---

## The feature contract

Defined once, in `engine/fusion.py::FEATURE_NAMES`, and imported by both the
training script and the live pipeline. A feature therefore cannot mean one
thing in training and another in production.

```
action_severity   action_top_conf   action_entropy    action_p_normal
t1_max_threat     t1_weapon_conf    t1_firesmoke_conf t1_persistence
t1_variety        ctx_people        ctx_person_conf   ctx_carriable
ctx_vehicle       motion_ratio      is_night          unattended
```

Reordering this list invalidates every trained checkpoint. That is deliberate:
the contract is enforced at load time rather than trusted.

---

## Relationship to the reference notebook

`docs/reference_cctv_pipeline_colab.py` is the original Colab export that this
integration is based on. It performs segmentation and per-model inference, and
states explicitly:

> This notebook does NOT do fusion/scoring yet — that decides High/Normal/Low
> importance and comes after this.

`engine/fusion.py` is that missing stage. The notebook's stage numbering
differs from the ITSO 5-stage pipeline (its "Stage 2" is action recognition,
which is ITSO Stage 4); `/api/models` reports both as `stage` and `ref_stage`
so the lineage stays visible without confusing the funnel.
