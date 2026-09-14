"""sentiment_dataset.py — training corpus for the ITSO sentiment model.

## What this corpus is, and what it is not

There is no public dataset that maps *"R3D-18 + Track 1 + Track 2 output for a
15-second CCTV segment"* to a human-assigned threat score, because that feature
space only exists once you have assembled this specific pipeline. Rather than
claim a labelled corpus we do not have, this module builds one explicitly, and
the honest description of it is:

    **Scenario-level expert labelling with distributional sampling.**

18 surveillance scenarios are defined below. Each carries a severity band
assigned from its harm profile (an armed robbery is more significant than a
crowd gathering), and a generator that samples *plausible model outputs* for
that scenario — including the messy ones: weak detections, one-frame false
positives, undecided action predictions. Labels get annotator-style noise so
the network learns a smooth ranking rather than memorising 18 constants.

So the model is a **calibrated distillation of an expert severity rubric over
realistic model-output distributions**, not a model trained on human-annotated
video. That distinction is stated in docs/MODELS.md and in the model card the
training run writes out. What it buys over the hand-written rule it replaces:

- it interpolates smoothly between scenarios instead of stepping at thresholds
- it learns feature *interactions* (a weapon with 1 frame-hit and a Normal
  action means something different from a weapon with 14 frame-hits)
- it is measurable — held-out MAE, rank correlation and tier accuracy are
  reported, so regressions in later tuning are visible

The features come from `engine.fusion.build_features`, the same function the
live pipeline calls, so a feature can never mean one thing in training and
another in production.
"""
import random

from engine import fusion
from models import action_recognizer_r3d18 as r3d
from models import suspicious_detector as t1mod

FRAMES_SAMPLED = 15          # ~1 fps over a 15s segment, matching the pipeline


# ---------------------------------------------------------------------------
# Sampling helpers
# ---------------------------------------------------------------------------
def _action(rng, dominant, conf_range, contenders=()):
    """Build a 14-class distribution peaked on `dominant`.

    The remaining mass goes mostly to `contenders` (classes a model genuinely
    confuses with the dominant one) and a little to everything else, which is
    what a real softmax over a fine-tuned network looks like.
    """
    top = rng.uniform(*conf_range)
    probs = {lbl: 0.0 for lbl in r3d.LABELS}
    probs[dominant] = top
    rest = 1.0 - top
    if contenders:
        share = rest * rng.uniform(0.5, 0.8)
        weights = [rng.random() + 0.1 for _ in contenders]
        tot = sum(weights)
        for c, w in zip(contenders, weights):
            probs[c] += share * w / tot
        rest -= share
    others = [l for l in r3d.LABELS if l != dominant]
    weights = [rng.random() + 0.05 for _ in others]
    tot = sum(weights)
    for o, w in zip(others, weights):
        probs[o] += rest * w / tot
    s = sum(probs.values())
    probs = {k: v / s for k, v in probs.items()}
    label = max(probs, key=probs.get)
    return {"probs": probs, "label": label, "confidence": probs[label], "top": []}


def _det1(rng, label, conf_range, hits_range):
    return {"label": label,
            "confidence": round(rng.uniform(*conf_range), 3),
            "frame_hits": rng.randint(*hits_range),
            "bbox": [], "critical": label in t1mod.CRITICAL,
            "threat_weight": t1mod.THREAT_WEIGHTS.get(label, 0.5)}


def _det2(rng, label, conf_range, hits_range, instances=1):
    return {"label": label,
            "confidence": round(rng.uniform(*conf_range), 3),
            "frame_hits": rng.randint(*hits_range),
            "bbox": [], "instances": instances}


def _people(rng, lo, hi, conf=(0.75, 0.97)):
    n = rng.randint(lo, hi)
    if n == 0:
        return []
    return [_det2(rng, "person", conf, (max(1, n), FRAMES_SAMPLED), instances=n)]


# ---------------------------------------------------------------------------
# Scenario definitions
#
# `severity` is the expert-assigned centre of the band; `spread` is the
# annotator disagreement around it. Both are on the same 0..1 scale the model
# predicts.
# ---------------------------------------------------------------------------
def _empty(rng):
    return (_action(rng, "Normal", (0.75, 0.96)), [], [],
            rng.uniform(0.0, 0.002), rng.random() < 0.4)


def _pedestrian(rng):
    return (_action(rng, "Normal", (0.65, 0.92), ("Stealing", "Shoplifting")),
            [], _people(rng, 1, 2), rng.uniform(0.003, 0.04), rng.random() < 0.3)


def _traffic(rng):
    t2 = _people(rng, 0, 2) + [_det2(rng, "car", (0.7, 0.95), (5, FRAMES_SAMPLED),
                                     instances=rng.randint(1, 4))]
    return (_action(rng, "Normal", (0.55, 0.88), ("RoadAccidents",)),
            [], t2, rng.uniform(0.02, 0.25), rng.random() < 0.35)


def _crowd(rng):
    return (_action(rng, "Normal", (0.45, 0.80), ("Fighting", "Arrest")),
            [], _people(rng, 4, 8), rng.uniform(0.05, 0.3), rng.random() < 0.3)


def _loitering_night(rng):
    return (_action(rng, "Normal", (0.40, 0.70), ("Burglary", "Stealing")),
            [], _people(rng, 1, 2), rng.uniform(0.004, 0.05), True)


def _shoplifting(rng):
    t2 = _people(rng, 1, 3) + [_det2(rng, "handbag", (0.45, 0.8), (2, 10))]
    return (_action(rng, "Shoplifting", (0.45, 0.80), ("Stealing", "Normal")),
            [], t2, rng.uniform(0.01, 0.09), False)


def _unattended_bag(rng):
    t1 = [_det1(rng, "Unattended_Bag", (0.45, 0.85), (6, FRAMES_SAMPLED))]
    t2 = [_det2(rng, "suitcase", (0.5, 0.85), (6, FRAMES_SAMPLED))]
    return (_action(rng, "Normal", (0.5, 0.85), ("Burglary",)),
            t1, t2, rng.uniform(0.001, 0.02), rng.random() < 0.4)


def _vandalism(rng):
    t1 = [_det1(rng, "Broken_Glass", (0.4, 0.8), (3, FRAMES_SAMPLED))]
    return (_action(rng, "Vandalism", (0.45, 0.82), ("Burglary", "Arson")),
            t1, _people(rng, 1, 3), rng.uniform(0.05, 0.35), rng.random() < 0.6)


def _burglary(rng):
    t1 = []
    if rng.random() < 0.4:
        t1.append(_det1(rng, "Mask", (0.4, 0.75), (4, FRAMES_SAMPLED)))
    return (_action(rng, rng.choice(["Burglary", "Stealing"]), (0.45, 0.85),
                    ("Robbery", "Shoplifting")),
            t1, _people(rng, 1, 3), rng.uniform(0.02, 0.2), rng.random() < 0.7)


def _road_accident(rng):
    t2 = _people(rng, 1, 5) + [_det2(rng, "car", (0.6, 0.95), (4, FRAMES_SAMPLED),
                                     instances=rng.randint(1, 3))]
    t1 = [_det1(rng, "Smoke", (0.35, 0.7), (2, 8))] if rng.random() < 0.35 else []
    return (_action(rng, "RoadAccidents", (0.5, 0.9), ("Explosion", "Normal")),
            t1, t2, rng.uniform(0.15, 0.6), rng.random() < 0.4)


def _fight(rng):
    t1 = [_det1(rng, "Broken_Glass", (0.35, 0.6), (1, 5))] if rng.random() < 0.2 else []
    return (_action(rng, rng.choice(["Fighting", "Assault", "Abuse"]), (0.5, 0.92),
                    ("Arrest", "Robbery")),
            t1, _people(rng, 2, 6), rng.uniform(0.2, 0.75), rng.random() < 0.45)


def _masked_intruder(rng):
    t1 = [_det1(rng, "Mask", (0.5, 0.9), (5, FRAMES_SAMPLED))]
    if rng.random() < 0.3:
        t1.append(_det1(rng, "Knife", (0.35, 0.6), (1, 4)))
    return (_action(rng, rng.choice(["Burglary", "Robbery"]), (0.5, 0.88),
                    ("Stealing", "Assault")),
            t1, _people(rng, 1, 3), rng.uniform(0.05, 0.4), True)


def _armed_robbery(rng):
    weapon = rng.choice(["Gun", "Knife", "Rifle"])
    t1 = [_det1(rng, weapon, (0.55, 0.93), (6, FRAMES_SAMPLED))]
    if rng.random() < 0.4:
        t1.append(_det1(rng, "Mask", (0.4, 0.8), (4, FRAMES_SAMPLED)))
    return (_action(rng, "Robbery", (0.5, 0.92), ("Assault", "Shooting")),
            t1, _people(rng, 2, 6), rng.uniform(0.1, 0.6), rng.random() < 0.5)


def _shooting(rng):
    t1 = [_det1(rng, rng.choice(["Gun", "Rifle"]), (0.6, 0.96), (7, FRAMES_SAMPLED))]
    return (_action(rng, "Shooting", (0.55, 0.95), ("Assault", "Robbery")),
            t1, _people(rng, 2, 8), rng.uniform(0.25, 0.9), rng.random() < 0.5)


def _fire(rng):
    t1 = [_det1(rng, "Fire", (0.5, 0.93), (5, FRAMES_SAMPLED)),
          _det1(rng, "Smoke", (0.45, 0.9), (6, FRAMES_SAMPLED))]
    return (_action(rng, "Arson", (0.45, 0.9), ("Explosion", "Vandalism")),
            t1, _people(rng, 0, 4), rng.uniform(0.2, 0.8), rng.random() < 0.5)


def _explosion(rng):
    t1 = [_det1(rng, "Fire", (0.55, 0.95), (4, FRAMES_SAMPLED)),
          _det1(rng, "Smoke", (0.6, 0.97), (7, FRAMES_SAMPLED))]
    return (_action(rng, "Explosion", (0.5, 0.95), ("Arson", "Shooting")),
            t1, _people(rng, 0, 6), rng.uniform(0.4, 1.0), rng.random() < 0.4)


def _ambiguous(rng):
    """Deliberately undecided: the action head spreads mass across many classes
    and detections are weak. Teaches the model to hedge rather than commit."""
    probs = {lbl: rng.uniform(0.4, 1.0) for lbl in r3d.LABELS}
    s = sum(probs.values())
    probs = {k: v / s for k, v in probs.items()}
    label = max(probs, key=probs.get)
    action = {"probs": probs, "label": label, "confidence": probs[label], "top": []}
    t1 = [_det1(rng, rng.choice(t1mod.CLASSES), (0.35, 0.48), (1, 3))] \
        if rng.random() < 0.5 else []
    return action, t1, _people(rng, 0, 3), rng.uniform(0.01, 0.3), rng.random() < 0.5


def _false_positive_weapon(rng):
    """A weapon class firing on a single sampled frame while the action model
    says Normal — the classic false positive. The label stays moderate, which
    is what teaches the network that `t1_persistence` matters."""
    t1 = [_det1(rng, rng.choice(["Gun", "Knife", "Rifle"]), (0.36, 0.55), (1, 2))]
    return (_action(rng, "Normal", (0.6, 0.9), ("Robbery",)),
            t1, _people(rng, 1, 3), rng.uniform(0.01, 0.15), rng.random() < 0.4)


SCENARIOS = [
    # (name, generator, expert severity, annotator spread, sampling weight)
    ("empty_scene",           _empty,                 0.04, 0.03, 1.4),
    ("routine_pedestrian",    _pedestrian,            0.12, 0.05, 1.4),
    ("routine_traffic",       _traffic,               0.16, 0.06, 1.2),
    ("crowd_gathering",       _crowd,                 0.34, 0.08, 1.0),
    ("loitering_night",       _loitering_night,       0.40, 0.08, 0.9),
    ("shoplifting",           _shoplifting,           0.50, 0.08, 0.9),
    ("unattended_bag",        _unattended_bag,        0.58, 0.08, 0.9),
    ("vandalism",             _vandalism,             0.56, 0.08, 0.9),
    ("burglary_stealing",     _burglary,              0.62, 0.08, 1.0),
    ("road_accident",         _road_accident,         0.71, 0.08, 0.9),
    ("fight_assault",         _fight,                 0.79, 0.07, 1.1),
    ("masked_intruder",       _masked_intruder,       0.73, 0.08, 0.9),
    ("armed_robbery",         _armed_robbery,         0.91, 0.05, 1.1),
    ("shooting",              _shooting,              0.97, 0.03, 1.0),
    ("fire_smoke",            _fire,                  0.88, 0.06, 1.0),
    ("explosion",             _explosion,             0.96, 0.04, 0.8),
    ("ambiguous_lowconf",     _ambiguous,             0.36, 0.12, 1.1),
    ("false_positive_weapon", _false_positive_weapon, 0.44, 0.10, 1.1),
]

SCENARIO_NAMES = [s[0] for s in SCENARIOS]


def generate(n_samples=12000, seed=17):
    """Build the corpus.

    Returns (X, y, scenario_index) where X rows are in `fusion.FEATURE_NAMES`
    order — produced by the live pipeline's own feature builder, not a
    reimplementation of it.
    """
    rng = random.Random(seed)
    weights = [s[4] for s in SCENARIOS]
    X, y, groups = [], [], []
    for _ in range(n_samples):
        i = rng.choices(range(len(SCENARIOS)), weights=weights, k=1)[0]
        name, gen, severity, spread, _w = SCENARIOS[i]
        action, t1, t2, motion, night = gen(rng)
        feats = fusion.build_features(action, t1, t2, motion, night, FRAMES_SAMPLED)
        label = min(1.0, max(0.0, rng.gauss(severity, spread)))
        X.append(fusion.feature_vector(feats))
        y.append(label)
        groups.append(i)
    return X, y, groups
