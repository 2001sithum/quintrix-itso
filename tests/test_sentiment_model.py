"""The trained sentiment model: contract enforcement, ranking behaviour, fallback."""
import pytest

from engine import fusion
from models import sentiment_model


def feats(**kw):
    f = {n: 0.0 for n in fusion.FEATURE_NAMES}
    f.update(kw)
    return f


def test_checkpoint_loads_and_reports_ok():
    if not sentiment_model.available():
        pytest.skip("no trained checkpoint — run python -m training.train_sentiment")
    sentiment_model.metadata()
    assert sentiment_model.status() == "ok"


def test_metadata_exposes_heldout_metrics():
    if not sentiment_model.available():
        pytest.skip("no trained checkpoint")
    m = sentiment_model.metadata()
    test = m["metrics"]["test"]
    assert test["n"] > 100
    # Guardrails, not targets: these catch a training regression, they are not
    # claims about real-world accuracy (see docs/MODEL_CARD_sentiment.md).
    assert test["mae"] < 0.12
    assert test["spearman"] > 0.85
    assert test["tier_accuracy"] > 0.70


def test_feature_contract_is_stored_in_the_checkpoint():
    if not sentiment_model.available():
        pytest.skip("no trained checkpoint")
    assert sentiment_model.metadata()["feature_names"] == fusion.FEATURE_NAMES


def test_scores_stay_in_unit_interval():
    for f in (feats(), feats(**{n: 1.0 for n in fusion.FEATURE_NAMES}),
              feats(action_severity=1.0, t1_weapon_conf=1.0)):
        score, _ = sentiment_model.predict(f)
        assert 0.0 <= score <= 1.0


def test_ranks_armed_incident_above_empty_scene():
    empty, _ = sentiment_model.predict(feats(action_p_normal=0.95, action_top_conf=0.95))
    armed, _ = sentiment_model.predict(feats(
        action_severity=0.95, action_top_conf=0.9, t1_max_threat=0.9,
        t1_weapon_conf=0.9, t1_persistence=0.85, ctx_people=0.6,
        ctx_person_conf=0.9, motion_ratio=0.6))
    assert armed > empty + 0.4


def test_persistence_separates_a_one_frame_false_positive():
    """The scenario the corpus was built to teach: a weapon class firing on a
    single sampled frame while the action model says Normal."""
    fp, _ = sentiment_model.predict(feats(
        action_p_normal=0.8, action_top_conf=0.8, t1_max_threat=0.45,
        t1_weapon_conf=0.45, t1_persistence=0.07, ctx_people=0.25,
        ctx_person_conf=0.9, motion_ratio=0.1))
    real, _ = sentiment_model.predict(feats(
        action_p_normal=0.05, action_severity=0.85, action_top_conf=0.85,
        t1_max_threat=0.9, t1_weapon_conf=0.9, t1_persistence=0.95,
        ctx_people=0.5, ctx_person_conf=0.9, motion_ratio=0.5))
    assert real > fp
    assert fp < 0.65, "a single-frame weapon hit must not score like a real one"


def test_monotone_in_weapon_confidence():
    prev = -1.0
    for c in (0.0, 0.3, 0.6, 0.9):
        s, _ = sentiment_model.predict(feats(
            t1_max_threat=c, t1_weapon_conf=c, t1_persistence=0.8,
            action_severity=0.5, action_top_conf=0.7))
        assert s >= prev - 0.02, "score should not fall as threat confidence rises"
        prev = s


def test_batch_matches_single_prediction():
    fs = [feats(action_severity=v, action_top_conf=0.8) for v in (0.1, 0.5, 0.9)]
    batch, backend = sentiment_model.predict_batch(fs)
    singles = [sentiment_model.predict(f)[0] for f in fs]
    assert backend == sentiment_model.predict(fs[0])[1]
    for b, s in zip(batch, singles):
        assert b == pytest.approx(s, abs=1e-4)


def test_rule_fallback_is_usable_on_its_own():
    """A fresh clone with no checkpoint must still produce a sane ordering."""
    empty = sentiment_model.rule_score(feats(action_p_normal=0.95))
    armed = sentiment_model.rule_score(feats(
        action_severity=0.9, t1_max_threat=0.9, t1_weapon_conf=0.9,
        t1_persistence=0.9, ctx_people=0.6))
    assert 0.0 <= empty < armed <= 1.0


def test_mismatched_feature_contract_is_refused(tmp_path, monkeypatch):
    """A checkpoint trained on a different feature order must be rejected, not
    silently used — a permuted vector yields plausible but wrong scores."""
    import importlib
    import torch
    from training.train_sentiment import SentimentNet

    bad = tmp_path / "bad.pt"
    torch.save({"state_dict": SentimentNet().state_dict(),
                "feature_names": list(reversed(fusion.FEATURE_NAMES)),
                "hidden": [64, 32]}, bad)

    monkeypatch.setenv("SENTIMENT_WEIGHTS", str(bad))
    mod = importlib.reload(sentiment_model)
    try:
        score, backend = mod.predict(feats(action_severity=0.9))
        assert backend == "rule"
        assert mod.status() == "mismatch"
    finally:
        monkeypatch.delenv("SENTIMENT_WEIGHTS", raising=False)
        importlib.reload(sentiment_model)
