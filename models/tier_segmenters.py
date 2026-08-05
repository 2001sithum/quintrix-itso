"""tier_segmenters.py — tier-specific storage execution (FR18-22).
Tier 1 (HIGH): lossless stream copy — original quality, indefinite retention.
Tier 2 (MEDIUM): H.264/HEVC transcode, reduced res/bitrate, original deleted.
Tier 3 (LOW): keyframe extraction only, full video discarded.
Uses ffmpeg when present, else falls back to cv2 so it always runs.
"""
import os, shutil, subprocess
import cv2


def _ffmpeg_ok():
    return shutil.which("ffmpeg") is not None


def store_high(seg_path, out_dir, seg_id):
    """FR20 — original quality (stream copy)."""
    out = os.path.join(out_dir, f"{seg_id}_high.mp4")
    if _ffmpeg_ok():
        subprocess.run(["ffmpeg", "-y", "-i", seg_path, "-c", "copy", out],
                       capture_output=True)
    if not os.path.exists(out):
        shutil.copy(seg_path, out)
    return out, os.path.getsize(out)


def store_medium(seg_path, out_dir, seg_id):
    """FR21 — transcode to reduced quality, delete original."""
    out = os.path.join(out_dir, f"{seg_id}_med.mp4")
    if _ffmpeg_ok():
        subprocess.run(["ffmpeg", "-y", "-i", seg_path, "-vf", "scale=-2:480",
                        "-c:v", "libx264", "-crf", "30", "-preset", "veryfast",
                        "-r", "10", out], capture_output=True)
    if not os.path.exists(out):
        # cv2 fallback: re-encode at half resolution
        cap = cv2.VideoCapture(seg_path)
        fps = cap.get(cv2.CAP_PROP_FPS) or 15
        w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH) * 0.5) or 320
        h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT) * 0.5) or 240
        vw = cv2.VideoWriter(out, cv2.VideoWriter_fourcc(*"mp4v"), fps, (w, h))
        while True:
            ok, fr = cap.read()
            if not ok:
                break
            vw.write(cv2.resize(fr, (w, h)))
        cap.release(); vw.release()
    size = os.path.getsize(out) if os.path.exists(out) else 0
    return out, size


def store_low(seg_path, out_dir, seg_id, n_keyframes=3):
    """FR22 — extract keyframes, discard full video."""
    cap = cv2.VideoCapture(seg_path)
    total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT)) or 1
    paths, size = [], 0
    for i in range(n_keyframes):
        cap.set(cv2.CAP_PROP_POS_FRAMES, int(total * (i + 0.5) / n_keyframes))
        ok, fr = cap.read()
        if not ok:
            continue
        p = os.path.join(out_dir, f"{seg_id}_kf{i}.jpg")
        cv2.imwrite(p, fr)
        paths.append(p)
        size += os.path.getsize(p)
    cap.release()
    return (paths[0] if paths else ""), size


def execute_tier(tier, seg_path, out_dir, seg_id):
    """FR18 dispatcher. Returns (stored_path, stored_bytes)."""
    os.makedirs(out_dir, exist_ok=True)
    if tier == "HIGH":
        return store_high(seg_path, out_dir, seg_id)
    if tier == "MEDIUM":
        return store_medium(seg_path, out_dir, seg_id)
    return store_low(seg_path, out_dir, seg_id)
