"""action_recognizer.py — X3D-S action recognition (FR12), top-5 predictions."""
import threading, os, json
import numpy as np

_model = None
_labels = None
_lock = threading.Lock()
_DEVICE = "cpu"


def _load():
    global _model, _labels
    with _lock:
        if _model is None:
            import torch
            _model = torch.hub.load("facebookresearch/pytorchvideo",
                                    "x3d_s", pretrained=True)
            _model.eval().to(_DEVICE)
            path = os.path.join(os.path.dirname(__file__), "kinetics_labels.json")
            _labels = json.load(open(path)) if os.path.exists(path) else {}
        return _model


def recognize(frames, min_frames=16):
    """Top-5 action predictions. Empty list if under min_frames."""
    if len(frames) < min_frames:
        return []
    try:
        import cv2, torch
        model = _load()
        idxs = np.linspace(0, len(frames) - 1, 16).astype(int)
        clip = [cv2.resize(cv2.cvtColor(frames[i], cv2.COLOR_BGR2RGB), (182, 182))
                for i in idxs]
        x = np.stack(clip).astype(np.float32) / 255.0
        x = (x - np.array([0.45, 0.45, 0.45])) / np.array([0.225, 0.225, 0.225])
        x = torch.tensor(x).permute(3, 0, 1, 2).unsqueeze(0).float().to(_DEVICE)
        with torch.no_grad():
            probs = torch.softmax(model(x), dim=1)[0]
            top = torch.topk(probs, 5)
        out = []
        for score, i in zip(top.values.tolist(), top.indices.tolist()):
            out.append({"action": (_labels or {}).get(str(i), f"action_{i}"),
                        "confidence": round(float(score), 3)})
        return out
    except Exception as e:
        print("X3D inference failed:", e)
        return []
