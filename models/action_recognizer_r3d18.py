"""action_recognizer_r3d18.py — ResNet3D-18 (R3D-18) action/anomaly recognition,
ALTERNATIVE to the default X3D-S backend in action_recognizer.py (FR12).

This is an additive, opt-in module: it is only imported/used when config key
`action_model_backend` is set to "r3d18" (see engine/itso_engine.py dispatch).
The default pipeline path (X3D-S, action_recognizer.py) is untouched.

Weights: r3d18_best.pt at the project root — a torchvision `r3d_18`
(Kinetics-style video ResNet-18) fine-tuned with a 14-class head, matching the
UCF-Crime class set (13 anomaly classes + Normal). Same top-5 output shape as
the X3D backend so callers don't need to branch on which model produced it.
"""
import threading, os
import numpy as np

_model = None
_lock = threading.Lock()
_DEVICE = "cpu"  # matches the other model modules' convention (see object_detector.py, sentiment_analyzer.py)

_WEIGHTS_PATH = os.environ.get(
    "R3D18_WEIGHTS", os.path.join(os.path.dirname(os.path.dirname(__file__)), "r3d18_best.pt")
)

# UCF-Crime 14-class set, alphabetical order (standard dataset class index).
_LABELS = [
    "Abuse", "Arrest", "Arson", "Assault", "Burglary", "Explosion", "Fighting",
    "Normal", "RoadAccidents", "Robbery", "Shooting", "Shoplifting", "Stealing",
    "Vandalism",
]

# Kinetics-style normalization used by torchvision's video ResNet reference
# training recipe.
_MEAN = np.array([0.43216, 0.394666, 0.37645])
_STD = np.array([0.22803, 0.22145, 0.216989])
_CLIP_LEN = 16
_SIZE = 112


def available():
    """Whether the r3d18 checkpoint is present — lets callers/UI check before
    letting an operator select this backend."""
    return os.path.exists(_WEIGHTS_PATH)


def _load():
    global _model
    with _lock:
        if _model is None:
            import torch
            from torchvision.models.video import r3d_18

            model = r3d_18(weights=None)
            model.fc = torch.nn.Linear(model.fc.in_features, len(_LABELS))
            state = torch.load(_WEIGHTS_PATH, map_location="cpu")
            model.load_state_dict(state)
            _model = model.eval().to(_DEVICE)
        return _model


def recognize(frames, min_frames=16):
    """Top-5 action/anomaly predictions from the R3D-18 alternative backend.
    Same return shape as action_recognizer.recognize() — empty list if under
    min_frames or on any failure (failure isolation, per ARCHITECTURE.md)."""
    if len(frames) < min_frames:
        return []
    try:
        import cv2, torch
        model = _load()
        idxs = np.linspace(0, len(frames) - 1, _CLIP_LEN).astype(int)
        clip = [cv2.resize(cv2.cvtColor(frames[i], cv2.COLOR_BGR2RGB), (_SIZE, _SIZE))
                for i in idxs]
        x = np.stack(clip).astype(np.float32) / 255.0
        x = (x - _MEAN) / _STD
        # (T,H,W,C) -> (C,T,H,W) -> batch of 1
        x = torch.tensor(x).permute(3, 0, 1, 2).unsqueeze(0).float().to(_DEVICE)
        with torch.no_grad():
            probs = torch.softmax(model(x), dim=1)[0]
            top = torch.topk(probs, min(5, len(_LABELS)))
        out = []
        for score, i in zip(top.values.tolist(), top.indices.tolist()):
            out.append({"action": _LABELS[i] if i < len(_LABELS) else f"class_{i}",
                        "confidence": round(float(score), 3)})
        return out
    except Exception as e:
        print("R3D-18 inference failed:", e)
        return []
