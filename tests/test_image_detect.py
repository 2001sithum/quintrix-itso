"""Image Lab (single-image detection), device selection, and the model-loading
regressions that came with moving every model onto one device."""
import os

import cv2
import numpy as np
import pytest

from models import device, image_detector
from models import action_recognizer_r3d18 as r3d
from models import context_detector as track2
from models import suspicious_detector as track1


def _png(w=320, h=240):
    img = np.full((h, w, 3), 40, dtype=np.uint8)
    cv2.rectangle(img, (60, 60), (200, 180), (220, 220, 220), -1)
    ok, buf = cv2.imencode(".png", img)
    assert ok
    return buf.tobytes()


# --------------------------------------------------------------------------
# Device selection
# --------------------------------------------------------------------------
@pytest.fixture
def fresh_device(monkeypatch):
    device.reset()
    yield monkeypatch
    monkeypatch.delenv("QUINTRIX_DEVICE", raising=False)
    device.reset()


def test_device_override_is_honoured(fresh_device):
    fresh_device.setenv("QUINTRIX_DEVICE", "cpu")
    assert device.select() == "cpu"
    assert device.describe() == "CPU"


def test_invalid_device_override_falls_back_to_auto(fresh_device):
    fresh_device.setenv("QUINTRIX_DEVICE", "tpu9000")
    assert device.select() in ("cpu", "mps", "cuda")


def _mps_usable():
    import torch
    if not torch.backends.mps.is_available():
        return False
    try:
        torch.ones(1, device="mps")
        return True
    except Exception:
        return False


def test_auto_device_is_a_real_backend(fresh_device):
    fresh_device.delenv("QUINTRIX_DEVICE", raising=False)
    d = device.select()
    assert d in ("cpu", "mps", "cuda")
    import torch
    if _mps_usable() and not torch.cuda.is_available():
        assert d == "mps", "Apple-silicon GPU is available but was not selected"


def test_mps_that_cannot_allocate_falls_back_to_cpu(fresh_device):
    """Hosted macOS runners report MPS available and then fail every
    allocation; selecting it there would silently empty every detector."""
    import torch
    fresh_device.delenv("QUINTRIX_DEVICE", raising=False)
    fresh_device.setattr(torch.cuda, "is_available", lambda: False)
    fresh_device.setattr(torch.backends.mps, "is_available", lambda: True)
    fresh_device.setattr(torch.backends.mps, "is_built", lambda: True)

    def no_memory(*a, **k):
        raise RuntimeError("MPS backend out of memory (max allowed: 0 bytes)")
    fresh_device.setattr(torch, "ones", no_memory)
    assert device.select() == "cpu"


def test_mps_fallback_is_enabled():
    # ops without an MPS kernel must fall back to CPU instead of raising
    assert os.environ.get("PYTORCH_ENABLE_MPS_FALLBACK") == "1"


def test_engine_and_models_agree_on_device():
    from engine import itso_engine
    assert itso_engine.select_device() == device.select()


# --------------------------------------------------------------------------
# Regression: the MPS change deleted R3D-18's load lock
# --------------------------------------------------------------------------
def test_r3d18_module_has_its_load_lock():
    """`_load` does `with _lock:` — without the module global every
    prediction died with NameError and the fusion stage silently lost its
    action signal."""
    import threading
    assert isinstance(r3d._lock, type(threading.Lock()))


@pytest.mark.skipif(not r3d.available(), reason="R3D-18 checkpoint not present")
def test_r3d18_actually_predicts():
    frames = [np.random.randint(0, 255, (120, 160, 3), dtype=np.uint8) for _ in range(16)]
    out = r3d.predict(frames)
    assert out["label"] in r3d.LABELS
    assert abs(sum(out["probs"].values()) - 1.0) < 0.01


# --------------------------------------------------------------------------
# Track 2 weight resolution
# --------------------------------------------------------------------------
def test_context_detector_prefers_yolo11m_when_vendored():
    vendored = os.path.join(os.path.dirname(os.path.dirname(__file__)),
                            "weights", "yolo11m.pt")
    if os.path.exists(vendored) and not os.environ.get("CONTEXT_WEIGHTS"):
        assert track2.weights_path() == vendored
        assert track2.model_name() == "YOLO11m"
    else:
        assert track2.model_name()


# --------------------------------------------------------------------------
# image_detector unit
# --------------------------------------------------------------------------
def test_decode_rejects_garbage():
    assert image_detector.decode(b"definitely not an image") is None


def test_decode_downscales_huge_images():
    big = np.zeros((5000, 6000, 3), dtype=np.uint8)
    ok, buf = cv2.imencode(".jpg", big)
    img = image_detector.decode(buf.tobytes())
    assert max(img.shape[:2]) == image_detector.MAX_SIDE


def test_detect_summarises_and_orders(monkeypatch):
    fake = {
        "track1": [{"label": "Knife", "conf": 0.8, "track": 1, "critical": True,
                    "threat_weight": 0.85, "context": False,
                    "bbox": [0, 0, 10, 10], "box": [0, 0, .1, .1]}],
        "coco": [{"label": "person", "conf": 0.9, "track": 2, "critical": False,
                  "threat_weight": 0.0, "context": True,
                  "bbox": [0, 0, 10, 10], "box": [0, 0, .1, .1]}] * 2,
    }
    monkeypatch.setattr(image_detector, "_run", lambda k, *a: list(fake[k]))
    monkeypatch.setattr(track1, "available", lambda: True)
    res = image_detector.detect(np.zeros((100, 100, 3), np.uint8))
    s = res["summary"]
    assert s["total"] == 3 and s["people"] == 2 and s["threats"] == 1
    assert s["verdict"] == "CRITICAL"
    assert res["detections"][0]["label"] == "Knife", "critical threats sort first"
    assert s["threat_score"] == round(0.8 * 0.85, 3)


def test_detect_reports_missing_threat_weights(monkeypatch):
    monkeypatch.setattr(image_detector, "_run", lambda k, *a: [])
    monkeypatch.setattr(track1, "available", lambda: False)
    res = image_detector.detect(np.zeros((50, 50, 3), np.uint8))
    assert "track1" in res["errors"]
    assert res["summary"]["verdict"] == "CLEAR"


def test_conf_is_clamped(monkeypatch):
    monkeypatch.setattr(image_detector, "_run", lambda k, *a: [])
    assert image_detector.detect(np.zeros((8, 8, 3), np.uint8), conf=5)["conf"] == 0.95
    assert image_detector.detect(np.zeros((8, 8, 3), np.uint8), conf=-1)["conf"] == 0.05


# --------------------------------------------------------------------------
# API
# --------------------------------------------------------------------------
def test_detect_endpoints_require_auth(client):
    assert client.get("/api/detect/info").status_code == 401
    r = client.post("/api/detect/image", files={"file": ("a.png", _png(), "image/png")})
    assert r.status_code == 401


def test_detect_info_reports_device_and_models(client, auth):
    r = client.get("/api/detect/info", headers=auth).json()
    assert r["device"] in ("cpu", "mps", "cuda")
    assert r["coco"]["classes"] == 80
    assert r["track1"]["classes"] == track1.CLASSES


def test_detect_image_rejects_wrong_type(client, auth):
    r = client.post("/api/detect/image", headers=auth,
                    files={"file": ("clip.mp4", b"xx", "video/mp4")})
    assert r.status_code == 400 and "Unsupported" in r.json()["detail"]


def test_detect_image_rejects_undecodable(client, auth):
    r = client.post("/api/detect/image", headers=auth,
                    files={"file": ("x.jpg", b"not really a jpeg", "image/jpeg")})
    assert r.status_code == 400 and "decode" in r.json()["detail"]


def test_detect_image_requires_a_detector(client, auth):
    r = client.post("/api/detect/image", headers=auth,
                    data={"track1": "false", "coco": "false"},
                    files={"file": ("a.png", _png(), "image/png")})
    assert r.status_code == 400


def test_detect_image_contract(client, auth, monkeypatch):
    seen = {}

    def fake_run(kind, img, conf, imgsz):
        seen.update(kind=kind, conf=conf, imgsz=imgsz, shape=img.shape)
        return [{"label": "person", "conf": 0.7, "track": 2, "critical": False,
                 "threat_weight": 0.0, "context": True,
                 "bbox": [10, 10, 50, 90], "box": [.03, .04, .16, .38]}]
    monkeypatch.setattr(image_detector, "_run", fake_run)
    r = client.post("/api/detect/image", headers=auth,
                    data={"conf": "0.2", "track1": "false", "imgsz": "1000"},
                    files={"file": ("cam.png", _png(), "image/png")})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["filename"] == "cam.png"
    assert body["image"] == {"width": 320, "height": 240}
    assert body["summary"]["people"] == 1
    assert seen["kind"] == "coco" and seen["conf"] == 0.2
    assert seen["imgsz"] == 960, "imgsz snaps to a supported size"


@pytest.mark.skipif(not os.path.exists(track2.weights_path()),
                    reason="COCO weights not vendored")
def test_real_models_find_people_in_reference_image(client, auth):
    """End to end on the real weights and the real device: ultralytics'
    bundled bus.jpg has a bus and several people in it."""
    from ultralytics.utils import ASSETS
    data = (ASSETS / "bus.jpg").read_bytes()
    r = client.post("/api/detect/image", headers=auth,
                    files={"file": ("bus.jpg", data, "image/jpeg")})
    assert r.status_code == 200, r.text
    body = r.json()
    labels = {d["label"] for d in body["detections"]}
    assert "person" in labels and "bus" in labels
    assert body["summary"]["people"] >= 3
    for d in body["detections"]:
        assert all(0.0 <= v <= 1.0 for v in d["box"])
    assert body["models"]["device"] == device.select()
