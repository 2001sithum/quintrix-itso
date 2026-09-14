"""suspicious_detector.py — Stage 3 Track 1: purpose-trained suspicious-object
detector (YOLOv8s, 8 classes).

This is the "Track 1" model from the CCTV segment+inference pipeline
(`docs/reference_cctv_pipeline_colab.py`). Unlike the stock COCO detector in
`object_detector.py`, these weights were fine-tuned specifically on
surveillance-relevant threat objects, so they carry direct security semantics:

    Gun, Knife, Rifle, Fire, Smoke, Unattended_Bag, Mask, Broken_Glass

Aggregation follows the reference pipeline exactly: sample ~1 frame per second
across the segment rather than every frame (suspicious objects do not appear
and vanish within a single frame, and per-frame YOLO is the dominant cost),
then keep per-class `max_conf` plus `frame_hits` — the number of *sampled*
frames the class appeared in. `frame_hits` is what separates a one-frame false
positive from an object that is genuinely present for the whole segment, and
the fusion stage weights it accordingly (see `engine/fusion.py`).

Weights load from `weights/yolov8s_suspicious.pt`, overridable with the
`SUSPICIOUS_WEIGHTS` env var. If the file is absent the module degrades to
returning no detections rather than raising, per the failure-isolation rule in
docs/ARCHITECTURE.md.
"""
import os
import threading

_model = None
_lock = threading.Lock()

_ROOT = os.path.dirname(os.path.dirname(__file__))
_WEIGHTS_PATH = os.environ.get(
    "SUSPICIOUS_WEIGHTS", os.path.join(_ROOT, "weights", "yolov8s_suspicious.pt")
)

# Class set baked into the checkpoint. Declared here too so the fusion stage and
# the UI can reason about threat semantics without loading 22MB of weights.
CLASSES = ["Gun", "Knife", "Rifle", "Fire", "Smoke",
           "Unattended_Bag", "Mask", "Broken_Glass"]

# Per-class threat weight used by the fusion stage. Weapons are the top band;
# fire/smoke are life-safety hazards; the rest are suspicious-but-ambiguous.
THREAT_WEIGHTS = {
    "Gun": 1.00, "Rifle": 1.00, "Knife": 0.85,
    "Fire": 0.90, "Smoke": 0.65,
    "Broken_Glass": 0.45, "Unattended_Bag": 0.55, "Mask": 0.40,
}

# Classes that constitute an immediate life-safety hazard (FR23).
CRITICAL = {"Gun", "Rifle", "Knife", "Fire"}


def available():
    """Whether the Track 1 checkpoint is on disk — lets the API and UI report
    backend availability without paying the model-load cost."""
    return os.path.exists(_WEIGHTS_PATH)


def weights_path():
    return _WEIGHTS_PATH


def _load():
    global _model
    with _lock:
        if _model is None:
            from ultralytics import YOLO
            _model = YOLO(_WEIGHTS_PATH)
        return _model


def detect_detailed(frames, conf=0.35, stride=1, segment_seconds=15.0):
    """Run Track 1 and return both the aggregate and a per-frame timeline.

    The aggregate is what the fusion stage scores on. The **timeline** is what
    makes the result visible: one entry per sampled frame, carrying the frame's
    offset in seconds and every box found in it, normalised to 0..1 of the frame
    dimensions.

    Normalised coordinates matter — the overlay is drawn over a `<video>`
    element whose displayed size has nothing to do with the frame size the model
    saw, so pixel coordinates would be unusable on the client.

    Returns {"aggregate": [...], "timeline": [{t, boxes: [...]}, ...]}.
    """
    empty = {"aggregate": [], "timeline": []}
    if not frames or not available():
        return empty
    try:
        model = _load()
    except Exception as e:                                  # pragma: no cover
        print("Track 1 (suspicious) load failed:", e)
        return empty

    step = max(1, int(stride))
    # Map a buffer index back to its position in the segment. The buffer is a
    # decimated sample spanning the whole segment, so index/len is the fraction
    # of the way through it.
    span = max(1, len(frames) - 1)
    agg, timeline = {}, []

    for i in range(0, len(frames), step):
        f = frames[i]
        h, w = f.shape[:2]
        try:
            r = model.predict(f, conf=conf, verbose=False)[0]
        except Exception:
            continue
        seen, boxes = set(), []
        for b in r.boxes:
            label = model.names[int(b.cls)]
            c = float(b.conf)
            x1, y1, x2, y2 = (float(v) for v in b.xyxy[0])
            entry = agg.setdefault(label, {
                "label": label, "confidence": 0.0, "frame_hits": 0,
                "bbox": [], "critical": label in CRITICAL,
                "threat_weight": THREAT_WEIGHTS.get(label, 0.5),
            })
            if c > entry["confidence"]:
                entry["confidence"] = round(c, 3)
                entry["bbox"] = [round(v, 1) for v in (x1, y1, x2, y2)]
            boxes.append({
                "label": label, "conf": round(c, 3), "track": 1,
                "critical": label in CRITICAL,
                "box": [round(x1 / w, 4), round(y1 / h, 4),
                        round(x2 / w, 4), round(y2 / h, 4)],
            })
            seen.add(label)
        for label in seen:
            agg[label]["frame_hits"] += 1
        if boxes:
            timeline.append({"t": round(i / span * segment_seconds, 2), "boxes": boxes})

    out = list(agg.values())
    out.sort(key=lambda d: d["confidence"], reverse=True)
    return {"aggregate": out, "timeline": timeline}


def detect(frames, conf=0.35, stride=1):
    """Aggregate-only Track 1 detections — the shape the fusion stage consumes."""
    return detect_detailed(frames, conf, stride)["aggregate"]


def frames_sampled(n_frames, stride):
    """How many frames `detect` would actually run YOLO on — used by the
    per-stage reduction telemetry to report compute reduction honestly."""
    if n_frames <= 0:
        return 0
    return len(range(0, n_frames, max(1, int(stride))))
