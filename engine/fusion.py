"""fusion.py — Stage 4: multi-model fusion, scoring and tier assignment.

The reference CCTV pipeline (`docs/reference_cctv_pipeline_colab.py`) stops
after per-model inference and says so explicitly:

    "This notebook does NOT do fusion/scoring yet - that decides
     High/Normal/Low importance and comes after this."

This module is that missing stage. It takes the three model outputs for one
15-second segment and produces a single decision:

    R3D-18 action distribution  ┐
    Track 1 suspicious objects  ├─► 16-D feature vector ─► sentiment (learned)
    Track 2 context objects     │                              │
    motion ratio / clock        ┘                              ▼
                                                     Ssig ─► HIGH/MEDIUM/LOW

Two design rules shape everything here:

1. **The feature vector is defined once, here.** `build_features()` is imported
   by both the training script and the live pipeline, so a feature can never
   drift between how the model was trained and how it is called. Reordering
   `FEATURE_NAMES` invalidates the checkpoint, which is why the trained file
   records its own feature list and `models/sentiment_model.py` refuses to load
   a checkpoint whose list disagrees.

2. **Every decision carries its reasons.** `fuse()` returns a `trace` listing
   each contribution to the score with a human-readable explanation. The
   Workflow Simulator renders it directly, and an operator reviewing a
   wrongly-tiered segment can see exactly which signal drove it — the
   reviewer's point about a wrong significance score being irreversible is much
   less dangerous when the score is auditable.
"""
import math

from models import action_recognizer_r3d18 as r3d
from models import suspicious_detector as t1mod
from models import context_detector as t2mod

# ---------------------------------------------------------------------------
# Feature specification — the contract between training and inference.
# ---------------------------------------------------------------------------
FEATURE_NAMES = [
    "action_severity",      # Σ p(class) × severity(class) over the 14 UCF-Crime classes
    "action_top_conf",      # top-1 confidence
    "action_entropy",       # normalized Shannon entropy — how undecided the model is
    "action_p_normal",      # probability mass on "Normal"
    "t1_max_threat",        # max over Track 1 of confidence × per-class threat weight
    "t1_weapon_conf",       # best Gun/Rifle/Knife confidence
    "t1_firesmoke_conf",    # best Fire/Smoke confidence
    "t1_persistence",       # best frame_hits / frames_sampled — sustained vs one-frame
    "t1_variety",           # distinct Track 1 classes / 8
    "ctx_people",           # peak simultaneous person count, saturating at 8
    "ctx_person_conf",      # best person confidence
    "ctx_carriable",        # a bag/case/backpack is present
    "ctx_vehicle",          # a car/motorcycle/bicycle is present
    "motion_ratio",         # Invaligator peak motion, log-compressed
    "is_night",             # night-hours flag from the segment timestamp
    "unattended",           # carriable object present with nobody near it
]
N_FEATURES = len(FEATURE_NAMES)

_WEAPONS = {"Gun", "Rifle", "Knife"}
_FIRESMOKE = {"Fire", "Smoke"}
_MAX_PEOPLE = 8.0


def _entropy(probs):
    """Shannon entropy normalized to 0..1 over the class count. High entropy
    means the action model could not commit, which should damp its influence.

    A distribution with fewer than two classes has no entropy to speak of and
    would divide by log(1) = 0, so it returns 0 — that happens when the action
    model failed and left an empty/degenerate distribution behind."""
    vals = [p for p in (probs or {}).values() if p > 0]
    if len(probs or {}) < 2 or not vals:
        return 0.0
    h = -sum(p * math.log(p) for p in vals)
    return round(min(1.0, h / math.log(len(probs))), 4)


def _best(dets, labels=None, key="confidence"):
    vals = [d.get(key, 0.0) for d in dets if labels is None or d["label"] in labels]
    return float(max(vals)) if vals else 0.0


def build_features(action, track1, track2, motion_ratio=0.0,
                   is_night=False, frames_sampled=1):
    """Assemble the 16-D feature vector for one segment.

    `action` is the dict from `action_recognizer_r3d18.predict()`; `track1` and
    `track2` are the detection lists from the two detector modules. Everything
    is clipped to 0..1 so the trained model sees a bounded input space no
    matter how odd a segment is.
    """
    probs = action.get("probs") or {}
    severity = sum(p * r3d.SEVERITY.get(lbl, 0.5) for lbl, p in probs.items())

    hits = max((d.get("frame_hits", 0) for d in track1), default=0)
    persistence = hits / max(1, frames_sampled)

    people = 0
    for d in track2:
        if d["label"] == "person":
            people = int(d.get("instances", 1))
            break

    carriable = any(d["label"] in t2mod.CARRIABLE for d in track2)
    vehicle = any(d["label"] in t2mod.VEHICLES for d in track2)
    bag_flagged = any(d["label"] == "Unattended_Bag" for d in track1)

    feats = {
        "action_severity": round(min(1.0, severity), 4),
        "action_top_conf": round(min(1.0, float(action.get("confidence", 0.0))), 4),
        "action_entropy": _entropy(probs),
        "action_p_normal": round(float(probs.get("Normal", 0.0)), 4),
        "t1_max_threat": round(min(1.0, max(
            (d["confidence"] * d.get("threat_weight", 0.5) for d in track1),
            default=0.0)), 4),
        "t1_weapon_conf": round(_best(track1, _WEAPONS), 4),
        "t1_firesmoke_conf": round(_best(track1, _FIRESMOKE), 4),
        "t1_persistence": round(min(1.0, persistence), 4),
        "t1_variety": round(min(1.0, len(track1) / len(t1mod.CLASSES)), 4),
        "ctx_people": round(min(1.0, people / _MAX_PEOPLE), 4),
        "ctx_person_conf": round(_best(track2, {"person"}), 4),
        "ctx_carriable": 1.0 if carriable else 0.0,
        "ctx_vehicle": 1.0 if vehicle else 0.0,
        # Motion ratio is a tiny fraction (the default gate is 0.001), so a raw
        # value would be indistinguishable from zero to the network. Log-compress
        # it into a usable 0..1 range instead.
        "motion_ratio": round(min(1.0, math.log10(1 + 999 * min(1.0, motion_ratio))
                                  / 3.0), 4),
        "is_night": 1.0 if is_night else 0.0,
        # An unattended object is a bag with nobody around it — either the
        # dedicated Track 1 class fired, or Track 2 sees a case and no person.
        "unattended": 1.0 if (bag_flagged or (carriable and people == 0)) else 0.0,
    }
    return feats


def feature_vector(feats):
    """Dict → list in the canonical `FEATURE_NAMES` order."""
    return [float(feats.get(n, 0.0)) for n in FEATURE_NAMES]


# ---------------------------------------------------------------------------
# Hazard classification (FR23)
# ---------------------------------------------------------------------------
def classify_hazard(track1, action):
    """Life-safety hazard level, driven by the Track 1 classes that represent
    immediate physical danger plus the most severe action classes."""
    labels = {d["label"] for d in track1}
    if labels & {"Fire", "Explosion"} or action.get("label") in ("Explosion", "Arson"):
        return "critical"
    if labels & _WEAPONS or action.get("label") in ("Shooting", "Assault"):
        return "critical"
    if "Smoke" in labels or action.get("label") in ("Fighting", "Abuse", "Robbery"):
        return "elevated"
    if labels or action.get("label") not in ("", "Normal"):
        return "moderate"
    return "none"


# ---------------------------------------------------------------------------
# Significance score
# ---------------------------------------------------------------------------
# Base weights over the three evidence channels. They sum to 1.0 before context
# modifiers so that a segment with no context boosts can never exceed 1.0 from
# the base term alone, which keeps the tier thresholds interpretable.
W_ACTION = 0.40
W_OBJECT = 0.35
W_SENTIMENT = 0.25


def fuse(action, track1, track2, sentiment_score, motion_ratio=0.0,
         is_night=False, frames_sampled=1, ctx_rules=None,
         th_high=0.7, th_low=0.4):
    """Fuse the three model outputs into a significance score and a tier.

    `sentiment_score` comes from the trained model (see
    `models/sentiment_model.py`); this function does not compute it, so the
    scoring stage stays testable without loading any checkpoint.

    Returns a dict with `ssig`, `tier`, `hazard`, `threat_level`, the feature
    vector, and a `trace` of every contribution with its explanation.
    """
    ctx_rules = ctx_rules or {}
    feats = build_features(action, track1, track2, motion_ratio,
                           is_night, frames_sampled)
    trace = []

    # --- Channel 1: action. Severity is damped by how confident the model is;
    # an uncertain "Assault" should not score like a certain one.
    confidence_damping = 0.5 + 0.5 * feats["action_top_conf"]
    action_term = feats["action_severity"] * confidence_damping
    trace.append({
        "channel": "action", "weight": W_ACTION,
        "value": round(action_term, 4),
        "detail": f"{action.get('label') or 'n/a'} "
                  f"@ {feats['action_top_conf']:.2f} conf",
        "why": "UCF-Crime class severity, damped by top-1 confidence",
    })

    # --- Channel 2: suspicious objects. A detection seen in one sampled frame
    # is treated as weaker evidence than one present throughout the segment.
    persistence_gain = 0.6 + 0.4 * feats["t1_persistence"]
    object_term = feats["t1_max_threat"] * persistence_gain
    t1_labels = ", ".join(d["label"] for d in track1[:3]) or "none"
    trace.append({
        "channel": "objects", "weight": W_OBJECT,
        "value": round(object_term, 4),
        "detail": t1_labels,
        "why": "highest threat-weighted Track 1 detection, scaled by persistence",
    })

    # --- Channel 3: learned sentiment.
    trace.append({
        "channel": "sentiment", "weight": W_SENTIMENT,
        "value": round(float(sentiment_score), 4),
        "detail": f"{sentiment_score:.2f}",
        "why": "trained sentiment model over the full 16-D feature vector",
    })

    ssig = (W_ACTION * action_term + W_OBJECT * object_term
            + W_SENTIMENT * float(sentiment_score))

    # --- Context modifiers. Additive, capped, and each one recorded.
    modifiers = []

    def add_mod(name, amount, why):
        nonlocal ssig
        if amount <= 0:
            return
        ssig += amount
        modifiers.append({"name": name, "amount": round(amount, 4), "why": why})

    if feats["t1_weapon_conf"] > 0:
        add_mod("weapon", ctx_rules.get("weapon_boost", 0.25) * feats["t1_weapon_conf"],
                "a firearm or blade was detected")
    if feats["t1_firesmoke_conf"] > 0:
        add_mod("fire", ctx_rules.get("fire_boost", 0.20) * feats["t1_firesmoke_conf"],
                "fire or smoke was detected")
    if feats["ctx_people"] >= 3 / _MAX_PEOPLE:
        add_mod("crowd", ctx_rules.get("crowd_boost", 0.15) * feats["ctx_people"],
                "three or more people present at once")
    if feats["is_night"]:
        add_mod("night", ctx_rules.get("night_boost", 0.10),
                "segment falls in night hours")
    if feats["unattended"]:
        add_mod("unattended", ctx_rules.get("unattended_boost", 0.12),
                "a bag or case appears with nobody attending it")

    # High action entropy means the action model was undecided — pull the score
    # back toward the middle rather than trusting a coin-flip label.
    if feats["action_entropy"] > 0.8 and feats["action_severity"] > 0.3:
        penalty = 0.10 * (feats["action_entropy"] - 0.8) / 0.2
        ssig -= penalty
        modifiers.append({"name": "uncertainty", "amount": round(-penalty, 4),
                          "why": "action model was highly undecided"})

    ssig = max(0.0, min(1.0, round(ssig, 4)))
    hazard = classify_hazard(track1, action)
    if hazard == "critical":
        # A confirmed life-safety hazard floors the score at HIGH regardless of
        # everything else. Being wrong upward costs disk; being wrong downward
        # destroys the only recording of a shooting.
        ssig = max(ssig, th_high + 0.01)
        modifiers.append({"name": "hazard-floor", "amount": 0.0,
                          "why": "critical hazard forces HIGH tier"})

    tier = "HIGH" if ssig > th_high else "MEDIUM" if ssig > th_low else "LOW"
    threat = ("high" if ssig >= 0.7 else "medium" if ssig >= 0.45 else "low")

    return {
        "ssig": ssig, "tier": tier, "hazard": hazard, "threat_level": threat,
        "features": feats, "trace": trace, "modifiers": modifiers,
        "sentiment_score": round(float(sentiment_score), 4),
    }
