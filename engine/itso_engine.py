"""
itso_engine.py — the ITSO video processing pipeline.

Two profiles run through this module, selected by the `pipeline_profile`
config key:

**fusion** (default, deployed) — the CCTV segment+inference pipeline:

    1  Ingestion      video ──► N × 15s segments
    2  Motion filter  N segments ──► M with real motion        (Invaligator/MOG2)
    3  Frame sampling F frames ──► 16 action + ~1fps detector frames
    4  Inference      R3D-18 + Track 1 + Track 2 ──► fusion ──► Ssig + tier
    5  Tiered storage segment bytes ──► stored bytes           (+ Tier-3 grace queue)

**legacy** — the original X3D-S + COCO YOLOv8 + MobileNetV3 path, kept runnable
so the two can be compared on identical footage. See docs/MODELS.md.

Every stage records what went in and what came out into `stage_stats`
(`storage_manager.record_stage`). That telemetry is measured during the real
run, which is what lets the Workflow Simulator show a reduction funnel for an
actual video rather than an illustration.

Covers FR08-23, FR33, FR40, FR41, FR55, FR59.
"""
import os, uuid, json, time, shutil, subprocess, threading, datetime as dt
import cv2
import numpy as np

from engine import storage_manager as sm
from engine import fusion
from models import object_detector, action_recognizer, sentiment_analyzer, tier_segmenters
from models import action_recognizer_r3d18 as r3d
from models import suspicious_detector as track1
from models import context_detector as track2
from models import sentiment_model

ARCHIVE = sm.ARCHIVE_DIR

# How many frames to retain in memory per segment. A 15s segment at 30fps is
# ~450 frames; keeping all of them for a long video exhausts RAM, and keeping
# only the first 32 (as an earlier version did) means the detector sampling
# never sees past the first second of each segment. Decimating to ~48 frames
# spread across the whole segment gives both models full temporal coverage at
# a bounded memory cost.
FRAME_BUDGET = 48

# Night hours used for the `is_night` context feature.
NIGHT_START, NIGHT_END = 21, 5


# ---------------------------------------------------------------------------
# Stage 1 — Ingestion: technical metadata (FR28) + 15s segmentation (FR09)
# ---------------------------------------------------------------------------
def extract_tech_metadata(path):
    cap = cv2.VideoCapture(path)
    fps = cap.get(cv2.CAP_PROP_FPS) or 25.0
    total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    cap.release()
    return {"fps": fps, "frames": total, "duration": total / fps if fps else 0,
            "width": w, "height": h, "codec": "h264"}


def _sample_frames(seg_path):
    """Decode one segment file into a decimated frame list spanning the whole
    segment, plus the true frame count it was decimated from."""
    cap = cv2.VideoCapture(seg_path)
    total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT)) or 0
    keep_every = max(1, total // FRAME_BUDGET) if total else 1
    buf, n = [], 0
    while True:
        ok, frame = cap.read()
        if not ok:
            break
        if n % keep_every == 0 and len(buf) < FRAME_BUDGET:
            buf.append(frame)
        n += 1
    cap.release()
    return buf, (total or n)


def _segment_with_ffmpeg(path, seg_seconds, seg_dir):
    """Split with ffmpeg's segment muxer using **stream copy**.

    This is the important one. The obvious implementation — decode every frame
    and re-encode it through `cv2.VideoWriter` — writes MPEG-4 Part 2, which is
    far less efficient than the H.264 it is fed. Measured on a 17 MB H.264
    clip, that path produced 63 MB of segments: segmentation alone inflated
    storage 3.7x *before* any tiering, and every downstream savings figure was
    then computed against that inflated baseline instead of the real file.

    Stream copy avoids re-encoding entirely, so it is lossless, roughly
    instant, and byte-comparable to the source — which also means the HIGH tier
    genuinely keeps original quality rather than a generational re-encode.

    The trade-off is honest and worth stating: `-c copy` can only cut on
    keyframes, so segment boundaries land near `seg_seconds` rather than
    exactly on it. Real durations are measured per segment afterwards, so the
    timeline reflects what was actually written.

    Returns None if ffmpeg is unavailable or produced nothing, so the caller
    falls back to the cv2 writer.
    """
    if not shutil.which("ffmpeg"):
        return None
    stamp = uuid.uuid4().hex
    pattern = os.path.join(seg_dir, f"{stamp}_%04d.mp4")
    proc = subprocess.run(
        ["ffmpeg", "-y", "-i", path, "-c", "copy", "-map", "0:v:0",
         "-f", "segment", "-segment_time", str(seg_seconds),
         "-reset_timestamps", "1", "-segment_format_options",
         "movflags=+faststart", pattern],
        capture_output=True)
    produced = sorted(f for f in os.listdir(seg_dir) if f.startswith(stamp))
    if proc.returncode != 0 or not produced:
        for f in produced:
            try: os.remove(os.path.join(seg_dir, f))
            except OSError: pass
        return None
    return [os.path.join(seg_dir, f) for f in produced]


def _segment_with_cv2(path, seg_seconds, seg_dir):
    """Fallback splitter for hosts without ffmpeg. Re-encodes, so it inflates
    storage — `process_footage` records that inflation rather than hiding it."""
    cap = cv2.VideoCapture(path)
    fps = cap.get(cv2.CAP_PROP_FPS) or 25.0
    seg_frames = max(1, int(seg_seconds * fps))
    w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    fourcc = cv2.VideoWriter_fourcc(*"mp4v")
    paths, frame_id = [], 0
    writer, seg_path = None, None
    while True:
        ok, frame = cap.read()
        if not ok:
            break
        if frame_id % seg_frames == 0:
            if writer:
                writer.release()
            seg_path = os.path.join(seg_dir, f"{uuid.uuid4().hex}.mp4")
            writer = cv2.VideoWriter(seg_path, fourcc, fps, (w, h))
            paths.append(seg_path)
        writer.write(frame)
        frame_id += 1
    if writer:
        writer.release()
    cap.release()
    return paths


def segment_video(path, seg_seconds):
    """Cut the video into ~`seg_seconds` segments.

    Returns (fps, [(idx, seg_path, frames, n_frames_in_segment)], lossless)
    where `frames` is the decimated in-memory sample spanning the whole segment,
    `n_frames_in_segment` is how many frames it was decimated *from* (the input
    side of the frame-sampling reduction), and `lossless` says whether the split
    was a stream copy or a re-encode.
    """
    cap = cv2.VideoCapture(path)
    fps = cap.get(cv2.CAP_PROP_FPS) or 25.0
    cap.release()
    seg_dir = os.path.join(ARCHIVE, "segments")
    os.makedirs(seg_dir, exist_ok=True)

    paths = _segment_with_ffmpeg(path, seg_seconds, seg_dir)
    lossless = paths is not None
    if not lossless:
        paths = _segment_with_cv2(path, seg_seconds, seg_dir)

    segments = []
    for idx, p in enumerate(paths):
        frames, n = _sample_frames(p)
        if not frames:            # a zero-length trailing segment from the muxer
            try: os.remove(p)
            except OSError: pass
            continue
        segments.append((len(segments), p, frames, n))
    return fps, segments, lossless


# ---------------------------------------------------------------------------
# Stage 2 — Invaligator motion filter (FR10). MOG2 + motion-ratio gate.
# ---------------------------------------------------------------------------
# Frames fed to MOG2 purely to build its background model, before any
# measurement is taken. Without this the filter is broken — see below.
MOTION_WARMUP = 3
# How many measured frames must exceed the sensitivity for a segment to count
# as moving. Real activity persists across sampled frames; a codec keyframe
# refresh or a single-frame glitch does not.
MOTION_MIN_HITS = 2


def invaligator(frames, sensitivity):
    """Stage 2 motion gate. Returns (moved, peak_motion_ratio).

    Two corrections over the naive version, both found by running a genuinely
    static clip through the pipeline and watching it pass:

    1. **Warm-up.** MOG2 has no background model on its first frame, so it
       classifies *every pixel* as foreground and returns a motion ratio of
       1.0. Taking `max()` across all frames therefore reported 1.0 for a
       completely still video, and the filter passed everything — the stage
       whose whole job is to skip static footage was a no-op, and the compute
       saving it is credited with was never actually realised.

    2. **Sustain instead of peak.** A lossy codec's keyframe refresh produces a
       one-frame foreground spike on otherwise still footage (measured at ~0.10
       on a static test clip, a hundred times the default sensitivity).
       Requiring the threshold to be crossed in at least `MOTION_MIN_HITS`
       measured frames rejects those without lowering sensitivity to real
       motion.

    The reported ratio is the peak over *measured* frames only, so the value
    stored on the segment and fed to the fusion features is meaningful too.
    """
    if len(frames) < 2:
        return False, 0.0
    mog = cv2.createBackgroundSubtractorMOG2(detectShadows=False)

    warmup = min(MOTION_WARMUP, max(1, len(frames) // 4))
    for f in frames[:warmup]:
        mog.apply(f)

    measured = frames[warmup:]
    if not measured:                      # very short buffer: measure them all
        measured = frames

    peak, hits = 0.0, 0
    for f in measured:
        ratio = float((mog.apply(f) > 200).mean())
        peak = max(peak, ratio)
        if ratio >= sensitivity:
            hits += 1

    required = MOTION_MIN_HITS if len(measured) >= MOTION_MIN_HITS * 2 else 1
    return hits >= required, round(peak, 5)


# ---------------------------------------------------------------------------
# Stage 3 — thumbnails (FR33) + downscale (FR55) handled inside detectors
# ---------------------------------------------------------------------------
def make_thumb(frames, seg_id):
    if not frames:
        return ""
    p = os.path.join(ARCHIVE, "thumbs", f"{seg_id}.jpg")
    cv2.imwrite(p, cv2.resize(frames[len(frames) // 2], (320, 180)))
    return p


# ---------------------------------------------------------------------------
# Legacy Stage 4 — original Ssig prioritization (FR14/FR15) + hazard (FR23)
# ---------------------------------------------------------------------------
def assess_hazard(objects, threat_level):
    labels = {o["label"] for o in objects}
    if labels & sentiment_analyzer._WEAPONS:
        return "critical"
    return {"high": "elevated", "medium": "moderate"}.get(threat_level, "none")


def compute_ssig(objects, actions, sentiment_score, hazard, ctx_rules, is_night=False):
    obj_c = min(1.0, len(objects) / 6.0)
    act_c = actions[0]["confidence"] if actions else 0.0
    base = 0.35 * obj_c + 0.30 * act_c + 0.35 * sentiment_score
    if any(o.get("critical") for o in objects):
        base += ctx_rules.get("weapon_boost", 0.25) if \
            any(o["label"] in sentiment_analyzer._WEAPONS for o in objects) else 0.1
    if sum(1 for o in objects if o["label"] == "person") >= 3:
        base += ctx_rules.get("crowd_boost", 0.15)
    if is_night:
        base += ctx_rules.get("night_boost", 0.1)
    if hazard == "critical":
        base += 0.25
    return min(1.0, round(base, 3))


def assign_tier(ssig, th_high, th_low):
    if ssig > th_high:
        return "HIGH"
    if ssig > th_low:
        return "MEDIUM"
    return "LOW"


# ---------------------------------------------------------------------------
# Action-recognition backend dispatch for the LEGACY profile
# ---------------------------------------------------------------------------
def recognize_actions(frames, min_frames, backend="x3d"):
    """Legacy-profile action backend selection. The fusion profile does not use
    this — it always calls R3D-18 directly, because its scoring depends on
    UCF-Crime class severity that Kinetics-400 labels cannot express."""
    if backend == "r3d18" and r3d.available():
        return r3d.recognize(frames, min_frames)
    return action_recognizer.recognize(frames, min_frames)


# ---------------------------------------------------------------------------
# Config loading
# ---------------------------------------------------------------------------
def _load_settings():
    cfg = sm.get_config()

    def f(key, default):
        try:
            return float(cfg.get(key, default))
        except (TypeError, ValueError):
            return float(default)

    try:
        ctx = json.loads(cfg.get("context_rules") or "{}")
    except Exception:
        ctx = {}
    return {
        "th_high": f("threshold_high", 0.7), "th_low": f("threshold_low", 0.4),
        "alert_th": f("alert_threshold", 0.8), "sens": f("motion_sensitivity", 0.001),
        "conf": f("object_conf_threshold", 0.35),
        "t1_conf": f("track1_conf_threshold", 0.35),
        "t2_conf": f("track2_conf_threshold", 0.35),
        "stride_s": f("detection_stride_seconds", 1.0),
        "min_f": int(f("action_min_frames", 16)),
        "seg_s": int(f("segment_seconds", 15)),
        "grace_h": f("tier3_grace_hours", 24),
        "ctx": ctx,
        "profile": cfg.get("pipeline_profile", "fusion"),
        "action_backend": cfg.get("action_model_backend", "x3d"),
    }


def _is_night(ts_iso):
    try:
        h = dt.datetime.fromisoformat(ts_iso).hour
    except Exception:
        return False
    return h >= NIGHT_START or h < NIGHT_END


# ---------------------------------------------------------------------------
# Stage 4 (fusion profile) — run all three models on one segment
# ---------------------------------------------------------------------------
def analyze_segment_fusion(frames, motion_ratio, is_night, cfg, fps):
    """Run R3D-18 + Track 1 + Track 2 on one segment's frames and fuse them.

    Returns (result, timings, sampling) — `result` is the fusion output,
    `timings` is per-model wall time in ms, `sampling` records how many frames
    each model actually consumed (the compute-reduction numbers).
    """
    # Detector stride: the buffered frames already span the whole segment, so
    # convert the configured seconds-per-sample into a stride over the buffer
    # rather than over raw video frames.
    seg_seconds = cfg["seg_s"] or 15
    buf_fps = len(frames) / seg_seconds if seg_seconds else len(frames)
    stride = max(1, int(round(buf_fps * cfg["stride_s"])))
    n_det = track1.frames_sampled(len(frames), stride)

    timings = {}
    t0 = time.time()
    action = r3d.predict(frames, cfg["min_f"])
    timings["action_r3d18_ms"] = round((time.time() - t0) * 1000, 1)

    t0 = time.time()
    t1_full = track1.detect_detailed(frames, cfg["t1_conf"], stride, seg_seconds)
    t1_dets = t1_full["aggregate"]
    timings["track1_ms"] = round((time.time() - t0) * 1000, 1)

    t0 = time.time()
    t2_full = track2.detect_detailed(frames, cfg["t2_conf"], stride, seg_seconds)
    t2_dets = t2_full["aggregate"]
    timings["track2_ms"] = round((time.time() - t0) * 1000, 1)

    # Merge both tracks into one time-ordered overlay track. Playback needs a
    # single list keyed by offset-in-segment; which model found a box is kept as
    # a per-box field rather than as separate streams.
    merged = {}
    for entry in t1_full["timeline"] + t2_full["timeline"]:
        merged.setdefault(entry["t"], []).extend(entry["boxes"])
    overlay = [{"t": t, "boxes": b} for t, b in sorted(merged.items())]

    feats = fusion.build_features(action, t1_dets, t2_dets, motion_ratio,
                                  is_night, max(1, n_det))
    t0 = time.time()
    score, backend = sentiment_model.predict(feats)
    timings["sentiment_ms"] = round((time.time() - t0) * 1000, 1)

    result = fusion.fuse(action, t1_dets, t2_dets, score, motion_ratio, is_night,
                         max(1, n_det), cfg["ctx"], cfg["th_high"], cfg["th_low"])
    result["action"] = action
    result["track1"] = t1_dets
    result["track2"] = t2_dets
    result["sentiment_backend"] = backend
    result["overlay"] = overlay
    sampling = {"buffered_frames": len(frames), "action_frames": min(16, len(frames)),
                "detector_frames": n_det, "detector_stride": stride,
                "overlay_frames": len(overlay)}
    return result, timings, sampling


# ---------------------------------------------------------------------------
# Tier-3 safeguard — park instead of destroy
# ---------------------------------------------------------------------------
def _dispose_segment(seg_id, footage_id, tier, ssig, seg_path, grace_hours, reason):
    """Handle the original segment file after tiering.

    HIGH keeps it. MEDIUM and LOW have already written a reduced artefact, so
    the original is redundant — but for LOW the reduced artefact is a *keyframe
    JPEG*, which means deleting the original destroys the only moving footage
    of that moment. If the score was wrong, that is unrecoverable.

    With a grace period configured, LOW originals are parked in `purge_queue`
    instead, giving an operator a window to restore them. See
    `run_purge_sweep()` for the deletion side.
    """
    if not seg_path or not os.path.exists(seg_path):
        return None
    if tier == "LOW" and grace_hours > 0:
        held = os.path.join(ARCHIVE, "pending_purge", f"{seg_id}.mp4")
        os.makedirs(os.path.dirname(held), exist_ok=True)
        try:
            os.replace(seg_path, held)
        except OSError:
            return None
        return sm.queue_for_purge(seg_id, footage_id, tier, ssig, held,
                                  os.path.getsize(held), grace_hours, reason)
    try:
        os.remove(seg_path)
    except OSError:
        pass
    return None


def run_purge_sweep(limit=500):
    """Delete parked segments whose grace period has expired. Idempotent and
    safe to call repeatedly; returns (n_purged, bytes_reclaimed)."""
    rows = sm.due_purges(limit)
    n, freed = 0, 0
    for r in rows:
        p = r["path"]
        if p and os.path.exists(p):
            try:
                freed += os.path.getsize(p)
                os.remove(p)
            except OSError:
                continue
        sm.execute("UPDATE purge_queue SET status='purged', reviewed_at=? WHERE id=?",
                   (sm.now(), r["id"]))
        sm.execute("UPDATE segments SET purge_state='purged' WHERE id=?",
                   (r["segment_id"],))
        n += 1
    if n:
        sm.log("audit", f"Purge sweep: {n} segment(s) deleted after grace period, "
                        f"{freed / 1e6:.1f} MB reclaimed",
               context={"purged": n, "bytes": freed})
    return n, freed


def start_purge_worker(interval_seconds=300):
    """Background sweeper. One daemon thread; started once from server.py."""
    def loop():
        while True:
            try:
                run_purge_sweep()
            except Exception as e:                          # pragma: no cover
                print("Purge sweep failed:", e)
            time.sleep(interval_seconds)
    t = threading.Thread(target=loop, daemon=True, name="itso-purge")
    t.start()
    return t


# ---------------------------------------------------------------------------
# Reprocessing — re-run an existing project through the current pipeline
# ---------------------------------------------------------------------------
def reset_footage(footage_id):
    """Delete everything derived from a previous run, leaving the upload.

    Reprocessing exists because the pipeline itself changed: projects analysed
    before the lossless-segmentation and motion-filter fixes carry inflated
    segment sizes, no stage telemetry, no fusion trace and no detection
    overlay. Re-running them is the only way to get comparable numbers.

    Everything removed here is *derived* — tier artefacts, thumbnails, held
    purge files, segment rows (which cascade to events and alerts) and stage
    telemetry. The uploaded source file is never touched, so this is
    repeatable.

    Returns a summary of what was cleaned, so the caller can report it rather
    than silently dropping data.
    """
    segs = sm.q("SELECT id, stored_path, thumb FROM segments WHERE footage_id=?",
                (footage_id,))
    held = sm.q("SELECT path FROM purge_queue WHERE footage_id=?", (footage_id,))

    removed, freed = 0, 0
    for row in segs:
        for path in (row.get("stored_path"), row.get("thumb")):
            if path and os.path.exists(path):
                try:
                    freed += os.path.getsize(path)
                    os.remove(path)
                    removed += 1
                except OSError:
                    pass
        # LOW tiers can have sibling keyframes beyond the one recorded
        base = (row.get("stored_path") or "").rsplit("_kf", 1)[0]
        if base:
            for i in range(1, 6):
                kf = f"{base}_kf{i}.jpg"
                if os.path.exists(kf):
                    try:
                        freed += os.path.getsize(kf); os.remove(kf); removed += 1
                    except OSError:
                        pass
    for row in held:
        path = row.get("path")
        if path and os.path.exists(path):
            try:
                freed += os.path.getsize(path); os.remove(path); removed += 1
            except OSError:
                pass

    sm.execute("DELETE FROM purge_queue WHERE footage_id=?", (footage_id,))
    sm.execute("DELETE FROM stage_stats WHERE footage_id=?", (footage_id,))
    sm.execute("DELETE FROM events WHERE footage_id=?", (footage_id,))
    sm.execute("DELETE FROM alerts WHERE footage_id=?", (footage_id,))
    sm.execute("DELETE FROM segments WHERE footage_id=?", (footage_id,))
    sm.execute("UPDATE footages SET status='uploaded', stored_bytes=0, segment_bytes=0,"
               " processing_ms=0, frames_total=0, frames_analyzed=0 WHERE id=?",
               (footage_id,))
    return {"segments_removed": len(segs), "files_removed": removed,
            "bytes_freed": freed}


# ---------------------------------------------------------------------------
# Full per-project pipeline (async worker calls this) — FR29 background
# ---------------------------------------------------------------------------
def process_footage(footage_id):
    cfg = _load_settings()
    proj = sm.q("SELECT * FROM footages WHERE id=?", (footage_id,), one=True)
    if not proj:
        return
    sm.execute("UPDATE footages SET status='processing', pipeline_profile=? WHERE id=?",
               (cfg["profile"], footage_id))
    sm.log("processing", f"Pipeline started for {footage_id} "
                         f"[profile={cfg['profile']}]",
           context={"job": proj["job_id"], "footage": footage_id,
                    "profile": cfg["profile"]})
    run_start = time.time()

    try:
        # ---------------- Stage 1: ingestion + segmentation ----------------
        t0 = time.time()
        meta = extract_tech_metadata(proj["original_path"])              # FR28
        sm.execute("UPDATE footages SET fps=?,duration=?,width=?,height=?,codec=? "
                   "WHERE id=?",
                   (meta["fps"], meta["duration"], meta["width"], meta["height"],
                    meta["codec"], footage_id))
        sm.log("processing", f"Stage 1/5 Ingestion: {meta['duration']:.1f}s @ "
               f"{meta['fps']:.0f}fps, {meta['width']}x{meta['height']}",
               context={"footage": footage_id})
        fps, segs, lossless = segment_video(proj["original_path"], cfg["seg_s"])  # FR09
        seg_bytes = sum(os.path.getsize(p) for _, p, _, _ in segs if os.path.exists(p))
        frames_total = sum(n for _, _, _, n in segs)
        # Stage 1 splits one video into N segments — that is *decomposition*, so
        # reporting it on the segment count would show a nonsensical negative
        # "reduction". The meaningful quantity here is bytes: cutting re-encodes
        # the stream, and if the segment writer is less efficient than the source
        # codec this stage can legitimately *inflate* storage before any of the
        # later stages claw it back. Reporting that honestly matters.
        sm.record_stage(footage_id, 1, "Ingestion & Segmentation", "bytes",
                        items_in=1, items_out=len(segs),
                        bytes_in=proj["original_bytes"] or 0, bytes_out=seg_bytes,
                        frames_in=frames_total, frames_out=frames_total,
                        duration_ms=round((time.time() - t0) * 1000, 1),
                        detail={"kind": "decomposition", "segments": len(segs),
                                "segment_seconds": cfg["seg_s"], "fps": round(fps, 2),
                                "resolution": f"{meta['width']}x{meta['height']}",
                                "method": "ffmpeg stream copy (lossless)" if lossless
                                          else "cv2 re-encode (mp4v — inflates size)",
                                "lossless": lossless,
                                "note": "one video becomes N independently "
                                        "scorable units; a re-encoding split "
                                        "inflates bytes here"})
        sm.log("processing", f"Stage 1/5 Segmentation: cut into {len(segs)} "
               f"segments of {cfg['seg_s']}s", context={"footage": footage_id})

        base_ts = dt.datetime.fromisoformat(proj["created_at"])
        tier_dir = os.path.join(ARCHIVE, "tiers")
        totals = {"orig": 0, "stored": 0, "moving": 0, "frames_analyzed": 0,
                  "det_frames": 0, "motion_ms": 0.0, "infer_ms": 0.0,
                  "store_ms": 0.0, "uploaded": proj["original_bytes"] or 0}
        tier_counts = {"HIGH": 0, "MEDIUM": 0, "LOW": 0}
        parked = 0

        for idx, seg_path, frames, n_frames in segs:
            seg_id = uuid.uuid4().hex
            ts = (base_ts + dt.timedelta(seconds=idx * cfg["seg_s"])).isoformat()
            night = _is_night(ts)

            # ---------------- Stage 2: motion filter ----------------
            t0 = time.time()
            moved, ratio = invaligator(frames, cfg["sens"])              # FR10
            totals["motion_ms"] += (time.time() - t0) * 1000
            thumb = make_thumb(frames, seg_id)                           # FR33
            orig_bytes = os.path.getsize(seg_path) if os.path.exists(seg_path) else 0
            totals["orig"] += orig_bytes

            row = _blank_row(ts, ratio)
            if moved:
                totals["moving"] += 1
                t0 = time.time()
                row = (_analyze_fusion(frames, ratio, night, cfg, fps)
                       if cfg["profile"] == "fusion"
                       else _analyze_legacy(frames, ratio, night, cfg))
                totals["infer_ms"] += (time.time() - t0) * 1000
                totals["frames_analyzed"] += row["_sampling"].get("action_frames", 0)
                totals["det_frames"] += row["_sampling"].get("detector_frames", 0)
                row["ts"] = ts
                row["motion_ratio"] = ratio

                # ---------------- Stage 5: tiered storage ----------------
                t0 = time.time()
                stored_path, stored_bytes = tier_segmenters.execute_tier(  # FR18/20-22
                    row["tier"], seg_path, tier_dir, seg_id)
                totals["store_ms"] += (time.time() - t0) * 1000
                row["stored_path"], row["stored_bytes"] = stored_path, stored_bytes

            totals["stored"] += row["stored_bytes"]
            tier_counts[row["tier"]] += 1
            # The segment row must exist before anything references it —
            # purge_queue.segment_id carries an ON DELETE CASCADE foreign key,
            # so parking the file first fails the constraint.
            _insert_segment(seg_id, footage_id, idx, cfg, row, moved, thumb,
                            orig_bytes, n_frames)

            # Dispose of the original segment file. HIGH keeps it; everything
            # else either parks it in the review queue or deletes it.
            if row["tier"] != "HIGH":
                reason = (f"tiered {row['tier']} at Ssig {row['ssig']}" if moved
                          else "no motion detected")
                if _dispose_segment(seg_id, footage_id, row["tier"], row["ssig"],
                                    seg_path, cfg["grace_h"], reason):
                    parked += 1
                    row["purge_state"] = "pending"

            if moved:
                t1_summary = ", ".join(d["label"] for d in row["track1"][:3]) or "none"
                sm.log("processing",
                       f"Stage 2-5 Segment {idx + 1}/{len(segs)}: motion detected, "
                       f"action={row['action_label'] or 'n/a'}, suspicious=[{t1_summary}], "
                       f"ssig={row['ssig']:.2f}, tier={row['tier']}",
                       context={"footage": footage_id, "segment": seg_id})
            else:
                sm.log("processing",
                       f"Stage 2 Segment {idx + 1}/{len(segs)}: no motion — "
                       f"skipped, auto-tiered LOW", context={"footage": footage_id})

            _emit_event_and_alert(seg_id, footage_id, ts, moved, row, cfg)

        # ---------------- Stage telemetry ----------------
        _record_remaining_stages(footage_id, segs, totals, tier_counts, cfg,
                                 frames_total)

        elapsed_ms = round((time.time() - run_start) * 1000, 1)
        # `original_bytes` stays the size of the file the operator uploaded.
        # An earlier version overwrote it with the sum of the segment files,
        # which meant every savings percentage was measured against a baseline
        # the pipeline had produced itself — flattering and wrong. The segment
        # total now lives in its own column.
        sm.execute("UPDATE footages SET status='done',segment_bytes=?,stored_bytes=?,"
                   "processing_ms=?,frames_total=?,frames_analyzed=? WHERE id=?",
                   (totals["orig"], totals["stored"], elapsed_ms, frames_total,
                    totals["frames_analyzed"], footage_id))
        _generate_recommendations(footage_id)                            # FR59
        baseline = proj["original_bytes"] or totals["orig"]
        savings = round(100 * (1 - totals["stored"] / baseline), 1) if baseline else 0.0
        sm.log("processing",
               f"Stage 5/5 Complete: {len(segs)} segments — "
               f"HIGH={tier_counts['HIGH']} MEDIUM={tier_counts['MEDIUM']} "
               f"LOW={tier_counts['LOW']}, {savings}% storage saved"
               + (f", {parked} held for review" if parked else ""),
               context={"footage": footage_id, "segments": len(segs),
                        "tiers": tier_counts, "parked": parked,
                        "elapsed_ms": elapsed_ms})
    except Exception as e:
        sm.execute("UPDATE footages SET status='error' WHERE id=?", (footage_id,))
        sm.log("error", f"Pipeline failed: {e}", severity="error",
               context={"footage": footage_id})
        print("PIPELINE ERROR:", e)
        import traceback; traceback.print_exc()


# ---------------------------------------------------------------------------
# Per-segment result shaping. Both profiles fill the same row dict, so the DB
# write and the UI never need to know which pipeline produced a segment.
# ---------------------------------------------------------------------------
def _blank_row(ts, ratio):
    return {"objects": [], "actions": [], "track1": [], "track2": [], "overlay": [],
            "sentiment": 0.0, "sentiment_label": "neutral",
            "sentiment_backend": "", "action_label": "", "action_backend": "",
            "threat": "low", "hazard": "none", "ssig": 0.0, "tier": "LOW",
            "stored_path": "", "stored_bytes": 0, "fusion": {}, "features": {},
            "motion_ratio": ratio, "ts": ts, "purge_state": "",
            "_sampling": {}, "_timings": {}}


def _analyze_fusion(frames, ratio, night, cfg, fps):
    res, timings, sampling = analyze_segment_fusion(frames, ratio, night, cfg, fps)
    action = res["action"]
    return {
        # `objects` carries the union of both tracks so every existing consumer
        # (search, segment cards, metadata modal) keeps working unchanged.
        "objects": [{"label": d["label"], "confidence": d["confidence"],
                     "bbox": d.get("bbox", []), "critical": d.get("critical", False),
                     "track": 1, "frame_hits": d.get("frame_hits", 0)}
                    for d in res["track1"]]
                   + [{"label": d["label"], "confidence": d["confidence"],
                       "bbox": d.get("bbox", []), "critical": False, "track": 2,
                       "frame_hits": d.get("frame_hits", 0),
                       "instances": d.get("instances", 1)}
                      for d in res["track2"]],
        "actions": action.get("top", []),
        "track1": res["track1"], "track2": res["track2"],
        "sentiment": res["sentiment_score"],
        "sentiment_label": sentiment_model.label_for(res["sentiment_score"]),
        "sentiment_backend": res["sentiment_backend"],
        "action_label": action.get("label", ""), "action_backend": "r3d18",
        "threat": res["threat_level"], "hazard": res["hazard"], "ssig": res["ssig"],
        "tier": res["tier"], "stored_path": "", "stored_bytes": 0,
        "fusion": {"trace": res["trace"], "modifiers": res["modifiers"]},
        "features": res["features"], "purge_state": "",
        "overlay": res.get("overlay", []),
        "_sampling": sampling, "_timings": timings,
    }


def _analyze_legacy(frames, ratio, night, cfg):
    objects = object_detector.detect(frames, cfg["conf"])                # FR11
    actions = recognize_actions(frames, cfg["min_f"], cfg["action_backend"])  # FR12
    sc, lab, threat = sentiment_analyzer.analyze(frames, objects)        # FR13
    hazard = assess_hazard(objects, threat)                              # FR23
    ssig = compute_ssig(objects, actions, sc, hazard, cfg["ctx"], night)  # FR14/15
    return {
        "objects": objects, "actions": actions, "track1": [], "track2": [],
        "sentiment": sc, "sentiment_label": lab, "sentiment_backend": "mobilenet",
        "action_label": actions[0]["action"] if actions else "",
        "action_backend": cfg["action_backend"],
        "threat": threat, "hazard": hazard,
        "ssig": ssig, "tier": assign_tier(ssig, cfg["th_high"], cfg["th_low"]),
        "stored_path": "", "stored_bytes": 0, "fusion": {}, "features": {},
        "purge_state": "", "overlay": [],
        "_sampling": {"action_frames": min(16, len(frames)), "detector_frames": 4},
        "_timings": {},
    }


def _insert_segment(seg_id, footage_id, idx, cfg, row, moved, thumb,
                    orig_bytes, n_frames):
    metadata = {"objects": row["objects"], "actions": row["actions"],
                "track1": row["track1"], "track2": row["track2"],
                "sentiment": row["sentiment"], "threat": row["threat"],
                "hazard": row["hazard"], "ssig": row["ssig"], "tier": row["tier"],
                "motion_ratio": row["motion_ratio"], "ts": row["ts"],
                "action_label": row["action_label"],
                "overlay": row.get("overlay", []),
                "sampling": row["_sampling"], "timings": row["_timings"]}
    sm.execute(
        "INSERT INTO segments(id,footage_id,idx,start_time,end_time,ts,motion,"
        "thumb,objects,actions,sentiment_score,sentiment_label,threat_level,"
        "hazard_level,ssig,tier,stored_path,original_bytes,stored_bytes,metadata,"
        "fusion,features,action_backend,sentiment_backend,pipeline_profile,"
        "frames_total,frames_analyzed,purge_state) "
        "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (seg_id, footage_id, idx, idx * cfg["seg_s"], idx * cfg["seg_s"] + cfg["seg_s"],
         row["ts"], int(moved), thumb, json.dumps(row["objects"]),
         json.dumps(row["actions"]), row["sentiment"], row["sentiment_label"],
         row["threat"], row["hazard"], row["ssig"], row["tier"], row["stored_path"],
         orig_bytes, row["stored_bytes"], json.dumps(metadata),
         json.dumps(row["fusion"]), json.dumps(row["features"]),
         row["action_backend"], row["sentiment_backend"], cfg["profile"],
         n_frames, row["_sampling"].get("action_frames", 0)
         + row["_sampling"].get("detector_frames", 0), row["purge_state"]))


def _emit_event_and_alert(seg_id, footage_id, ts, moved, row, cfg):
    if not moved:
        return
    if row["ssig"] >= cfg["th_high"]:                                    # FR17
        sm.execute("INSERT INTO events(segment_id,footage_id,ts,objects,actions,"
                   "tier,ssig) VALUES(?,?,?,?,?,?,?)",
                   (seg_id, footage_id, ts, json.dumps(row["objects"]),
                    json.dumps(row["actions"]), row["tier"], row["ssig"]))
    if row["ssig"] >= cfg["alert_th"]:                                   # FR41
        sev = "critical" if row["ssig"] >= 0.9 else "high"
        sm.execute("INSERT INTO alerts(segment_id,footage_id,ts,severity,ssig,detail)"
                   " VALUES(?,?,?,?,?,?)",
                   (seg_id, footage_id, ts, sev, row["ssig"],
                    json.dumps({"threat": row["threat"], "hazard": row["hazard"],
                                "action": row["action_label"],
                                "objects": [o["label"] for o in row["objects"]]})))
        sm.log("system", f"ALERT {sev} ssig={row['ssig']}", severity="warning",
               context={"segment": seg_id})


def _record_remaining_stages(footage_id, segs, totals, tier_counts, cfg, frames_total):
    """Write stages 2-5 of the reduction funnel from the run's own counters."""
    n_segs = len(segs)
    seg_bytes = totals["orig"]

    # Stage 2 — motion filter: segments that avoided deep analysis entirely.
    sm.record_stage(footage_id, 2, "Invaligator Motion Filter", "segments",
                    items_in=n_segs, items_out=totals["moving"],
                    bytes_in=seg_bytes, bytes_out=seg_bytes,
                    frames_in=frames_total, frames_out=frames_total,
                    duration_ms=round(totals["motion_ms"], 1),
                    detail={"model": "MOG2 background subtraction",
                            "sensitivity": cfg["sens"],
                            "skipped": n_segs - totals["moving"],
                            "note": "static segments never reach the models — "
                                    "this is compute reduction, not disk"})

    # Stage 3 — frame sampling: how much of the pixel stream the models saw.
    analyzed = totals["frames_analyzed"] + totals["det_frames"]
    sm.record_stage(footage_id, 3, "Frame Sampling", "frames",
                    items_in=totals["moving"], items_out=totals["moving"],
                    frames_in=frames_total, frames_out=analyzed,
                    duration_ms=0.0,
                    detail={"action_frames": totals["frames_analyzed"],
                            "detector_frames": totals["det_frames"],
                            "detector_stride_seconds": cfg["stride_s"],
                            "note": "16 frames per segment for action recognition, "
                                    "~1 fps for the detectors"})

    # Stage 4 — inference + fusion: pixels become a structured record.
    approx_pixel_bytes = analyzed * 112 * 112 * 3
    meta_rows = sm.q("SELECT SUM(LENGTH(metadata)) b FROM segments WHERE footage_id=?",
                     (footage_id,), one=True)
    meta_bytes = (meta_rows or {}).get("b") or 0
    models = (["R3D-18 (UCF-Crime)", "YOLOv8s Track 1 (suspicious)",
               "YOLOv8s Track 2 (COCO context)", "Sentiment MLP"]
              if cfg["profile"] == "fusion"
              else ["X3D-S (Kinetics-400)", "YOLOv8n (COCO)", "MobileNetV3"])
    sm.record_stage(footage_id, 4, "Inference & Fusion", "bytes",
                    items_in=totals["moving"], items_out=totals["moving"],
                    bytes_in=approx_pixel_bytes, bytes_out=meta_bytes,
                    frames_in=analyzed, frames_out=0,
                    duration_ms=round(totals["infer_ms"], 1),
                    detail={"models": models, "profile": cfg["profile"],
                            "note": "sampled pixels collapse into one searchable "
                                    "record per segment"})

    # Stage 5 — tiered storage: the actual disk reduction.
    sm.record_stage(footage_id, 5, "Tiered Storage", "bytes",
                    items_in=n_segs, items_out=n_segs,
                    bytes_in=totals["orig"], bytes_out=totals["stored"],
                    duration_ms=round(totals["store_ms"], 1),
                    detail={"tiers": tier_counts,
                            "grace_hours": cfg["grace_h"],
                            "uploaded_bytes": totals.get("uploaded", 0),
                            "note": "HIGH lossless, MEDIUM transcoded, "
                                    "LOW keyframe-only"})


# ---------------------------------------------------------------------------
# FR59 — intelligent recommendation generation
# ---------------------------------------------------------------------------
def _generate_recommendations(footage_id):
    rep = sm.storage_report()
    if rep["storage_savings_percent"] < 50:
        sm.add_recommendation("storage",
            "Storage savings below 50% — consider lowering threshold_high to tier more segments down.")
    high = sm.q("SELECT COUNT(*) n FROM segments WHERE footage_id=? AND tier='HIGH'",
                (footage_id,), one=True)["n"]
    if high > 10:
        sm.add_recommendation("tuning",
            f"{high} HIGH-tier segments detected — review context rules to reduce false criticals.")
    alerts = sm.q("SELECT COUNT(*) n FROM alerts WHERE footage_id=?", (footage_id,), one=True)["n"]
    if alerts:
        sm.add_recommendation("security",
            f"{alerts} alert(s) raised in this project — operator review recommended.")
    pending = sm.q("SELECT COUNT(*) n FROM purge_queue WHERE footage_id=? AND status='pending'",
                   (footage_id,), one=True)["n"]
    if pending:
        sm.add_recommendation("retention",
            f"{pending} LOW-tier segment(s) held in the review queue — confirm or "
            f"restore before the grace period expires.")


# ---------------------------------------------------------------------------
# FR60 — compute device auto selection
# ---------------------------------------------------------------------------
def select_device():
    try:
        import torch
        if torch.cuda.is_available():
            return "cuda"
        if getattr(torch.backends, "mps", None) and torch.backends.mps.is_available():
            return "mps"
    except Exception:
        pass
    return "cpu"


# ---------------------------------------------------------------------------
# FR13 direct single-frame sentiment (used by ITSOIntegratedSystem API)
# ---------------------------------------------------------------------------
def analyze_frame_sentiment(frame):
    return sentiment_analyzer.analyze([frame], [])
