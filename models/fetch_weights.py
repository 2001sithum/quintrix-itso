"""fetch_weights.py — vendor the public pretrained weights into weights/.

    python -m models.fetch_weights

Only covers checkpoints that are publicly downloadable. The fine-tuned ones
(R3D-18 UCF-Crime, the Track 1 threat detector, the sentiment MLP) are this
project's own trainings and ship out of band — see scripts/upload-weights.sh.

The Docker images run this at build time. Without it a CI checkout (where
*.pt is gitignored) would resolve Track 2 to the older YOLOv8s copy pulled from
the weights bucket and quietly lose the YOLO11m accuracy gain.
"""
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WEIGHTS_DIR = os.path.join(_ROOT, "weights")

# name -> minimum plausible size in bytes (guards against a saved HTML error page)
PUBLIC = {"yolo11m.pt": 30_000_000}


def fetch(name):
    dest = os.path.join(WEIGHTS_DIR, name)
    if os.path.exists(dest) and os.path.getsize(dest) >= PUBLIC[name]:
        print(f"ok       {dest} ({os.path.getsize(dest) / 1e6:.1f} MB, already present)")
        return dest
    from ultralytics.utils.downloads import attempt_download_asset
    os.makedirs(WEIGHTS_DIR, exist_ok=True)
    path = attempt_download_asset(dest)
    size = os.path.getsize(path) if os.path.exists(path) else 0
    if size < PUBLIC[name]:
        raise RuntimeError(f"{name}: download incomplete ({size} bytes)")
    print(f"fetched  {path} ({size / 1e6:.1f} MB)")
    return path


def main():
    # python.org macOS builds ship without a CA bundle (same fix as server.py)
    try:
        import certifi
        os.environ.setdefault("SSL_CERT_FILE", certifi.where())
    except ImportError:
        pass
    failed = 0
    for name in PUBLIC:
        try:
            fetch(name)
        except Exception as e:
            print(f"FAILED   {name}: {e}", file=sys.stderr)
            failed += 1
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
