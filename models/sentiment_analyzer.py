"""sentiment_analyzer.py — MobileNetV3 scene sentiment + threat level (FR13)."""
import threading

_model = None
_tf = None
_lock = threading.Lock()
_DEVICE = "cpu"
_WEAPONS = {"knife", "scissors", "baseball bat", "gun"}


def _load():
    global _model, _tf
    with _lock:
        if _model is None:
            import torch
            from torchvision.models import (mobilenet_v3_small,
                                            MobileNet_V3_Small_Weights)
            w = MobileNet_V3_Small_Weights.IMAGENET1K_V1
            _model = mobilenet_v3_small(weights=w).eval().to(_DEVICE)
            _tf = w.transforms()
        return _model, _tf


def analyze(frames, objects):
    """Return (score 0..1, label, threat_level). Combines a MobileNetV3 scene
    activation with an object-aware heuristic head (no training needed)."""
    activation = 0.4
    try:
        import cv2, torch
        from PIL import Image
        model, tf = _load()
        mid = frames[len(frames) // 2]
        img = Image.fromarray(cv2.cvtColor(mid, cv2.COLOR_BGR2RGB))
        with torch.no_grad():
            feat = model(tf(img).unsqueeze(0).to(_DEVICE))
            activation = float(torch.sigmoid(feat.mean()))
    except Exception as e:
        print("MobileNet inference failed:", e)

    labels = {o["label"] for o in objects}
    weapon = bool(labels & _WEAPONS)
    crowd = sum(1 for o in objects if o["label"] == "person")
    score = 0.2 + 0.4 * activation
    if weapon:
        score += 0.4
    if crowd >= 3:
        score += 0.15
    score = min(1.0, round(score, 3))
    if score >= 0.7:
        return score, "negative", "high"
    if score >= 0.45:
        return score, "tense", "medium"
    return score, "neutral", "low"
