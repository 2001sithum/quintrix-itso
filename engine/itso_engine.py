"""
itso_engine.py — the 5-stage ITSO video processing pipeline.
Stage 1 Ingestion → Stage 2 Motion Filter (Invaligator) → Stage 3 Deep Analysis
→ Stage 4 Prioritization (Ssig) → Stage 5 Tiered Storage.
Covers FR08-23, FR33, FR40, FR41, FR55, FR59.
"""
import os, uuid, json, datetime as dt
import cv2
import numpy as np

from engine import storage_manager as sm
from models import object_detector, action_recognizer, sentiment_analyzer, tier_segmenters

ARCHIVE = sm.ARCHIVE_DIR


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


def segment_video(path, seg_seconds):
    cap = cv2.VideoCapture(path)
    fps = cap.get(cv2.CAP_PROP_FPS) or 25.0
    seg_frames = max(1, int(seg_seconds * fps))
    w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    fourcc = cv2.VideoWriter_fourcc(*"mp4v")
    seg_dir = os.path.join(ARCHIVE, "segments")
    segments, idx, frame_id = [], 0, 0
    writer, seg_path, buf = None, None, []
    while True:
        ok, frame = cap.read()
        if not ok:
            break
        if frame_id % seg_frames == 0:
            if writer:
                writer.release(); segments.append((idx, seg_path, buf)); idx += 1
            seg_path = os.path.join(seg_dir, f"{uuid.uuid4().hex}.mp4")
            writer = cv2.VideoWriter(seg_path, fourcc, fps, (w, h)); buf = []
        writer.write(frame)
        if len(buf) < 32:
            buf.append(frame.copy())
        frame_id += 1
    if writer:
        writer.release(); segments.append((idx, seg_path, buf))
    cap.release()
    return fps, segments


# ---------------------------------------------------------------------------
# Stage 2 — Invaligator motion filter (FR10). MOG2 + motion-ratio gate.
# ---------------------------------------------------------------------------
def invaligator(frames, sensitivity):
    if len(frames) < 2:
        return False, 0.0
    mog = cv2.createBackgroundSubtractorMOG2(detectShadows=False)
    peak = 0.0
    for f in frames:
        mask = mog.apply(f)
        ratio = float((mask > 200).mean())
        peak = max(peak, ratio)
    return peak >= sensitivity, round(peak, 5)


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
# Stage 4 — Ssig prioritization engine (FR14/FR15) + hazard (FR23)
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
# Full per-project pipeline (async worker calls this) — FR29 background
# ---------------------------------------------------------------------------
def process_project(project_id):
    cfg = sm.get_config()
    th_high = float(cfg["threshold_high"]); th_low = float(cfg["threshold_low"])
    alert_th = float(cfg["alert_threshold"]); sens = float(cfg["motion_sensitivity"])
    conf = float(cfg["object_conf_threshold"]); min_f = int(cfg["action_min_frames"])
    seg_s = int(cfg["segment_seconds"]); ctx = json.loads(cfg["context_rules"])

    proj = sm.q("SELECT * FROM projects WHERE id=?", (project_id,), one=True)
    sm.execute("UPDATE projects SET status='processing' WHERE id=?", (project_id,))
    sm.log("processing", f"Pipeline started for {project_id}", context={"job": proj["job_id"]})
    try:
        meta = extract_tech_metadata(proj["original_path"])              # FR28
        sm.execute("UPDATE projects SET fps=?,duration=?,width=?,height=?,codec=? WHERE id=?",
                   (meta["fps"], meta["duration"], meta["width"], meta["height"],
                    meta["codec"], project_id))
        fps, segs = segment_video(proj["original_path"], seg_s)          # FR09
        base_ts = dt.datetime.fromisoformat(proj["created_at"])
        tier_dir = os.path.join(ARCHIVE, "tiers")
        total_o = total_s = 0

        for idx, seg_path, frames in segs:
            seg_id = uuid.uuid4().hex
            ts = (base_ts + dt.timedelta(seconds=idx * seg_s)).isoformat()
            moved, ratio = invaligator(frames, sens)                     # FR10
            thumb = make_thumb(frames, seg_id)                           # FR33
            orig_bytes = os.path.getsize(seg_path) if os.path.exists(seg_path) else 0

            objects, actions = [], []
            sc, lab, threat, hazard, ssig, tier = 0.0, "neutral", "low", "none", 0.0, "LOW"
            stored_path, stored_bytes = "", 0

            if moved:
                objects = object_detector.detect(frames, conf)          # FR11
                actions = action_recognizer.recognize(frames, min_f)    # FR12
                sc, lab, threat = sentiment_analyzer.analyze(frames, objects)  # FR13
                hazard = assess_hazard(objects, threat)                  # FR23
                ssig = compute_ssig(objects, actions, sc, hazard, ctx)   # FR14/15
                tier = assign_tier(ssig, th_high, th_low)                # FR19
                stored_path, stored_bytes = tier_segmenters.execute_tier(  # FR18/20-22
                    tier, seg_path, tier_dir, seg_id)
                if tier != "HIGH":       # medium/low discard original (FR21/22)
                    try: os.remove(seg_path)
                    except OSError: pass
            else:
                stored_path, stored_bytes = "", 0
                try: os.remove(seg_path)
                except OSError: pass

            metadata = {"objects": objects, "actions": actions, "sentiment": sc,
                        "threat": threat, "hazard": hazard, "ssig": ssig,
                        "tier": tier, "motion_ratio": ratio, "ts": ts}
            sm.execute(
                "INSERT INTO segments(id,project_id,idx,start_time,end_time,ts,motion,"
                "thumb,objects,actions,sentiment_score,sentiment_label,threat_level,"
                "hazard_level,ssig,tier,stored_path,original_bytes,stored_bytes,metadata) "
                "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (seg_id, project_id, idx, idx * seg_s, idx * seg_s + seg_s, ts,
                 int(moved), thumb, json.dumps(objects), json.dumps(actions), sc, lab,
                 threat, hazard, ssig, tier, stored_path, orig_bytes, stored_bytes,
                 json.dumps(metadata)))
            total_o += orig_bytes; total_s += stored_bytes

            # FR17 event extraction
            if moved and ssig >= th_high:
                sm.execute("INSERT INTO events(segment_id,project_id,ts,objects,actions,"
                           "tier,ssig) VALUES(?,?,?,?,?,?,?)",
                           (seg_id, project_id, ts, json.dumps(objects),
                            json.dumps(actions), tier, ssig))
            # FR41 alert generation
            if moved and ssig >= alert_th:
                sev = "critical" if ssig >= 0.9 else "high"
                sm.execute("INSERT INTO alerts(segment_id,project_id,ts,severity,ssig,detail)"
                           " VALUES(?,?,?,?,?,?)",
                           (seg_id, project_id, ts, sev, ssig,
                            json.dumps({"threat": threat, "hazard": hazard,
                                        "objects": [o["label"] for o in objects]})))
                sm.log("system", f"ALERT {sev} ssig={ssig}", severity="warning",
                       context={"segment": seg_id})

        sm.execute("UPDATE projects SET status='done',original_bytes=?,stored_bytes=? WHERE id=?",
                   (total_o, total_s, project_id))
        _generate_recommendations(project_id)                            # FR59
        sm.log("processing", f"Pipeline complete for {project_id}",
               context={"segments": len(segs)})
    except Exception as e:
        sm.execute("UPDATE projects SET status='error' WHERE id=?", (project_id,))
        sm.log("error", f"Pipeline failed: {e}", severity="error",
               context={"project": project_id})
        print("PIPELINE ERROR:", e)


# ---------------------------------------------------------------------------
# FR59 — intelligent recommendation generation
# ---------------------------------------------------------------------------
def _generate_recommendations(project_id):
    rep = sm.storage_report()
    if rep["storage_savings_percent"] < 50:
        sm.add_recommendation("storage",
            "Storage savings below 50% — consider lowering threshold_high to tier more segments down.")
    high = sm.q("SELECT COUNT(*) n FROM segments WHERE project_id=? AND tier='HIGH'",
                (project_id,), one=True)["n"]
    if high > 10:
        sm.add_recommendation("tuning",
            f"{high} HIGH-tier segments detected — review context rules to reduce false criticals.")
    alerts = sm.q("SELECT COUNT(*) n FROM alerts WHERE project_id=?", (project_id,), one=True)["n"]
    if alerts:
        sm.add_recommendation("security",
            f"{alerts} alert(s) raised in this project — operator review recommended.")


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
