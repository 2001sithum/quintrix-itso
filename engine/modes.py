"""modes.py — five named operating modes for the ITSO pipeline.

Every knob in `config` is independently tunable, which is powerful and
unhelpful: an operator setting up a site does not want to reason about
`t1_conf` and `detection_stride_seconds` in isolation, they want to say
"this is a bank vault" or "this is a car park with a small disk".

These five modes are coherent *combinations* of those knobs, each with a stated
purpose and a stated cost. They are deliberately opinionated — the point is
that the trade-off is made explicitly and written down, rather than emerging by
accident from whatever the thresholds happened to be left at.

Applying a mode writes its keys into `config` and is audited. Editing any key
afterwards puts the system into `custom`, which is reported honestly rather
than pretending a mode is still in force.

The numbers here are grounded in the threshold-calibration sweep
(`training/calibrate_thresholds.py`); `expected` on each mode reports what that
sweep measured for its boundary pair, so the trade-offs are evidence-backed
rather than asserted. See docs/MODES.md.
"""
import json

from engine import storage_manager as sm

# Keys a mode owns. Anything outside this set is left alone when a mode is
# applied, so site-specific settings (credentials, paths) are never clobbered.
OWNED_KEYS = [
    "threshold_high", "threshold_low", "alert_threshold",
    "motion_sensitivity", "track1_conf_threshold", "track2_conf_threshold",
    "detection_stride_seconds", "action_min_frames", "segment_seconds",
    "tier3_grace_hours", "context_rules", "pipeline_profile",
]


def _ctx(**kw):
    base = {"night_boost": 0.10, "weapon_boost": 0.25, "crowd_boost": 0.15,
            "fire_boost": 0.20, "unattended_boost": 0.12}
    base.update(kw)
    return json.dumps(base)


MODES = {
    # ---------------------------------------------------------------- 1 ---
    "forensic": {
        "id": "forensic",
        "name": "Forensic Hold",
        "tagline": "Keep almost everything. Storage is the cheapest thing you own.",
        "icon": "🔒",
        "accent": "#ff3b5c",
        "purpose": (
            "Active investigation, legal hold, or a site under a retention "
            "order. Evidence must survive even if the scoring is wrong."),
        "use_when": [
            "An incident is under investigation and footage may be subpoenaed",
            "A new site whose normal behaviour the models have not yet seen",
            "Regulatory retention requirements override storage cost",
        ],
        "cost": (
            "Highest storage use by a wide margin — expect 3-5x the disk of "
            "Balanced. Deletion is effectively disabled for a week."),
        "config": {
            "threshold_high": "0.25", "threshold_low": "0.10",
            "alert_threshold": "0.60", "motion_sensitivity": "0.0005",
            "track1_conf_threshold": "0.25", "track2_conf_threshold": "0.30",
            "detection_stride_seconds": "0.5", "action_min_frames": "16",
            "segment_seconds": "15", "tier3_grace_hours": "168",
            "pipeline_profile": "fusion",
            "context_rules": _ctx(weapon_boost=0.30, unattended_boost=0.18),
        },
        "expected": {"critical_miss": "≈0%", "storage_index": "~0.85",
                     "compute": "2x Balanced"},
    },
    # ---------------------------------------------------------------- 2 ---
    "security": {
        "id": "security",
        "name": "High Security",
        "tagline": "Miss nothing that matters. Calibrated against model drift.",
        "icon": "🛡️",
        "accent": "#ff9f43",
        "purpose": (
            "Sites where failing to keep footage of a real incident is the "
            "dominant risk: banks, pharmacies, transport hubs, custody areas."),
        "use_when": [
            "A wrong LOW decision would destroy the only record of a crime",
            "Lighting or camera quality is variable, so model confidence drops",
            "You want the boundaries the calibration sweep recommends",
        ],
        "cost": (
            "Moderately more storage than Balanced, because ambiguous segments "
            "land in MEDIUM (re-encoded video kept) rather than LOW "
            "(keyframes only)."),
        # These are the boundaries training/calibrate_thresholds.py recommends:
        # they cut worst-case irreversible loss from 6.43% to 1.27% *and* use
        # less storage than 0.4/0.7, because widening MEDIUM is cheaper than
        # widening HIGH.
        "config": {
            "threshold_high": "0.90", "threshold_low": "0.30",
            "alert_threshold": "0.75", "motion_sensitivity": "0.0008",
            "track1_conf_threshold": "0.30", "track2_conf_threshold": "0.35",
            "detection_stride_seconds": "1.0", "action_min_frames": "16",
            "segment_seconds": "15", "tier3_grace_hours": "72",
            "pipeline_profile": "fusion",
            "context_rules": _ctx(weapon_boost=0.28, fire_boost=0.24),
        },
        "expected": {"critical_miss": "1.27% worst case",
                     "storage_index": "0.227", "compute": "baseline"},
    },
    # ---------------------------------------------------------------- 3 ---
    "balanced": {
        "id": "balanced",
        "name": "Balanced",
        "tagline": "The default. Sensible trade-off for a typical site.",
        "icon": "⚖️",
        "accent": "#7dd3fc",
        "purpose": (
            "General-purpose surveillance where both storage and evidence "
            "matter and neither dominates."),
        "use_when": [
            "Standard retail, office or residential monitoring",
            "You have no specific regulatory or incident-driven requirement",
            "A starting point before tuning to a site's real footage",
        ],
        "cost": (
            "Carries the known weakness the calibration sweep found: under "
            "degraded model confidence, 6.4% of genuinely critical segments "
            "are tiered LOW. Use High Security if that matters at your site."),
        "config": {
            "threshold_high": "0.70", "threshold_low": "0.40",
            "alert_threshold": "0.80", "motion_sensitivity": "0.001",
            "track1_conf_threshold": "0.35", "track2_conf_threshold": "0.35",
            "detection_stride_seconds": "1.0", "action_min_frames": "16",
            "segment_seconds": "15", "tier3_grace_hours": "24",
            "pipeline_profile": "fusion",
            "context_rules": _ctx(),
        },
        "expected": {"critical_miss": "6.43% worst case",
                     "storage_index": "0.269", "compute": "baseline"},
    },
    # ---------------------------------------------------------------- 4 ---
    "economy": {
        "id": "economy",
        "name": "Storage Economy",
        "tagline": "Maximum reduction. Accepts that some detail is lost.",
        "icon": "💾",
        "accent": "#26d07c",
        "purpose": (
            "Long-horizon archival on constrained storage, or low-risk areas "
            "where the recording exists for completeness rather than evidence."),
        "use_when": [
            "Corridors, stockrooms, car parks — high volume, low incident rate",
            "Disk or cloud spend is the binding constraint",
            "Footage older than a few days is rarely retrieved",
        ],
        "cost": (
            "Highest rate of irreversible loss of the five. Not appropriate "
            "anywhere an incident would need to be reconstructed from video."),
        "config": {
            "threshold_high": "0.80", "threshold_low": "0.55",
            "alert_threshold": "0.85", "motion_sensitivity": "0.004",
            "track1_conf_threshold": "0.45", "track2_conf_threshold": "0.50",
            "detection_stride_seconds": "2.0", "action_min_frames": "16",
            "segment_seconds": "30", "tier3_grace_hours": "6",
            "pipeline_profile": "fusion",
            "context_rules": _ctx(night_boost=0.06, crowd_boost=0.10),
        },
        "expected": {"critical_miss": "elevated — see docs/MODES.md",
                     "storage_index": "~0.10", "compute": "0.5x Balanced"},
    },
    # ---------------------------------------------------------------- 5 ---
    "edge": {
        "id": "edge",
        "name": "Edge / Low Power",
        "tagline": "Least compute. Leans hard on the motion gate.",
        "icon": "⚡",
        "accent": "#c084fc",
        "purpose": (
            "Constrained hardware — an on-camera box, a shared VM, or a site "
            "processing many streams on one machine."),
        "use_when": [
            "CPU, not disk, is the bottleneck",
            "Many cameras share one processing host",
            "Scenes are mostly static, so the motion gate can do the filtering",
        ],
        "cost": (
            "Coarser detector sampling means brief events can be missed "
            "entirely — a weapon visible for under two seconds may never be "
            "sampled. Raise Stage 2 sensitivity before lowering detector rate."),
        "config": {
            "threshold_high": "0.75", "threshold_low": "0.40",
            "alert_threshold": "0.80", "motion_sensitivity": "0.006",
            "track1_conf_threshold": "0.40", "track2_conf_threshold": "0.45",
            "detection_stride_seconds": "3.0", "action_min_frames": "16",
            "segment_seconds": "30", "tier3_grace_hours": "12",
            "pipeline_profile": "fusion",
            "context_rules": _ctx(),
        },
        "expected": {"critical_miss": "elevated for brief events",
                     "storage_index": "~0.15", "compute": "0.3x Balanced"},
    },
}

ORDER = ["forensic", "security", "balanced", "economy", "edge"]


def list_modes():
    """All modes, plus which one (if any) matches the live configuration."""
    current = detect_current()
    out = []
    for mid in ORDER:
        m = dict(MODES[mid])
        m["active"] = (mid == current["mode"])
        m["differences"] = current["differences"].get(mid, [])
        out.append(m)
    return {"modes": out, "current": current["mode"],
            "is_custom": current["mode"] == "custom",
            "owned_keys": OWNED_KEYS}


def _normalise(v):
    """Compare config values numerically where possible, so "0.70" and "0.7"
    are the same mode rather than a spurious custom."""
    try:
        return round(float(v), 6)
    except (TypeError, ValueError):
        if isinstance(v, str):
            try:
                return json.loads(v)          # context_rules
            except Exception:
                return v
        return v


def detect_current():
    """Which mode the live config corresponds to, or `custom`.

    Also returns, per mode, the specific keys that differ — so the UI can show
    *how far* the current setup is from each preset instead of a bare
    "custom".
    """
    cfg = sm.get_config()
    diffs = {}
    match = None
    for mid in ORDER:
        d = []
        for k, want in MODES[mid]["config"].items():
            have = cfg.get(k)
            if _normalise(have) != _normalise(want):
                d.append({"key": k, "current": have, "mode_value": want})
        diffs[mid] = d
        if not d and match is None:
            match = mid
    return {"mode": match or "custom", "differences": diffs}


def apply_mode(mode_id, user=""):
    """Write a mode's keys into config. Returns the keys that changed."""
    if mode_id not in MODES:
        raise ValueError(f"Unknown mode: {mode_id}")
    cfg = sm.get_config()
    changed = []
    for k, v in MODES[mode_id]["config"].items():
        if _normalise(cfg.get(k)) != _normalise(v):
            changed.append({"key": k, "from": cfg.get(k), "to": v})
        sm.set_config(k, v)
    sm.log("audit", f"Operating mode set to '{MODES[mode_id]['name']}'",
           user=user, context={"mode": mode_id,
                               "changed": [c["key"] for c in changed]})
    return {"mode": mode_id, "name": MODES[mode_id]["name"], "changed": changed}


def preview(mode_id):
    """What applying a mode would change, without changing it."""
    if mode_id not in MODES:
        raise ValueError(f"Unknown mode: {mode_id}")
    cfg = sm.get_config()
    return {"mode": mode_id,
            "changes": [{"key": k, "from": cfg.get(k), "to": v}
                        for k, v in MODES[mode_id]["config"].items()
                        if _normalise(cfg.get(k)) != _normalise(v)]}


# ---------------------------------------------------------------------------
# Why a mode scores the way it does
# ---------------------------------------------------------------------------
# Measured per-tier compression from real pipeline runs: HIGH is a stream copy,
# MEDIUM a 480p CRF-30 10fps transcode, LOW three JPEG keyframes.
TIER_COST = {"HIGH": 1.0, "MEDIUM": 0.18, "LOW": 0.008}

# Plain-language effect of each knob a mode owns. The UI shows these next to
# the mode's value so an operator can see *why* a preset behaves as it does,
# rather than just what numbers it sets.
KNOB_EFFECTS = {
    "threshold_high": ("Tier HIGH above", "Segments above this keep their original "
                       "video losslessly. Lower it and more footage is preserved "
                       "at full quality — and at full size."),
    "threshold_low": ("Tier LOW below", "Segments below this keep keyframes only; "
                      "their video is destroyed. This is the irreversible boundary, "
                      "so lowering it is the single most effective way to reduce "
                      "accidental loss."),
    "alert_threshold": ("Raise an alert at", "Significance at which an operator is "
                        "notified. Independent of storage — it changes attention, "
                        "not retention."),
    "motion_sensitivity": ("Motion gate", "Fraction of the frame that must change "
                           "for a segment to reach the models at all. Raising it "
                           "skips more segments, which is the cheapest saving "
                           "available — nothing is analysed and nothing is stored."),
    "track1_conf_threshold": ("Threat detector confidence", "Minimum confidence for "
                              "a suspicious-object detection to count. Lower catches "
                              "more real threats and more false positives; the "
                              "fusion stage damps the latter using persistence."),
    "track2_conf_threshold": ("Context detector confidence", "Minimum confidence for "
                              "context objects. Mostly affects crowd counting and "
                              "unattended-object reasoning."),
    "detection_stride_seconds": ("Detector sampling", "Seconds between frames sent "
                                 "to the detectors. This is the dominant compute "
                                 "cost. At a 3s stride a weapon visible for under "
                                 "two seconds may never be sampled."),
    "action_min_frames": ("Action clip length", "Frames required before action "
                          "recognition runs at all."),
    "segment_seconds": ("Segment length", "Longer segments mean fewer model runs "
                        "but coarser localisation — an event is pinned to a wider "
                        "window, and one significant moment promotes the whole "
                        "segment."),
    "tier3_grace_hours": ("Degradation grace", "How long LOW-tier video is held "
                          "before it is destroyed. Pure safety margin: it costs "
                          "disk and buys reversibility."),
    "context_rules": ("Context modifiers", "Additive boosts applied after the three "
                      "weighted channels — weapon, fire, crowd, night and "
                      "unattended-object."),
    "pipeline_profile": ("Pipeline", "Which model set runs. `fusion` is the "
                         "deployed pipeline; `legacy` is kept for comparison."),
}


def rationale(mode_id, sample_limit=4000):
    """Explain a mode against the system's own scored segments.

    Rather than asserting that a mode is "aggressive", this replays every real
    Ssig the pipeline has produced through that mode's tier boundaries and
    reports what would actually happen: the tier split, an estimated storage
    index from measured per-tier compression, and how many segments would move
    relative to the live configuration.

    Ssig itself is not recomputed — the boundaries are what a mode changes for
    already-scored footage. Detector and motion settings change *future*
    scoring, which is why they are explained qualitatively rather than
    simulated.
    """
    if mode_id not in MODES:
        raise ValueError(f"Unknown mode: {mode_id}")
    m = MODES[mode_id]
    lo, hi = float(m["config"]["threshold_low"]), float(m["config"]["threshold_high"])

    rows = sm.q("SELECT ssig, tier, original_bytes FROM segments "
                "WHERE motion=1 AND tier!='' LIMIT ?", (sample_limit,))
    cfg = sm.get_config()
    cur_lo = float(cfg.get("threshold_low", 0.4))
    cur_hi = float(cfg.get("threshold_high", 0.7))

    def tier_of(v, a, b):
        return "HIGH" if v > b else "MEDIUM" if v > a else "LOW"

    counts = {"HIGH": 0, "MEDIUM": 0, "LOW": 0}
    cur_counts = {"HIGH": 0, "MEDIUM": 0, "LOW": 0}
    moved_up = moved_down = 0
    newly_destroyed = []
    for r in rows:
        v = r["ssig"] or 0.0
        t = tier_of(v, lo, hi)
        c = tier_of(v, cur_lo, cur_hi)
        counts[t] += 1
        cur_counts[c] += 1
        rank = {"LOW": 0, "MEDIUM": 1, "HIGH": 2}
        if rank[t] > rank[c]:
            moved_up += 1
        elif rank[t] < rank[c]:
            moved_down += 1
            if t == "LOW" and c != "LOW":
                newly_destroyed.append(r["original_bytes"] or 0)

    n = len(rows) or 1
    index = sum(counts[t] * TIER_COST[t] for t in counts) / n
    cur_index = sum(cur_counts[t] * TIER_COST[t] for t in cur_counts) / n

    knobs = []
    for k, v in m["config"].items():
        label, why = KNOB_EFFECTS.get(k, (k, ""))
        knobs.append({"key": k, "label": label, "value": v, "why": why,
                      "current": cfg.get(k),
                      "differs": _normalise(cfg.get(k)) != _normalise(v)})

    return {
        "mode": mode_id, "name": m["name"],
        "sample_size": len(rows),
        "thresholds": {"low": lo, "high": hi},
        "current_thresholds": {"low": cur_lo, "high": cur_hi},
        "tiers": counts, "current_tiers": cur_counts,
        "storage_index": round(index, 4),
        "current_storage_index": round(cur_index, 4),
        "storage_delta_percent": round((index - cur_index) / cur_index * 100, 1)
                                 if cur_index else 0.0,
        "moved_up": moved_up, "moved_down": moved_down,
        "newly_destroyed_segments": len(newly_destroyed),
        "newly_destroyed_bytes": sum(newly_destroyed),
        "knobs": knobs,
        "note": ("Replayed over the Ssig values this system has actually produced. "
                 "Threshold changes are simulated exactly; detector and motion "
                 "settings change future scoring and are described rather than "
                 "simulated."),
    }
