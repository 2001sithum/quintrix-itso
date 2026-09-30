"""device.py — one place that decides where every model runs (FR60).

Order: `QUINTRIX_DEVICE` override → CUDA → Apple-silicon MPS → CPU.

Before this module each model file hard-coded its own device, so the YOLO
tracks ran on CPU even on a Mac with a perfectly good GPU, and the action model
picked MPS on its own. Centralising the choice keeps every stage on the same
device and lets one env var force CPU when a backend misbehaves.

`PYTORCH_ENABLE_MPS_FALLBACK=1` is set before torch is imported anywhere: a few
ops (some 3D-conv and NMS paths) have no MPS kernel yet, and without the
fallback they raise instead of quietly running that one op on CPU.
"""
import os
import threading

os.environ.setdefault("PYTORCH_ENABLE_MPS_FALLBACK", "1")

_VALID = ("cpu", "mps", "cuda")
_cached = None
_lock = threading.Lock()


def _probe():
    env = (os.environ.get("QUINTRIX_DEVICE") or "").strip().lower()
    if env:
        # accept "cuda:1" etc. but reject typos rather than crash every model
        if env.split(":")[0] in _VALID:
            return env
        print(f"QUINTRIX_DEVICE={env!r} is not one of {_VALID}; auto-selecting")
    try:
        import torch
        if torch.cuda.is_available():
            return "cuda"
        mps = getattr(torch.backends, "mps", None)
        if mps is not None and mps.is_available() and mps.is_built():
            # Virtual machines (hosted CI runners among them) can report MPS
            # as available yet fail every allocation. Prove a tiny op runs
            # before committing every model to it; otherwise stay on CPU.
            try:
                (torch.ones(8, device="mps") * 2).sum().item()
                return "mps"
            except Exception as e:
                print(f"MPS reported available but unusable ({e}); using CPU")
    except Exception:
        pass
    return "cpu"


def select():
    """The device string every model should use. Probed once, then cached."""
    global _cached
    with _lock:
        if _cached is None:
            _cached = _probe()
        return _cached


def reset():
    """Forget the cached choice — for tests that flip `QUINTRIX_DEVICE`."""
    global _cached
    with _lock:
        _cached = None


def describe():
    """Human-readable accelerator name for the UI badge."""
    d = select()
    if d.startswith("cuda"):
        try:
            import torch
            return f"CUDA · {torch.cuda.get_device_name(0)}"
        except Exception:
            return "CUDA"
    if d == "mps":
        return "Apple GPU (Metal / MPS)"
    return "CPU"
