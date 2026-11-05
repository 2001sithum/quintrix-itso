"""fetch_weights.py — vendor the public pretrained and fine-tuned weights into weights/.

    python -m models.fetch_weights        # public Track 2 (YOLO11m)
    python -m models.fetch_weights --all  # all weights including UCF-Crime R3D-18 and threat detector
"""
import argparse
import os
import ssl
import sys
import urllib.request

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WEIGHTS_DIR = os.path.join(_ROOT, "weights")

RELEASE_BASE = "https://github.com/2001sithum/quintrix-itso/releases/download/v1.0.0"

# name -> (min_size_bytes, download_url)
CHECKPOINTS = {
    "yolo11m.pt": (30_000_000, None),  # ultralytics asset
    "r3d18_ucfcrime.pt": (100_000_000, f"{RELEASE_BASE}/r3d18_ucfcrime.pt"),
    "yolov8s_suspicious.pt": (20_000_000, f"{RELEASE_BASE}/yolov8s_suspicious.pt"),
    "sentiment_mlp.pt": (10_000, f"{RELEASE_BASE}/sentiment_mlp.pt"),
    "yolov8s.pt": (20_000_000, f"{RELEASE_BASE}/yolov8s.pt"),
}

PUBLIC = {"yolo11m.pt": 30_000_000}


def _download_url(url, dest, min_size):
    ctx = ssl.create_default_context()
    try:
        import certifi
        ctx.load_verify_locations(certifi.where())
    except ImportError:
        pass

    req = urllib.request.Request(url, headers={"User-Agent": "quintrix-itso"})
    with urllib.request.urlopen(req, context=ctx) as resp, open(dest, "wb") as f:
        while chunk := resp.read(1024 * 1024):
            f.write(chunk)
    size = os.path.getsize(dest) if os.path.exists(dest) else 0
    if size < min_size:
        raise RuntimeError(f"{os.path.basename(dest)}: download incomplete ({size} bytes)")


def fetch(name):
    dest = os.path.join(WEIGHTS_DIR, name)
    min_size, url = CHECKPOINTS.get(name, (0, None))
    if os.path.exists(dest) and os.path.getsize(dest) >= min_size:
        print(f"ok       {dest} ({os.path.getsize(dest) / 1e6:.1f} MB, already present)")
        return dest

    os.makedirs(WEIGHTS_DIR, exist_ok=True)
    if url:
        print(f"downloading {name} from GitHub release...")
        _download_url(url, dest, min_size)
        path = dest
    else:
        from ultralytics.utils.downloads import attempt_download_asset
        path = attempt_download_asset(dest)

    size = os.path.getsize(path) if os.path.exists(path) else 0
    if size < min_size:
        raise RuntimeError(f"{name}: download incomplete ({size} bytes)")
    print(f"fetched  {path} ({size / 1e6:.1f} MB)")
    return path


def main():
    parser = argparse.ArgumentParser(description="Fetch model weights")
    parser.add_argument("--all", action="store_true", help="Fetch all models (including fine-tuned checkpoints from GitHub release)")
    args = parser.parse_args()

    targets = list(CHECKPOINTS.keys()) if args.all else list(PUBLIC.keys())
    failed = 0
    for name in targets:
        try:
            fetch(name)
        except Exception as e:
            print(f"FAILED   {name}: {e}", file=sys.stderr)
            failed += 1
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
