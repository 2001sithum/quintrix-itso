"""image_detector.py — single-image detection for the Image Lab (upload a
still, get boxes back).

The video pipeline only ever sees frames through the segment → motion-gate →
fusion path, so there was no way to ask "what do the detectors see in *this*
picture?" without cutting it into a fake video. This module runs the same two
detectors directly on one image:

* **Track 1** — the fine-tuned threat detector (`weights/yolov8s_suspicious.pt`)
* **COCO**    — YOLO11m, all 80 classes. Unlike Track 2 in the pipeline this
  does *not* filter to the seven context classes: on a still the operator
  wants to see everything the model found, and the context subset is flagged
  per box instead.

It keeps its own model instances rather than borrowing the pipeline's. An
ultralytics predictor holds per-call state, so sharing one with a job worker
that is mid-segment would race; two extra instances cost ~60MB of RAM, which
is cheap next to corrupting a running job's detections.

Boxes come back both in pixels and normalised to 0..1, so the client can draw
them over the image at whatever size it is displayed.
"""
import os
import threading
import time

import numpy as np

from models import device
from models import suspicious_detector as track1
from models import context_detector as track2

_models = {}
_load_lock = threading.Lock()
_infer_lock = threading.Lock()

# Keep huge phone photos from blowing up decode memory; YOLO letterboxes to
# `imgsz` anyway, so anything past this only costs time.
MAX_SIDE = 4096


def _load(kind):
    with _load_lock:
        if kind not in _models:
            from ultralytics import YOLO
            path = track1.weights_path() if kind == "track1" else track2.weights_path()
            _models[kind] = YOLO(path)
        return _models[kind]


_warm = {"state": "cold"}


def warmup():
    """Load both models and run one dummy inference in the background.

    The first MPS/CUDA call compiles kernels and takes seconds (≈7s on an M-series
    Mac versus ≈0.1s warm). Paying that when the Image Lab opens, rather than on
    the operator's first upload, makes the first result feel as fast as the rest.
    Idempotent — only the first call starts a thread.
    """
    with _load_lock:
        if _warm["state"] != "cold":
            return _warm["state"]
        _warm["state"] = "warming"

    def run():
        try:
            dummy = np.zeros((320, 320, 3), dtype=np.uint8)
            detect(dummy, use_track1=track1.available(), use_coco=True)
            _warm["state"] = "ready"
        except Exception as e:                          # pragma: no cover
            print("Image Lab warm-up failed:", e)
            _warm["state"] = "cold"
    threading.Thread(target=run, daemon=True, name="image-lab-warmup").start()
    return "warming"


def models_info():
    """What `/api/detect/image` will run, without loading any weights."""
    return {
        "device": device.select(),
        "accelerator": device.describe(),
        "warm": _warm["state"],
        "track1": {"name": "YOLOv8s (fine-tuned threat)",
                   "classes": track1.CLASSES,
                   "available": track1.available(),
                   "weights": os.path.basename(track1.weights_path())},
        "coco": {"name": track2.model_name() + " (COCO-pretrained)",
                 "classes": 80, "available": True,
                 "weights": os.path.basename(track2.weights_path())},
    }


def decode(data: bytes):
    """Bytes → BGR ndarray, or None if it is not a decodable image."""
    import cv2
    arr = np.frombuffer(data, dtype=np.uint8)
    img = cv2.imdecode(arr, cv2.IMREAD_COLOR)
    if img is None:
        return None
    h, w = img.shape[:2]
    if max(h, w) > MAX_SIDE:
        s = MAX_SIDE / max(h, w)
        img = cv2.resize(img, (int(w * s), int(h * s)), interpolation=cv2.INTER_AREA)
    return img


def _run(kind, img, conf, imgsz):
    model = _load(kind)
    with _infer_lock:
        r = model.predict(img, conf=conf, imgsz=imgsz, verbose=False,
                          device=device.select())[0]
    h, w = img.shape[:2]
    out = []
    for b in r.boxes:
        label = model.names[int(b.cls)]
        x1, y1, x2, y2 = (float(v) for v in b.xyxy[0])
        if kind == "track1":
            critical = label in track1.CRITICAL
            weight = track1.THREAT_WEIGHTS.get(label, 0.5)
        else:
            critical, weight = False, 0.0
        out.append({
            "label": label, "conf": round(float(b.conf), 4),
            "track": 1 if kind == "track1" else 2,
            "critical": critical, "threat_weight": weight,
            "context": kind == "coco" and label in track2.KEEP_CLASSES,
            "bbox": [round(x1, 1), round(y1, 1), round(x2, 1), round(y2, 1)],
            "box": [round(x1 / w, 4), round(y1 / h, 4),
                    round(x2 / w, 4), round(y2 / h, 4)],
        })
    return out


def detect(img, conf=0.35, use_track1=True, use_coco=True, imgsz=640):
    """Run the selected detectors on one BGR image.

    Returns {detections, summary, timings_ms, image, models}. A detector whose
    weights are missing or fail to load is reported in `errors` and skipped —
    one broken model must not blank the other's result.
    """
    h, w = img.shape[:2]
    conf = float(min(0.95, max(0.05, conf)))
    dets, timings, errors = [], {}, {}

    runs = []
    if use_track1:
        if track1.available():
            runs.append("track1")
        else:
            errors["track1"] = "threat-detector weights not found"
    if use_coco:
        runs.append("coco")

    for kind in runs:
        t0 = time.time()
        try:
            dets += _run(kind, img, conf, imgsz)
        except Exception as e:                          # pragma: no cover
            errors[kind] = f"{type(e).__name__}: {e}"
        timings[kind] = round((time.time() - t0) * 1000, 1)

    dets.sort(key=lambda d: (not d["critical"], d["track"], -d["conf"]))

    counts = {}
    for d in dets:
        c = counts.setdefault(d["label"], {"label": d["label"], "count": 0,
                                           "max_conf": 0.0, "track": d["track"],
                                           "critical": d["critical"]})
        c["count"] += 1
        c["max_conf"] = max(c["max_conf"], d["conf"])
    threats = [d for d in dets if d["track"] == 1]
    threat_score = max((d["conf"] * d["threat_weight"] for d in threats), default=0.0)

    return {
        "detections": dets,
        "summary": {
            "total": len(dets),
            "classes": sorted(counts.values(),
                              key=lambda c: (not c["critical"], c["track"], -c["count"])),
            "people": counts.get("person", {}).get("count", 0),
            "threats": len(threats),
            "critical": sum(1 for d in dets if d["critical"]),
            "threat_score": round(threat_score, 3),
            "verdict": ("CRITICAL" if any(d["critical"] for d in dets)
                        else "SUSPICIOUS" if threats else "CLEAR"),
        },
        "timings_ms": {**timings, "total": round(sum(timings.values()), 1)},
        "image": {"width": w, "height": h},
        "conf": conf,
        "errors": errors,
        "models": models_info(),
    }
