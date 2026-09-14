"""analytics.py — derived reporting that sits on top of the raw tables.

Everything here is computed from measurements the pipeline already recorded
(`stage_stats`, `segments`, `footages`); nothing is re-simulated. The one
exception is `savings_projection`, which is explicitly a forecast and labels
its assumptions in the payload it returns.
"""
import datetime as dt
import json
import math

from engine import storage_manager as sm


# ---------------------------------------------------------------------------
# Per-footage reduction funnel
# ---------------------------------------------------------------------------
def footage_funnel(footage_id):
    """The measured stage-by-stage funnel for one footage, plus the honest
    end-to-end figure: stored bytes against the file the operator uploaded."""
    proj = sm.q("SELECT * FROM footages WHERE id=?", (footage_id,), one=True)
    if not proj:
        return None
    stages = sm.stage_report(footage_id)
    uploaded = proj.get("original_bytes") or 0
    stored = proj.get("stored_bytes") or 0
    seg_bytes = proj.get("segment_bytes") or 0

    tiers = sm.q("SELECT tier, COUNT(*) n, SUM(original_bytes) ob, SUM(stored_bytes) sb "
                 "FROM segments WHERE footage_id=? AND tier!='' GROUP BY tier",
                 (footage_id,))
    return {
        "footage": {k: proj[k] for k in (
            "id", "name", "status", "duration", "fps", "width", "height",
            "original_bytes", "segment_bytes", "stored_bytes", "pipeline_profile",
            "processing_ms", "frames_total", "frames_analyzed", "created_at")
            if k in proj},
        "stages": stages,
        "end_to_end": {
            "uploaded_bytes": uploaded,
            "segment_bytes": seg_bytes,
            "stored_bytes": stored,
            # Measured against the upload, which is the only baseline the
            # operator did not get from us.
            "savings_percent": round(100 * (1 - stored / uploaded), 2) if uploaded else 0.0,
            "compression_ratio": round(uploaded / stored, 2) if stored else 0.0,
        },
        "tiers": {r["tier"]: {"count": r["n"], "original": r["ob"] or 0,
                              "stored": r["sb"] or 0} for r in tiers},
    }


# ---------------------------------------------------------------------------
# Fleet-wide savings projection
# ---------------------------------------------------------------------------
def savings_projection(months=36, growth_percent_per_month=6.0,
                       cost_per_gb_month=0.023, retention_months=None,
                       profile=None):
    """Project storage and cost over time, from the measured savings rate.

    The observed savings rate comes from real processed footage; everything
    else is a stated assumption, returned in `assumptions` so a reader can
    disagree with it explicitly.

    Two curves are returned deliberately:

    - `flat_fleet` — camera count constant. Cumulative saving grows **linearly**.
    - `growing_fleet` — camera count compounds at `growth_percent_per_month`.
      Monthly ingest is then ingest₀·(1+g)^t, so cumulative saving is a
      geometric series and grows **exponentially**.

    The exponential shape comes entirely from fleet growth compounding, not
    from the pipeline getting better over time. Presenting only the
    exponential curve without that caveat would be misleading, so both are
    returned and the caller is expected to show both.
    """
    rep = sm.storage_report()
    # `profile` restricts the measurement corpus to footage processed by one
    # pipeline. This matters: footage processed before the lossless-segmentation
    # fix stored *more* than they ingested, because the splitter re-encoded
    # H.264 into MPEG-4 Part 2 and the HIGH tier then stream-copied the bloat.
    # Averaging those in understates the deployed pipeline by a wide margin, so
    # the caller can ask for the profile actually in service — and is told which
    # corpus produced the number either way.
    where = "status='done'"
    args = ()
    if profile:
        where += " AND pipeline_profile=?"
        args = (profile,)
    proj = sm.q(f"SELECT SUM(original_bytes) ob, SUM(stored_bytes) sb, "
                f"SUM(duration) dur, COUNT(*) n FROM footages WHERE {where}",
                args, one=True) or {}
    if profile and not (proj.get("n") or 0):
        # No footage on that profile yet — fall back to everything rather than
        # dividing by zero, and say so in the assumptions.
        profile = None
        proj = sm.q("SELECT SUM(original_bytes) ob, SUM(stored_bytes) sb, "
                    "SUM(duration) dur, COUNT(*) n FROM footages WHERE status='done'",
                    one=True) or {}
    uploaded = proj.get("ob") or 0
    stored = proj.get("sb") or 0
    hours = (proj.get("dur") or 0) / 3600.0

    savings_rate = (1 - stored / uploaded) if uploaded else \
        (rep["storage_savings_percent"] / 100.0)
    savings_rate = max(0.0, min(0.999, savings_rate))

    # Ingest per month, extrapolated from observed bytes-per-hour of footage at
    # 24/7 recording. Falls back to a stated nominal rate with no corpus.
    bytes_per_hour = (uploaded / hours) if hours > 0.01 else 0
    measured = bytes_per_hour > 0
    if not measured:
        bytes_per_hour = 1.2e9      # ~1.2 GB/h — nominal 1080p H.264 CCTV stream
    monthly_ingest = bytes_per_hour * 24 * 30

    g = growth_percent_per_month / 100.0
    GB = 1024 ** 3

    flat, growing = [], []
    cum_base_f = cum_itso_f = 0.0
    cum_base_g = cum_itso_g = 0.0
    for m in range(1, months + 1):
        # Retention: with a finite window, only the last `retention_months` of
        # data is still on disk, so the curves plateau instead of growing.
        def live(series_month, ingest):
            if retention_months:
                start = max(1, series_month - retention_months + 1)
                return sum(ingest(k) for k in range(start, series_month + 1))
            return None

        ingest_flat = monthly_ingest
        ingest_grow = monthly_ingest * ((1 + g) ** (m - 1))

        cum_base_f += ingest_flat
        cum_itso_f += ingest_flat * (1 - savings_rate)
        cum_base_g += ingest_grow
        cum_itso_g += ingest_grow * (1 - savings_rate)

        if retention_months:
            lo = max(1, m - retention_months + 1)
            cum_base_f = sum(monthly_ingest for _ in range(lo, m + 1))
            cum_itso_f = cum_base_f * (1 - savings_rate)
            cum_base_g = sum(monthly_ingest * ((1 + g) ** (k - 1))
                             for k in range(lo, m + 1))
            cum_itso_g = cum_base_g * (1 - savings_rate)

        flat.append({
            "month": m,
            "baseline_gb": round(cum_base_f / GB, 2),
            "itso_gb": round(cum_itso_f / GB, 2),
            "saved_gb": round((cum_base_f - cum_itso_f) / GB, 2),
            "saved_cost": round((cum_base_f - cum_itso_f) / GB * cost_per_gb_month, 2),
        })
        growing.append({
            "month": m,
            "baseline_gb": round(cum_base_g / GB, 2),
            "itso_gb": round(cum_itso_g / GB, 2),
            "saved_gb": round((cum_base_g - cum_itso_g) / GB, 2),
            "saved_cost": round((cum_base_g - cum_itso_g) / GB * cost_per_gb_month, 2),
        })

    # Doubling time of the growing-fleet saving — the number that makes the
    # "exponential" claim concrete rather than rhetorical.
    doubling = round(math.log(2) / math.log(1 + g), 1) if g > 0 else None

    return {
        "savings_rate": round(savings_rate, 4),
        "savings_percent": round(savings_rate * 100, 2),
        "flat_fleet": flat,
        "growing_fleet": growing,
        "doubling_months": doubling,
        "assumptions": {
            "savings_rate_source": (
                f"measured on {proj.get('n') or 0} footage(s) processed by the "
                f"{profile} pipeline" if profile else
                "measured across all processed footage, including legacy runs"
                if uploaded else "no corpus yet — using tier defaults"),
            "ingest_rate_source": "measured bytes per hour of analysed footage"
                                  if measured else "nominal 1.2 GB/h 1080p stream",
            "bytes_per_hour": round(bytes_per_hour),
            "recording": "24/7",
            "growth_percent_per_month": growth_percent_per_month,
            "cost_per_gb_month_usd": cost_per_gb_month,
            "retention_months": retention_months,
            "why_exponential": "The growing-fleet curve compounds because camera "
                               "count grows month over month; the pipeline's "
                               "per-hour savings rate is constant. With a flat "
                               "fleet the saving is linear.",
        },
        "corpus": {"footages": proj.get("n") or 0,
                   "hours_analysed": round(hours, 2),
                   "profile": profile or "all",
                   "uploaded_bytes": uploaded, "stored_bytes": stored},
    }


# ---------------------------------------------------------------------------
# Model registry
# ---------------------------------------------------------------------------
def model_registry():
    """What is actually loaded and scoring, stage by stage.

    This exists because the report and the appendix disagreed about which
    action model was deployed. Rather than settling it in prose alone, the
    running system now reports it: this endpoint reads the live config and the
    real checkpoint paths, so the answer cannot drift from the code again.
    """
    from models import action_recognizer_r3d18 as r3d
    from models import suspicious_detector as t1
    from models import context_detector as t2
    from models import sentiment_model

    cfg = sm.get_config()
    profile = cfg.get("pipeline_profile", "fusion")
    sent_meta = sentiment_model.metadata() or {}

    # `stage` is the ITSO 5-stage pipeline number the model runs in, which is
    # what the Workflow Simulator groups by. The reference notebook numbered its
    # own stages differently (its "Stage 2" was action recognition); `ref_stage`
    # keeps that lineage visible without letting it drive the UI, which
    # previously showed R3D-18 attached to the motion filter.
    fusion_models = [
        {"stage": 4, "ref_stage": 2, "role": "Action / anomaly recognition",
         "name": "R3D-18 (ResNet3D-18)", "dataset": "UCF-Crime, 14 classes",
         "weights": r3d.weights_path(), "available": r3d.available(),
         "loaded_from": r3d.loaded_from(),
         "input": "16 frames @ 112×112, Kinetics normalization",
         "output": "14-class probability distribution",
         "active": True},
        {"stage": 4, "ref_stage": 3, "role": "Track 1 — suspicious objects",
         "name": "YOLOv8s (fine-tuned)",
         "dataset": ", ".join(t1.CLASSES),
         "weights": t1.weights_path(), "available": t1.available(),
         "input": "~1 fps sampled frames",
         "output": "per-class max confidence + frame hits",
         "active": True},
        {"stage": 4, "ref_stage": 3, "role": "Track 2 — context objects",
         "name": "YOLOv8s (stock COCO)",
         "dataset": ", ".join(sorted(t2.KEEP_CLASSES)),
         "weights": t2.weights_path(), "available": True,
         "input": "~1 fps sampled frames",
         "output": "per-class max confidence + instance count",
         "active": True},
        {"stage": 4, "ref_stage": 4, "role": "Sentiment / threat scoring",
         "name": sent_meta.get("arch", "MLP (untrained — rule fallback)"),
         "dataset": "18-scenario expert rubric corpus",
         "weights": sentiment_model.checkpoint_path(),
         "available": sentiment_model.available(),
         "status": sentiment_model.status(),
         "input": "16-D fusion feature vector",
         "output": "0..1 sentiment score",
         "metrics": (sent_meta.get("metrics") or {}).get("test"),
         "trained_at": sent_meta.get("trained_at"),
         "active": True},
    ]
    legacy_models = [
        {"stage": 4, "ref_stage": 2, "role": "Action recognition", "name": "X3D-S",
         "dataset": "Kinetics-400, 400 everyday-activity classes",
         "weights": "torch.hub (facebookresearch/pytorchvideo)", "available": True,
         "input": "16 frames @ 182×182", "output": "top-5 Kinetics labels",
         "active": profile == "legacy" and cfg.get("action_model_backend") != "r3d18"},
        {"stage": 4, "ref_stage": 3, "role": "Object detection", "name": "YOLOv8n (stock COCO)",
         "dataset": "COCO, 80 classes", "weights": "yolov8n.pt", "available": True,
         "input": "4 sampled frames", "output": "labelled boxes",
         "active": profile == "legacy"},
        {"stage": 4, "ref_stage": 4, "role": "Scene sentiment", "name": "MobileNetV3-Small",
         "dataset": "ImageNet (used as a scene-activation proxy)",
         "weights": "torchvision pretrained", "available": True,
         "input": "1 mid-segment frame", "output": "activation → heuristic score",
         "active": profile == "legacy"},
    ]

    # Stages 2 and 5 are classical CV / encoding rather than learned models,
    # but the simulator should still say what executes there.
    stage_ops = [
        {"stage": 2, "role": "Motion gate", "name": "MOG2 background subtraction",
         "dataset": "no training — adaptive background model",
         "weights": "—", "available": True,
         "input": "decimated segment frames",
         "output": "peak motion ratio + sustained-hit count", "active": True},
        {"stage": 5, "role": "Tier execution", "name": "ffmpeg / OpenCV",
         "dataset": "HIGH stream-copy · MEDIUM 480p CRF30 · LOW keyframes",
         "weights": "—", "available": True,
         "input": "segment file + assigned tier",
         "output": "tiered artefact on disk", "active": True},
    ]

    return {
        "active_profile": profile,
        "stage_ops": stage_ops,
        "deployed_action_model": ("R3D-18 (UCF-Crime)" if profile == "fusion"
                                  else ("R3D-18 (UCF-Crime)"
                                        if cfg.get("action_model_backend") == "r3d18"
                                        else "X3D-S (Kinetics-400)")),
        "fusion": fusion_models,
        "legacy": legacy_models,
        "feature_contract": None,   # filled by the caller to avoid a circular import
    }


# ---------------------------------------------------------------------------
# Segment fusion detail
# ---------------------------------------------------------------------------
def segment_explain(segment_id):
    """Everything needed to explain one segment's score: the feature vector,
    the weighted channels, the context modifiers, and the model timings."""
    s = sm.q("SELECT * FROM segments WHERE id=?", (segment_id,), one=True)
    if not s:
        return None
    for k in ("objects", "actions", "metadata", "fusion", "features"):
        try:
            s[k] = json.loads(s.get(k) or ("[]" if k in ("objects", "actions") else "{}"))
        except Exception:
            s[k] = [] if k in ("objects", "actions") else {}
    return s


# ---------------------------------------------------------------------------
# Deep analytics — what the models actually found, and what the engine did with it
# ---------------------------------------------------------------------------
def _segment_rows(footage_id=None, motion_only=True, profile=None):
    where, args = [], []
    if motion_only:
        where.append("motion=1")
    if footage_id:
        where.append("footage_id=?")
        args.append(footage_id)
    if profile:
        where.append("pipeline_profile=?")
        args.append(profile)
    sql = "SELECT * FROM segments"
    if where:
        sql += " WHERE " + " AND ".join(where)
    return sm.q(sql + " ORDER BY ts", tuple(args))


def _track_of(obj):
    """Which detector produced this object.

    Segments written by the fusion pipeline carry an explicit `track`. Legacy
    segments do not, and guessing from the `critical` flag is wrong — the legacy
    COCO detector marks `person`, `car` and `truck` as critical, which would file
    them under the purpose-trained threat detector and make it look like it
    detects pedestrians. Fall back to class membership instead: the Track 1 class
    set is known and disjoint from COCO.
    """
    t = obj.get("track")
    if t in (1, 2):
        return t
    from models import suspicious_detector as _t1
    return 1 if obj.get("label") in _t1.CLASSES else 2


def _loads(v, default):
    try:
        out = json.loads(v or "")
        return out if out else default
    except Exception:
        return default


def _histogram(values, bins=10, lo=0.0, hi=1.0):
    """Fixed-range histogram. Returns bucket edges and counts, which is what a
    bar chart needs — the frontend should not be re-deriving binning."""
    width = (hi - lo) / bins
    counts = [0] * bins
    for v in values:
        if v is None:
            continue
        idx = int((min(hi, max(lo, float(v))) - lo) / width)
        counts[min(bins - 1, idx)] += 1
    return [{"lo": round(lo + i * width, 3), "hi": round(lo + (i + 1) * width, 3),
             "count": c} for i, c in enumerate(counts)]


def detection_analytics(footage_id=None, profile=None):
    """What the three models found, across every analysed segment.

    Split by track, because conflating them would be misleading: Track 1 is a
    purpose-trained threat detector and Track 2 is generic context. A `person`
    appearing in 90% of segments means something completely different from a
    `Gun` appearing in 2%.
    """
    rows = _segment_rows(footage_id, profile=profile)
    n = len(rows)
    t1, t2, actions = {}, {}, {}
    co_occurrence = {}
    critical_segments = 0

    for r in rows:
        objs = _loads(r.get("objects"), [])
        acts = _loads(r.get("actions"), [])
        labels_here = set()

        for o in objs:
            bucket = t1 if _track_of(o) == 1 else t2
            e = bucket.setdefault(o["label"], {
                "label": o["label"], "segments": 0, "max_conf": 0.0,
                "sum_conf": 0.0, "sum_hits": 0, "critical": bool(o.get("critical")),
            })
            e["segments"] += 1
            e["max_conf"] = max(e["max_conf"], o.get("confidence", 0.0))
            e["sum_conf"] += o.get("confidence", 0.0)
            e["sum_hits"] += o.get("frame_hits", 0)
            labels_here.add(o["label"])
            if o.get("critical"):
                critical_segments += 0   # counted per segment below

        if any(o.get("critical") for o in objs):
            critical_segments += 1

        # Which labels appear together — the pairs an operator would want to
        # search for (a weapon plus a crowd, a bag with no person).
        for a in sorted(labels_here):
            for b in sorted(labels_here):
                if a < b:
                    k = f"{a}|{b}"
                    co_occurrence[k] = co_occurrence.get(k, 0) + 1

        # Action distribution uses the top-1 label per segment; the runner-up
        # is recorded separately so an operator can see near-misses.
        if acts:
            top = acts[0]
            e = actions.setdefault(top["action"], {
                "action": top["action"], "segments": 0, "sum_conf": 0.0,
                "max_conf": 0.0, "runner_up": 0,
            })
            e["segments"] += 1
            e["sum_conf"] += top.get("confidence", 0.0)
            e["max_conf"] = max(e["max_conf"], top.get("confidence", 0.0))
            if len(acts) > 1:
                ru = actions.setdefault(acts[1]["action"], {
                    "action": acts[1]["action"], "segments": 0, "sum_conf": 0.0,
                    "max_conf": 0.0, "runner_up": 0})
                ru["runner_up"] += 1

    def finish(d, key):
        out = []
        for e in d.values():
            seg = e["segments"] or 1
            e["avg_conf"] = round(e["sum_conf"] / seg, 3)
            e["prevalence"] = round(100 * e["segments"] / n, 2) if n else 0.0
            if "sum_hits" in e:
                e["avg_frame_hits"] = round(e["sum_hits"] / seg, 2)
                e.pop("sum_hits")
            e.pop("sum_conf")
            out.append(e)
        out.sort(key=lambda e: (-e["segments"], e[key]))
        return out

    pairs = sorted(({"a": k.split("|")[0], "b": k.split("|")[1], "count": v}
                    for k, v in co_occurrence.items()),
                   key=lambda d: -d["count"])[:15]

    return {
        "segments_analysed": n,
        "profile": profile or "all",
        "track1": finish(t1, "label"),
        "track2": finish(t2, "label"),
        "actions": finish(actions, "action"),
        "co_occurrence": pairs,
        "critical_segments": critical_segments,
        "critical_rate": round(100 * critical_segments / n, 2) if n else 0.0,
        "confidence_histogram": {
            "track1": _histogram([o.get("confidence", 0) for r in rows
                                  for o in _loads(r.get("objects"), [])
                                  if _track_of(o) == 1]),
            "track2": _histogram([o.get("confidence", 0) for r in rows
                                  for o in _loads(r.get("objects"), [])
                                  if _track_of(o) == 2]),
            "action": _histogram([a[0].get("confidence", 0) for r in rows
                                  if (a := _loads(r.get("actions"), []))]),
        },
    }


def decision_analytics(footage_id=None, profile=None):
    """How the engine turned model output into a tiering decision.

    This is the part that is normally invisible: which context modifiers fire
    and how often, how much each scoring channel actually contributes, and
    where segments land on the Ssig scale relative to the live thresholds.
    """
    rows = _segment_rows(footage_id, profile=profile)
    n = len(rows)
    cfg = sm.get_config()
    th_low = float(cfg.get("threshold_low", 0.4))
    th_high = float(cfg.get("threshold_high", 0.7))

    modifiers, channels = {}, {}
    ssigs, sentiments = [], []
    tier_counts = {"HIGH": 0, "MEDIUM": 0, "LOW": 0}
    hazard_counts, threat_counts = {}, {}
    traced = 0

    for r in rows:
        ssigs.append(r.get("ssig") or 0.0)
        sentiments.append(r.get("sentiment_score") or 0.0)
        tier_counts[r.get("tier") or "LOW"] = tier_counts.get(r.get("tier") or "LOW", 0) + 1
        hz = r.get("hazard_level") or "none"
        hazard_counts[hz] = hazard_counts.get(hz, 0) + 1
        th = r.get("threat_level") or "low"
        threat_counts[th] = threat_counts.get(th, 0) + 1

        fus = _loads(r.get("fusion"), {})
        if not fus:
            continue
        traced += 1
        for m in fus.get("modifiers", []):
            e = modifiers.setdefault(m["name"], {
                "name": m["name"], "fired": 0, "sum_amount": 0.0,
                "max_amount": 0.0, "why": m.get("why", "")})
            e["fired"] += 1
            e["sum_amount"] += m.get("amount", 0.0)
            e["max_amount"] = max(e["max_amount"], abs(m.get("amount", 0.0)))
        for t in fus.get("trace", []):
            e = channels.setdefault(t["channel"], {
                "channel": t["channel"], "weight": t.get("weight", 0),
                "sum_contribution": 0.0, "n": 0, "max_contribution": 0.0})
            contrib = t.get("weight", 0) * t.get("value", 0)
            e["sum_contribution"] += contrib
            e["max_contribution"] = max(e["max_contribution"], contrib)
            e["n"] += 1

    mod_list = []
    for e in modifiers.values():
        e["fire_rate"] = round(100 * e["fired"] / traced, 2) if traced else 0.0
        e["avg_amount"] = round(e["sum_amount"] / e["fired"], 4) if e["fired"] else 0.0
        e["total_amount"] = round(e["sum_amount"], 3)
        e.pop("sum_amount")
        mod_list.append(e)
    mod_list.sort(key=lambda e: -e["fired"])

    ch_list = []
    total_contrib = sum(e["sum_contribution"] for e in channels.values()) or 1
    for e in channels.values():
        e["avg_contribution"] = round(e["sum_contribution"] / e["n"], 4) if e["n"] else 0
        e["share_percent"] = round(100 * e["sum_contribution"] / total_contrib, 2)
        e["sum_contribution"] = round(e["sum_contribution"], 3)
        ch_list.append(e)
    ch_list.sort(key=lambda e: -e["share_percent"])

    # How close segments sit to a boundary — a proxy for how much a small
    # scoring error would change the outcome.
    near_band = 0.05
    near_low = sum(1 for v in ssigs if abs(v - th_low) <= near_band)
    near_high = sum(1 for v in ssigs if abs(v - th_high) <= near_band)

    return {
        "segments_analysed": n,
        "profile": profile or "all",
        "segments_with_trace": traced,
        "thresholds": {"low": th_low, "high": th_high},
        "modifiers": mod_list,
        "channels": ch_list,
        "tiers": tier_counts,
        "hazards": hazard_counts,
        "threats": threat_counts,
        "ssig_histogram": _histogram(ssigs, bins=20),
        "sentiment_histogram": _histogram(sentiments, bins=20),
        "ssig_stats": {
            "mean": round(sum(ssigs) / n, 4) if n else 0.0,
            "min": round(min(ssigs), 4) if ssigs else 0.0,
            "max": round(max(ssigs), 4) if ssigs else 0.0,
        },
        "boundary_sensitivity": {
            "band": near_band,
            "near_low": near_low, "near_high": near_high,
            "percent": round(100 * (near_low + near_high) / n, 2) if n else 0.0,
            "note": (f"segments within ±{near_band} of a tier boundary — a small "
                     "scoring error would flip their storage outcome"),
        },
    }


def alert_analytics(footage_id=None):
    """Alert volume, severity mix, what triggered them, and how fast they are
    acknowledged."""
    where, args = [], []
    if footage_id:
        where.append("footage_id=?")
        args.append(footage_id)
    sql = "SELECT * FROM alerts"
    if where:
        sql += " WHERE " + " AND ".join(where)
    rows = sm.q(sql + " ORDER BY ts", tuple(args))

    severity, by_day, triggers = {}, {}, {}
    ack_latencies = []
    open_count = 0

    for a in rows:
        sev = a.get("severity") or "high"
        severity[sev] = severity.get(sev, 0) + 1
        day = (a.get("ts") or "")[:10]
        if day:
            d = by_day.setdefault(day, {"day": day, "total": 0, "critical": 0,
                                        "high": 0, "acknowledged": 0})
            d["total"] += 1
            d[sev] = d.get(sev, 0) + 1
            if a.get("status") == "acknowledged":
                d["acknowledged"] += 1

        detail = _loads(a.get("detail"), {})
        for label in (detail.get("objects") or []):
            triggers[label] = triggers.get(label, 0) + 1
        act = detail.get("action")
        if act:
            triggers[f"action:{act}"] = triggers.get(f"action:{act}", 0) + 1

        if a.get("status") == "acknowledged" and a.get("ack_at") and a.get("ts"):
            try:
                t0 = dt.datetime.fromisoformat(a["ts"])
                t1 = dt.datetime.fromisoformat(a["ack_at"])
                ack_latencies.append(max(0.0, (t1 - t0).total_seconds()))
            except Exception:
                pass
        else:
            open_count += 1

    ack_latencies.sort()
    total = len(rows)
    return {
        "total": total,
        "open": open_count,
        "acknowledged": total - open_count,
        "ack_rate": round(100 * (total - open_count) / total, 2) if total else 0.0,
        "severity": severity,
        "by_day": sorted(by_day.values(), key=lambda d: d["day"]),
        "top_triggers": sorted(({"label": k, "count": v} for k, v in triggers.items()),
                               key=lambda d: -d["count"])[:12],
        "ack_latency_seconds": {
            "median": round(ack_latencies[len(ack_latencies) // 2], 1) if ack_latencies else None,
            "max": round(ack_latencies[-1], 1) if ack_latencies else None,
            "n": len(ack_latencies),
        },
        "ssig_histogram": _histogram([a.get("ssig") or 0 for a in rows], bins=10),
    }


def timeline_analytics(footage_id=None, limit=400, profile=None):
    """Per-segment series in capture order — Ssig, sentiment, tier and motion.

    Returned as parallel arrays rather than objects: the frontend plots them
    directly, and this keeps the payload small for long recordings.
    """
    rows = _segment_rows(footage_id, motion_only=False, profile=profile)[:limit]
    return {
        "n": len(rows),
        "idx": [r.get("idx") for r in rows],
        "start_time": [r.get("start_time") for r in rows],
        "ssig": [round(r.get("ssig") or 0.0, 4) for r in rows],
        "sentiment": [round(r.get("sentiment_score") or 0.0, 4) for r in rows],
        "motion": [int(r.get("motion") or 0) for r in rows],
        "tier": [r.get("tier") or "" for r in rows],
        "action": [r.get("metadata") and _loads(r.get("metadata"), {}).get("action_label", "")
                   for r in rows],
        "stored_bytes": [r.get("stored_bytes") or 0 for r in rows],
        "original_bytes": [r.get("original_bytes") or 0 for r in rows],
    }
