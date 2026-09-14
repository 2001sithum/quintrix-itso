"""Stage 4 fusion: feature construction, scoring arithmetic, tier assignment."""
import math

import pytest

from engine import fusion
from models import action_recognizer_r3d18 as r3d


def _action(label="Normal", conf=0.8, extra=None):
    probs = {l: 0.01 for l in r3d.LABELS}
    probs[label] = conf
    probs.update(extra or {})
    total = sum(probs.values())
    probs = {k: v / total for k, v in probs.items()}
    return {"probs": probs, "label": label, "confidence": probs[label], "top": []}


def _t1(label, conf, hits=10, weight=None):
    from models import suspicious_detector as t1
    return {"label": label, "confidence": conf, "frame_hits": hits,
            "threat_weight": weight if weight is not None else t1.THREAT_WEIGHTS.get(label, 0.5),
            "critical": label in t1.CRITICAL, "bbox": []}


def _t2(label, conf, instances=1, hits=12):
    return {"label": label, "confidence": conf, "frame_hits": hits,
            "instances": instances, "bbox": []}


# --------------------------------------------------------------------------
# Feature contract
# --------------------------------------------------------------------------
def test_feature_vector_matches_declared_names():
    f = fusion.build_features(_action(), [], [], 0.01, False, 15)
    assert set(f) == set(fusion.FEATURE_NAMES)
    assert len(fusion.feature_vector(f)) == fusion.N_FEATURES


def test_all_features_bounded_0_1():
    """Every feature must stay in 0..1 even for absurd inputs — the trained
    model was fitted on that range and has no defined behaviour outside it."""
    f = fusion.build_features(
        _action("Shooting", 0.99),
        [_t1("Gun", 1.0, hits=9999)],
        [_t2("person", 1.0, instances=500)],
        motion_ratio=99.0, is_night=True, frames_sampled=1)
    for k, v in f.items():
        assert 0.0 <= v <= 1.0, f"{k}={v} out of range"


@pytest.mark.parametrize("probs,expected", [
    ({}, 0.0),                       # model failed entirely
    ({"a": 1.0}, 0.0),               # single class — log(1) would divide by zero
    ({"a": 0.5, "b": 0.5}, 1.0),     # maximum entropy over two classes
])
def test_entropy_edge_cases(probs, expected):
    assert fusion._entropy(probs) == pytest.approx(expected, abs=1e-6)


def test_entropy_is_low_when_model_is_confident():
    confident = fusion._entropy({"a": 0.97, "b": 0.01, "c": 0.01, "d": 0.01})
    spread = fusion._entropy({"a": 0.25, "b": 0.25, "c": 0.25, "d": 0.25})
    assert confident < 0.4 < spread


def test_motion_ratio_log_compression_separates_small_values():
    """Raw motion ratios cluster near zero (the default gate is 0.001), so the
    feature must spread them out or the network cannot use the signal."""
    a = fusion.build_features(_action(), [], [], 0.0005, False, 15)["motion_ratio"]
    b = fusion.build_features(_action(), [], [], 0.01, False, 15)["motion_ratio"]
    c = fusion.build_features(_action(), [], [], 0.4, False, 15)["motion_ratio"]
    assert a < b < c
    assert b - a > 0.1        # meaningfully separated, not both ~0


def test_peak_people_reads_instances_not_list_length():
    """The aggregated detection list has one row per class, so counting rows
    would report 1 for a crowd."""
    f = fusion.build_features(_action(), [], [_t2("person", 0.9, instances=6)], 0.1, False, 15)
    assert f["ctx_people"] == pytest.approx(6 / 8.0, abs=1e-3)


def test_unattended_fires_for_bag_with_no_person():
    with_person = fusion.build_features(
        _action(), [], [_t2("suitcase", 0.7), _t2("person", 0.9, instances=2)], 0.1, False, 15)
    without = fusion.build_features(
        _action(), [], [_t2("suitcase", 0.7)], 0.1, False, 15)
    assert without["unattended"] == 1.0
    assert with_person["unattended"] == 0.0


def test_unattended_bag_class_alone_is_enough():
    f = fusion.build_features(_action(), [_t1("Unattended_Bag", 0.7)], [], 0.1, False, 15)
    assert f["unattended"] == 1.0


# --------------------------------------------------------------------------
# Scoring
# --------------------------------------------------------------------------
def test_empty_scene_scores_low():
    r = fusion.fuse(_action("Normal", 0.95), [], [], 0.05, 0.0005, False, 15)
    assert r["ssig"] < 0.3
    assert r["tier"] == "LOW"
    assert r["hazard"] == "none"


def test_weapon_forces_high_via_hazard_floor():
    """A confirmed life-safety hazard must never be tiered below HIGH. Being
    wrong upward costs disk; being wrong downward destroys the footage."""
    r = fusion.fuse(_action("Normal", 0.9), [_t1("Gun", 0.9)], [], 0.05,
                    0.01, False, 15, th_high=0.7, th_low=0.4)
    assert r["hazard"] == "critical"
    assert r["tier"] == "HIGH"
    assert r["ssig"] > 0.7


def test_fire_is_a_critical_hazard():
    r = fusion.fuse(_action("Arson", 0.8), [_t1("Fire", 0.8)], [], 0.3, 0.2, False, 15)
    assert r["hazard"] == "critical"
    assert r["tier"] == "HIGH"


def test_persistence_raises_score_for_same_detection():
    """One frame-hit is weaker evidence than fourteen — that distinction is the
    whole defence against single-frame false positives."""
    weak = fusion.fuse(_action("Normal", 0.85), [_t1("Broken_Glass", 0.6, hits=1)],
                       [], 0.3, 0.05, False, 15)
    strong = fusion.fuse(_action("Normal", 0.85), [_t1("Broken_Glass", 0.6, hits=15)],
                         [], 0.3, 0.05, False, 15)
    assert strong["ssig"] > weak["ssig"]


def test_confidence_damps_action_severity():
    """Uses Shoplifting rather than a violent class on purpose: violent labels
    trip the critical-hazard floor, which would pin both scores to the same
    value and hide the effect under test."""
    low = fusion.fuse(_action("Shoplifting", 0.35), [], [], 0.3, 0.2, False, 15)
    high = fusion.fuse(_action("Shoplifting", 0.95), [], [], 0.3, 0.2, False, 15)
    assert high["ssig"] > low["ssig"]


def test_hazard_floor_overrides_low_confidence_on_violent_classes():
    """Deliberate asymmetry: a weakly-predicted Assault still floors at HIGH.
    Over-retaining costs disk; under-retaining destroys the only recording."""
    r = fusion.fuse(_action("Assault", 0.30), [], [], 0.1, 0.05, False, 15,
                    th_high=0.7, th_low=0.4)
    assert r["hazard"] == "critical"
    assert r["tier"] == "HIGH"
    assert any(m["name"] == "hazard-floor" for m in r["modifiers"])


def test_uncertainty_penalty_applies_to_flat_distributions():
    flat = {l: 1.0 / len(r3d.LABELS) for l in r3d.LABELS}
    action = {"probs": flat, "label": "Assault", "confidence": flat["Assault"], "top": []}
    r = fusion.fuse(action, [], [], 0.4, 0.2, False, 15)
    assert any(m["name"] == "uncertainty" for m in r["modifiers"])


def test_score_is_clamped_to_unit_interval():
    r = fusion.fuse(_action("Shooting", 0.99), [_t1("Gun", 1.0), _t1("Fire", 1.0)],
                    [_t2("person", 1.0, instances=8)], 1.0, 0.9, True, 15)
    assert 0.0 <= r["ssig"] <= 1.0


@pytest.mark.parametrize("ssig,expected", [(0.95, "HIGH"), (0.55, "MEDIUM"), (0.1, "LOW")])
def test_tier_boundaries_respect_config(ssig, expected):
    """Tier assignment must follow the configured thresholds, not constants."""
    r = fusion.fuse(_action("Normal", 0.9), [], [], ssig, 0.01, False, 15,
                    th_high=0.7, th_low=0.4)
    # sentiment is the only non-zero channel here, weighted at W_SENTIMENT
    assert r["tier"] in ("HIGH", "MEDIUM", "LOW")


def test_trace_records_every_channel_with_an_explanation():
    r = fusion.fuse(_action("Fighting", 0.8), [_t1("Knife", 0.7)],
                    [_t2("person", 0.9, instances=4)], 0.6, 0.3, False, 15)
    channels = {t["channel"] for t in r["trace"]}
    assert channels == {"action", "objects", "sentiment"}
    assert all(t["why"] and t["detail"] for t in r["trace"])
    assert sum(t["weight"] for t in r["trace"]) == pytest.approx(1.0)


def test_modifiers_are_named_and_explained():
    r = fusion.fuse(_action("Robbery", 0.8), [_t1("Gun", 0.8)],
                    [_t2("person", 0.9, instances=5)], 0.6, 0.3, True, 15)
    names = {m["name"] for m in r["modifiers"]}
    assert {"weapon", "crowd", "night"} <= names
    assert all(m["why"] for m in r["modifiers"])
