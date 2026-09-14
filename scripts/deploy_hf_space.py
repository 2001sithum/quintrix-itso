#!/usr/bin/env python3
"""Assemble and push the Hugging Face Space.

The Space is a separate repo that needs its own README (HF requires YAML
frontmatter at the root) and its own Dockerfile (Spaces run as uid 1000, and
free-tier storage is ephemeral). Rather than pushing either into the GitHub
repo, this stages a clean tree from `deploy/huggingface/` plus the application
source and uploads that.

Uploading via `huggingface_hub` rather than `git push` is deliberate: the API
handles large files (the weights are ~170 MB) with server-side LFS, so no local
git-lfs install is required.

Model weights are gitignored on GitHub but **must** ship here. Without them the
Space starts, serves, passes its health check and detects nothing — so this
refuses to deploy an incomplete set.

    export HF_TOKEN=hf_xxx          # needs "write" scope
    python scripts/deploy_hf_space.py --user YOURNAME [--space quintrix-itso-v13]
"""
import argparse
import os
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# Everything the container needs, and nothing it does not.
APP_ITEMS = ["server.py", "itso_system.py", "requirements.txt",
             "engine", "models", "static", "training", "weights"]

REQUIRED_WEIGHTS = ["r3d18_ucfcrime.pt", "yolov8s_suspicious.pt", "sentiment_mlp.pt"]

# Never upload: local state, caches, and anything personal.
EXCLUDE = {"__pycache__", ".pytest_cache", ".DS_Store"}


def stage(dest: Path) -> None:
    for item in APP_ITEMS:
        src = ROOT / item
        if not src.exists():
            sys.exit(f"ERROR: missing {item}")
        if src.is_dir():
            shutil.copytree(src, dest / item,
                            ignore=shutil.ignore_patterns(*EXCLUDE, "*.pyc"))
        else:
            shutil.copy2(src, dest / item)

    # Space-specific root files override the GitHub equivalents.
    hf = ROOT / "deploy" / "huggingface"
    for name in ("README.md", "Dockerfile", ".gitattributes"):
        shutil.copy2(hf / name, dest / name)

    # Metrics/calibration JSON belong in the GitHub repo, not the runtime image.
    for junk in (dest / "weights").glob("*.json"):
        junk.unlink()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--user", required=True, help="your Hugging Face username")
    ap.add_argument("--space", default="quintrix-itso-v13")
    ap.add_argument("--private", action="store_true")
    ap.add_argument("--dry-run", action="store_true",
                    help="stage and report without uploading")
    args = ap.parse_args()

    token = os.environ.get("HF_TOKEN")
    if not token and not args.dry_run:
        return fail("export HF_TOKEN=hf_xxx (a token with 'write' scope)")

    for w in REQUIRED_WEIGHTS:
        p = ROOT / "weights" / w
        if not p.exists() or p.stat().st_size == 0:
            return fail(f"missing weights/{w} — the Space would detect nothing")

    repo_id = f"{args.user}/{args.space}"
    with tempfile.TemporaryDirectory(prefix="hf-space-") as tmp:
        dest = Path(tmp) / "space"
        dest.mkdir()
        stage(dest)

        files = [p for p in dest.rglob("*") if p.is_file()]
        total = sum(p.stat().st_size for p in files)
        print(f"staged {len(files)} files, {total / 1e6:.1f} MB")
        for p in sorted(files, key=lambda x: -x.stat().st_size)[:5]:
            print(f"  {p.stat().st_size / 1e6:7.1f} MB  {p.relative_to(dest)}")

        if args.dry_run:
            print("\ndry run — nothing uploaded")
            return 0

        from huggingface_hub import HfApi
        api = HfApi(token=token)
        try:
            whoami = api.whoami()
            print(f"authenticated as {whoami.get('name')}")
        except Exception as e:
            return fail(f"token rejected: {e}")

        api.create_repo(repo_id=repo_id, repo_type="space", space_sdk="docker",
                        private=args.private, exist_ok=True)
        print(f"space ready: {repo_id}")

        api.upload_folder(
            folder_path=str(dest), repo_id=repo_id, repo_type="space",
            commit_message="Deploy Quintrix ITSO v13",
            # Remove anything upstream that is no longer staged, so a rename
            # does not leave the old file serving alongside the new one.
            delete_patterns=["*"],
        )

    url = f"https://huggingface.co/spaces/{repo_id}"
    print(f"\nDeployed: {url}")
    print("\nNEXT — set the admin password, or any visitor can administer it:")
    print(f"  {url}/settings  →  Variables and secrets  →  New secret")
    print("    ITSO_ADMIN_PASSWORD = <something long>")
    print("\nThe first build takes ~10 minutes (torch + 170MB of weights).")
    return 0


def fail(msg: str) -> int:
    print(f"ERROR: {msg}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
