"""action_recognizer_r3d18.py — Stage 2 action/anomaly recognition:
ResNet3D-18 fine-tuned on UCF-Crime (14 classes).

This is the action model of the CCTV segment+inference pipeline
(`docs/reference_cctv_pipeline_colab.py`) and, as of the pipeline integration,
**the default action backend of the deployed system**. The stock Kinetics-400
X3D-S model in `action_recognizer.py` is retained as a general-purpose
fallback. See docs/MODELS.md for the full reconciliation of the two — that
document is the single source of truth for which model runs where.

Why R3D-18 is the default: X3D-S predicts Kinetics-400 *everyday activity*
classes ("playing cricket", "washing dishes"), which carry no security
semantics — the significance score could only ever use its confidence, never
its label. R3D-18 predicts UCF-Crime classes (Assault, Robbery, Shooting, ...),
so the fusion stage can weight the *label itself*, which is what makes
`engine/fusion.py` possible at all.

Preprocessing matches the reference pipeline and the Stage 2 training recipe
exactly: 16 uniformly-spaced frames, resize to 128 then centre-crop to 112,
Kinetics normalization stats. Deviating on any of those silently degrades
accuracy without erroring, so they are pinned here as constants.
"""
import os
import threading

import numpy as np

_model = None
_loaded_from = None
_lock = threading.Lock()
_DEVICE = "cpu"  # matches the other model modules' convention

_ROOT = os.path.dirname(os.path.dirname(__file__))

# Search order: the pipeline-integrated checkpoint first, then the older
# project-root checkpoint. Both are 14-class UCF-Crime heads but they are
# distinct trainings (different SHA-256) — `loaded_from()` reports which one is
# actually live so the UI and the docs can never disagree about it.
_CANDIDATES = [
    os.environ.get("R3D18_WEIGHTS", ""),
    os.path.join(_ROOT, "weights", "r3d18_ucfcrime.pt"),
    os.path.join(_ROOT, "r3d18_best.pt"),
]

# UCF-Crime 14-class set, alphabetical (the ImageFolder ordering the Stage 2
# dataset builder used). Changing this order silently mislabels every
# prediction, so it is pinned rather than derived.
LABELS = [
    "Abuse", "Arrest", "Arson", "Assault", "Burglary", "Explosion", "Fighting",
    "Normal", "RoadAccidents", "Robbery", "Shooting", "Shoplifting", "Stealing",
    "Vandalism",
]

# Per-class severity used by the fusion stage. Derived from the harm profile of
# each UCF-Crime class: armed/violent-against-persons at the top, property
# crime in the middle, Normal at zero.
SEVERITY = {
    "Shooting": 1.00, "Explosion": 1.00, "Assault": 0.90, "Arson": 0.85,
    "Fighting": 0.85, "Abuse": 0.85, "Robbery": 0.80, "RoadAccidents": 0.70,
    "Burglary": 0.60, "Stealing": 0.55, "Vandalism": 0.50, "Shoplifting": 0.45,
    "Arrest": 0.40, "Normal": 0.00,
}

_MEAN = np.array([0.43216, 0.394666, 0.37645])
_STD = np.array([0.22803, 0.22145, 0.216989])
_CLIP_LEN = 16
_RESIZE = 128
_CROP = 112


def weights_path():
    """First candidate checkpoint that exists, or "" if none do."""
    for p in _CANDIDATES:
        if p and os.path.exists(p):
            return p
    return ""


def available():
    return bool(weights_path())


def loaded_from():
    """Path of the checkpoint actually loaded into memory, or None if the
    model has not been loaded yet."""
    return _loaded_from


def _extract_state_dict(raw):
    """Accept the three checkpoint shapes seen in practice: a bare state_dict,
    a dict wrapping one under `model_state_dict`/`state_dict`, and either of
    those saved from nn.DataParallel (a `module.` prefix on every key)."""
    if not isinstance(raw, dict):
        raise ValueError("Unrecognized checkpoint format: %s" % type(raw))
    sd = raw
    for key in ("model_state_dict", "state_dict"):
        if key in raw and isinstance(raw[key], dict):
            sd = raw[key]
            break
    return {(k[7:] if k.startswith("module.") else k): v for k, v in sd.items()}


def _load():
    global _model, _loaded_from
    with _lock:
        if _model is None:
            import torch
            from torchvision.models.video import r3d_18

            path = weights_path()
            if not path:
                raise FileNotFoundError("No R3D-18 checkpoint found")
            model = r3d_18(weights=None)
            model.fc = torch.nn.Linear(model.fc.in_features, len(LABELS))
            state = _extract_state_dict(
                torch.load(path, map_location="cpu", weights_only=False))
            missing, unexpected = model.load_state_dict(state, strict=False)
            if missing or unexpected:
                # Not fatal — a partial load still runs — but it means the
                # checkpoint and this architecture disagree, so say so loudly.
                print("R3D-18 load warning: %d missing / %d unexpected keys"
                      % (len(missing), len(unexpected)))
            _model = model.eval().to(_DEVICE)
            _loaded_from = path
        return _model


def _build_clip(frames):
    """16 uniformly-spaced frames -> normalized (1, C, T, H, W) tensor."""
    import cv2
    import torch

    idxs = np.linspace(0, len(frames) - 1, _CLIP_LEN).astype(int)
    off = (_RESIZE - _CROP) // 2
    clip = []
    for i in idxs:
        f = cv2.resize(cv2.cvtColor(frames[i], cv2.COLOR_BGR2RGB), (_RESIZE, _RESIZE))
        clip.append(f[off:off + _CROP, off:off + _CROP])
    x = np.stack(clip).astype(np.float32) / 255.0
    x = (x - _MEAN) / _STD
    return torch.tensor(x).permute(3, 0, 1, 2).unsqueeze(0).float().to(_DEVICE)


def predict(frames, min_frames=16):
    """Full prediction: top-5 list *and* the complete 14-class distribution.

    The fusion stage needs the whole distribution — a segment that is 45%
    Fighting / 40% Normal is a genuinely different situation from one that is
    95% Fighting, and top-1 confidence alone cannot express that.

    Returns {"top": [{action, confidence}], "probs": {label: p}, "label": str,
             "confidence": float} — empty structures on failure.
    """
    empty = {"top": [], "probs": {}, "label": "", "confidence": 0.0}
    if len(frames) < min_frames:
        return empty
    try:
        import torch
        model = _load()
        with torch.no_grad():
            probs = torch.softmax(model(_build_clip(frames)), dim=1)[0]
        pr = {LABELS[i]: round(float(p), 4) for i, p in enumerate(probs)}
        top = torch.topk(probs, min(5, len(LABELS)))
        top_list = [{"action": LABELS[i], "confidence": round(float(s), 3)}
                    for s, i in zip(top.values.tolist(), top.indices.tolist())]
        return {"top": top_list, "probs": pr,
                "label": top_list[0]["action"],
                "confidence": top_list[0]["confidence"]}
    except Exception as e:
        print("R3D-18 inference failed:", e)
        return empty


def recognize(frames, min_frames=16):
    """Top-5 only — the legacy shape shared with `action_recognizer.recognize`,
    kept so callers that predate the fusion stage keep working unchanged."""
    return predict(frames, min_frames)["top"]
