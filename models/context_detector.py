"""context_detector.py — Stage 3 Track 2: COCO context objects.

"Track 2" from the CCTV segment+inference pipeline. Stock COCO-pretrained
YOLOv8 (no training needed), filtered down to the handful of classes that
actually carry surveillance context:

    person, backpack, handbag, suitcase, car, motorcycle, bicycle

Track 1 answers *"is there a threat object?"*; Track 2 answers *"who and what
else is in the scene?"* — a knife alone scores differently from a knife with
six people around it, and that separation is what lets the fusion stage apply
crowd and unattended-object reasoning without conflating threat with context.

Everything COCO detects outside `KEEP_CLASSES` is dropped at the box level, so
the ~72 irrelevant classes never reach the fusion stage. Same aggregation
contract as Track 1: per-class `max_conf` + `frame_hits` over ~1 fps sampling.
"""
import os
import threading

_model = None
_class_ids = None
_lock = threading.Lock()

_ROOT = os.path.dirname(os.path.dirname(__file__))

# Search order: a vendored copy under weights/, then the project root, then the
# bare name — which makes ultralytics fetch it on first run. The bare-name
# fallback is what keeps a fresh clone working with no manual download step.
_CANDIDATES = [
    os.environ.get("CONTEXT_WEIGHTS", ""),
    os.path.join(_ROOT, "weights", "yolov8s.pt"),
    os.path.join(_ROOT, "yolov8s.pt"),
]


def _resolve_weights():
    for p in _CANDIDATES:
        if p and os.path.exists(p):
            return p
    return "yolov8s.pt"

KEEP_CLASSES = {"person", "backpack", "handbag", "suitcase",
                "car", "motorcycle", "bicycle"}

# Classes that count as a carryable/leavable container — feeds the fusion
# stage's unattended-object reasoning together with Track 1's Unattended_Bag.
CARRIABLE = {"backpack", "handbag", "suitcase"}
VEHICLES = {"car", "motorcycle", "bicycle"}


def weights_path():
    return _resolve_weights()


def _load():
    global _model, _class_ids
    with _lock:
        if _model is None:
            from ultralytics import YOLO
            _model = YOLO(_resolve_weights())
            _class_ids = {i for i, n in _model.names.items() if n in KEEP_CLASSES}
        return _model, _class_ids


def detect_detailed(frames, conf=0.35, stride=1, segment_seconds=15.0):
    """Track 2 aggregate plus a per-frame timeline with normalised boxes.
    See `suspicious_detector.detect_detailed` for why coordinates are
    normalised rather than in pixels."""
    empty = {"aggregate": [], "timeline": []}
    if not frames:
        return empty
    try:
        model, keep = _load()
    except Exception as e:                                  # pragma: no cover
        print("Track 2 (context) load failed:", e)
        return empty

    step = max(1, int(stride))
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
            cls_id = int(b.cls)
            if cls_id not in keep:
                continue                        # drop the ~73 irrelevant COCO classes
            label = model.names[cls_id]
            c = float(b.conf)
            x1, y1, x2, y2 = (float(v) for v in b.xyxy[0])
            entry = agg.setdefault(label, {
                "label": label, "confidence": 0.0, "frame_hits": 0,
                "bbox": [], "instances": 0,
            })
            if c > entry["confidence"]:
                entry["confidence"] = round(c, 3)
                entry["bbox"] = [round(v, 1) for v in (x1, y1, x2, y2)]
            entry["instances"] = max(entry["instances"],
                                     sum(1 for bb in r.boxes if int(bb.cls) == cls_id))
            boxes.append({
                "label": label, "conf": round(c, 3), "track": 2, "critical": False,
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
    """Aggregate-only Track 2 detections."""
    return detect_detailed(frames, conf, stride)["aggregate"]


def peak_people(context_detections):
    """Highest simultaneous person count seen in any sampled frame — the crowd
    signal the fusion stage uses. Reading `instances` rather than counting list
    entries matters: the aggregated list has one row per class, so a naive
    len() would report 1 for a crowd of twelve."""
    for d in context_detections:
        if d["label"] == "person":
            return int(d.get("instances", 1))
    return 0
