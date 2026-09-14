"""sentiment_model.py — inference for the trained ITSO sentiment model.

Loads `weights/sentiment_mlp.pt` (produced by `training/train_sentiment.py`)
and scores the 16-D fusion feature vector.

The checkpoint carries its own `feature_names`. This module **refuses to load a
checkpoint whose feature list disagrees with `engine.fusion.FEATURE_NAMES`**
rather than silently scoring a permuted vector — a mismatch there produces
plausible-looking numbers that are entirely wrong, which is the worst possible
failure mode for a score that decides whether footage is destroyed.

If the checkpoint is missing or rejected, `predict()` returns `(score, "rule")`
using the analytic fallback, so the pipeline still runs on a fresh clone that
has not trained a model yet.
"""
import os
import threading

_model = None
_meta = None
_status = "unloaded"
_lock = threading.Lock()

_ROOT = os.path.dirname(os.path.dirname(__file__))
_CKPT = os.environ.get("SENTIMENT_WEIGHTS",
                       os.path.join(_ROOT, "weights", "sentiment_mlp.pt"))


def available():
    return os.path.exists(_CKPT)


def checkpoint_path():
    return _CKPT


def status():
    """One of: unloaded, ok, missing, mismatch, error — surfaced by
    `GET /api/models` so an operator can see which head is really scoring."""
    return _status


def metadata():
    """Checkpoint metadata (arch, training date, held-out metrics) without the
    tensors, for the API and the Workflow Simulator's model cards."""
    _load()
    if not _meta:
        return None
    return {k: v for k, v in _meta.items() if k != "state_dict"}


def _load():
    global _model, _meta, _status
    with _lock:
        if _model is not None or _status in ("missing", "mismatch", "error"):
            return _model
        if not os.path.exists(_CKPT):
            _status = "missing"
            return None
        try:
            import torch
            from engine import fusion
            from training.train_sentiment import SentimentNet

            ckpt = torch.load(_CKPT, map_location="cpu", weights_only=False)
            names = ckpt.get("feature_names")
            if names != fusion.FEATURE_NAMES:
                # Loud, specific, and fatal for this backend — see module docstring.
                print("Sentiment checkpoint feature mismatch; refusing to load.\n"
                      f"  checkpoint: {names}\n  fusion:     {fusion.FEATURE_NAMES}")
                _status = "mismatch"
                return None
            net = SentimentNet(len(names), tuple(ckpt.get("hidden", (64, 32))))
            net.load_state_dict(ckpt["state_dict"])
            _model = net.eval()
            _meta = {k: v for k, v in ckpt.items() if k != "state_dict"}
            _status = "ok"
        except Exception as e:                              # pragma: no cover
            print("Sentiment model load failed:", e)
            _status = "error"
        return _model


def rule_score(feats):
    """Analytic fallback used when no trained checkpoint is usable.

    This is the arithmetic the trained model replaced. It is kept because a
    fresh clone must still produce a sane score, and because the Workflow
    Simulator shows both side by side so the contribution of training is
    visible rather than asserted.
    """
    s = 0.10
    s += 0.35 * feats.get("action_severity", 0.0)
    s += 0.25 * feats.get("t1_max_threat", 0.0) * (0.6 + 0.4 * feats.get("t1_persistence", 0.0))
    s += 0.12 * feats.get("t1_weapon_conf", 0.0)
    s += 0.10 * feats.get("t1_firesmoke_conf", 0.0)
    s += 0.08 * feats.get("ctx_people", 0.0)
    s += 0.05 * feats.get("unattended", 0.0)
    s += 0.04 * feats.get("is_night", 0.0)
    s -= 0.20 * feats.get("action_p_normal", 0.0)
    return max(0.0, min(1.0, round(s, 4)))


def predict(feats):
    """Score one feature dict.

    Returns `(score, backend)` where backend is "mlp" or "rule", so every
    caller — and the UI — can tell which head produced the number.
    """
    from engine import fusion

    model = _load()
    if model is None:
        return rule_score(feats), "rule"
    try:
        import torch
        x = torch.tensor([fusion.feature_vector(feats)], dtype=torch.float32)
        with torch.no_grad():
            return round(float(model(x)[0]), 4), "mlp"
    except Exception as e:                                  # pragma: no cover
        print("Sentiment inference failed:", e)
        return rule_score(feats), "rule"


def predict_batch(feat_dicts):
    """Vectorised scoring — one forward pass for many segments. Used by the
    threshold-calibration sweep, which scores thousands of segments."""
    from engine import fusion

    model = _load()
    if model is None:
        return [rule_score(f) for f in feat_dicts], "rule"
    try:
        import torch
        x = torch.tensor([fusion.feature_vector(f) for f in feat_dicts],
                         dtype=torch.float32)
        with torch.no_grad():
            return [round(float(v), 4) for v in model(x)], "mlp"
    except Exception as e:                                  # pragma: no cover
        print("Sentiment batch inference failed:", e)
        return [rule_score(f) for f in feat_dicts], "rule"


def label_for(score):
    """Coarse label kept for the legacy `sentiment_label` DB column."""
    if score >= 0.7:
        return "negative"
    if score >= 0.45:
        return "tense"
    return "neutral"
