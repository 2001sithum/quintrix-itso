"""object_detector.py — YOLOv8 object detection (FR11)."""
import threading, os

_model = None
_lock = threading.Lock()
_CRITICAL = {"knife", "scissors", "baseball bat", "gun", "fire hydrant",
             "backpack", "suitcase", "person", "car", "truck", "motorcycle"}


def _load():
    global _model
    with _lock:
        if _model is None:
            from ultralytics import YOLO
            _model = YOLO(os.environ.get("YOLO_WEIGHTS", "yolov8n.pt"))
        return _model


def detect(frames, conf=0.35):
    """Return de-duplicated best detections across a few sampled frames."""
    if not frames:
        return []
    try:
        model = _load()
    except Exception as e:
        print("YOLO load failed:", e)
        return []
    sample = frames[:: max(1, len(frames) // 4)][:4]
    best = {}
    for f in sample:
        try:
            r = model(f, verbose=False, conf=conf)[0]
        except Exception:
            continue
        for b in r.boxes:
            label = model.names[int(b.cls)]
            c = float(b.conf)
            xy = [round(float(x), 1) for x in b.xyxy[0]]
            if label not in best or c > best[label]["confidence"]:
                best[label] = {"label": label, "confidence": round(c, 3),
                               "bbox": xy, "critical": label in _CRITICAL}
    return list(best.values())


def critical_labels():
    return _CRITICAL
