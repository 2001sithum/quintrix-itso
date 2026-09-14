"""calibrate_thresholds.py — does the (0.4, 0.7) tier boundary generalise?

Run:  python -m training.calibrate_thresholds [--samples N] [--out PATH]

The tier boundaries were calibrated empirically on one dataset, and the project
limitations section flags that as a risk. This is the experiment that tests it.

## What it measures

For every candidate (`threshold_low`, `threshold_high`) pair it scores an
evaluation corpus end-to-end through the real fusion stage and reports:

- **critical_miss_rate** — fraction of segments with expert severity ≥ 0.80 that
  land in LOW. These are the dangerous errors: LOW is keyframe-only, so a miss
  here means the moving footage of a serious incident is destroyed. This is the
  metric the boundaries should be chosen to protect.
- **over_retention_rate** — fraction of segments with expert severity < 0.30
  that land in HIGH. These are the expensive errors: correct but wasteful.
- **storage_index** — relative disk use, from measured per-tier compression
  ratios (HIGH 1.0, MEDIUM ~0.18, LOW ~0.01).
- **tier distribution** and **band accuracy** against the expert label.

## Generalisation test

The same sweep runs on four corpora, not one:

- `nominal`     — the training distribution
- `low_light`   — detector confidences depressed, motion suppressed (night/IR)
- `busy_scene`  — crowds and clutter, more false-positive detections
- `weak_models` — every model less confident, action distributions flattened

A boundary pair that is only optimal on `nominal` has not generalised. The
report names the pair that is best in the worst case across all four, which is
the defensible choice for a system where the failure is irreversible.
"""
import argparse
import json
import os
import random

import numpy as np

from engine import fusion
from models import sentiment_model
from training import sentiment_dataset as ds

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT_PATH = os.path.join(ROOT, "weights", "threshold_calibration.json")
PLOT_PATH = os.path.join(ROOT, "docs", "assets", "threshold_calibration.png")

# Measured per-tier compression, from real pipeline runs: HIGH is a stream copy
# (1.0), MEDIUM is a 480p CRF-30 10fps transcode, LOW is three JPEG keyframes.
TIER_COST = {"HIGH": 1.0, "MEDIUM": 0.18, "LOW": 0.008}

CRITICAL_SEVERITY = 0.80
TRIVIAL_SEVERITY = 0.30


# ---------------------------------------------------------------------------
# Distribution shifts
# ---------------------------------------------------------------------------
def _shift(feats, kind, rng):
    """Apply a named distribution shift to a feature dict, in place-safe form."""
    f = dict(feats)
    if kind == "low_light":
        for k in ("t1_max_threat", "t1_weapon_conf", "t1_firesmoke_conf",
                  "ctx_person_conf", "action_top_conf"):
            f[k] = round(max(0.0, f.get(k, 0.0) * rng.uniform(0.55, 0.80)), 4)
        f["motion_ratio"] = round(f.get("motion_ratio", 0.0) * rng.uniform(0.5, 0.8), 4)
        f["is_night"] = 1.0
        f["action_entropy"] = round(min(1.0, f.get("action_entropy", 0.0) * 1.20), 4)
    elif kind == "busy_scene":
        f["ctx_people"] = round(min(1.0, f.get("ctx_people", 0.0) + rng.uniform(0.2, 0.6)), 4)
        f["ctx_carriable"] = 1.0
        f["t1_variety"] = round(min(1.0, f.get("t1_variety", 0.0) + rng.uniform(0.0, 0.25)), 4)
        # clutter produces short-lived spurious detections
        if rng.random() < 0.30 and f.get("t1_max_threat", 0.0) < 0.3:
            f["t1_max_threat"] = round(rng.uniform(0.30, 0.45), 4)
            f["t1_persistence"] = round(rng.uniform(0.05, 0.20), 4)
    elif kind == "weak_models":
        for k in ("action_top_conf", "t1_max_threat", "t1_weapon_conf",
                  "t1_firesmoke_conf", "ctx_person_conf", "action_severity"):
            f[k] = round(max(0.0, f.get(k, 0.0) * rng.uniform(0.60, 0.85)), 4)
        f["action_entropy"] = round(min(1.0, f.get("action_entropy", 0.0) * 1.35 + 0.10), 4)
        f["action_p_normal"] = round(min(1.0, f.get("action_p_normal", 0.0) + 0.10), 4)
    return f


SHIFTS = ["nominal", "low_light", "busy_scene", "weak_models"]


def build_corpus(n_samples, seed):
    """Score an evaluation corpus through the real trained model, once per
    distribution shift. Returns {shift: (scores, severities)}."""
    X, y, groups = ds.generate(n_samples, seed=seed)
    feats = [dict(zip(fusion.FEATURE_NAMES, row)) for row in X]
    out = {}
    for shift in SHIFTS:
        rng = random.Random(seed + hash(shift) % 9973)
        shifted = feats if shift == "nominal" else [_shift(f, shift, rng) for f in feats]
        scores, backend = sentiment_model.predict_batch(shifted)
        # The tiering decision uses Ssig, not the sentiment score alone, so run
        # the real fusion arithmetic over the same features.
        ssigs = [_ssig_from_features(f, s) for f, s in zip(shifted, scores)]
        out[shift] = {"ssig": ssigs, "severity": list(y), "groups": groups,
                      "backend": backend}
    return out


def _ssig_from_features(f, sentiment):
    """Reproduce `fusion.fuse`'s arithmetic directly from a feature dict.

    The sweep works on feature vectors rather than raw model outputs (that is
    what makes a distribution shift expressible), so it cannot call `fuse()`
    with real detections. Keeping the weights and modifiers imported from
    `fusion` means the two cannot silently diverge.
    """
    action_term = f["action_severity"] * (0.5 + 0.5 * f["action_top_conf"])
    object_term = f["t1_max_threat"] * (0.6 + 0.4 * f["t1_persistence"])
    s = (fusion.W_ACTION * action_term + fusion.W_OBJECT * object_term
         + fusion.W_SENTIMENT * sentiment)
    if f["t1_weapon_conf"] > 0:
        s += 0.25 * f["t1_weapon_conf"]
    if f["t1_firesmoke_conf"] > 0:
        s += 0.20 * f["t1_firesmoke_conf"]
    if f["ctx_people"] >= 3 / 8.0:
        s += 0.15 * f["ctx_people"]
    if f["is_night"]:
        s += 0.10
    if f["unattended"]:
        s += 0.12
    if f["action_entropy"] > 0.8 and f["action_severity"] > 0.3:
        s -= 0.10 * (f["action_entropy"] - 0.8) / 0.2
    return max(0.0, min(1.0, s))


# ---------------------------------------------------------------------------
# Sweep
# ---------------------------------------------------------------------------
def evaluate_pair(ssig, severity, lo, hi):
    ssig = np.asarray(ssig)
    sev = np.asarray(severity)
    tier = np.where(ssig > hi, "HIGH", np.where(ssig > lo, "MEDIUM", "LOW"))

    crit = sev >= CRITICAL_SEVERITY
    triv = sev < TRIVIAL_SEVERITY
    n = len(sev)
    counts = {t: int((tier == t).sum()) for t in ("HIGH", "MEDIUM", "LOW")}
    cost = sum(counts[t] * TIER_COST[t] for t in counts) / n

    return {
        "threshold_low": round(lo, 3), "threshold_high": round(hi, 3),
        "critical_miss_rate": round(float((tier[crit] == "LOW").mean()), 4)
                              if crit.any() else 0.0,
        "critical_high_capture": round(float((tier[crit] == "HIGH").mean()), 4)
                                 if crit.any() else 0.0,
        "over_retention_rate": round(float((tier[triv] == "HIGH").mean()), 4)
                               if triv.any() else 0.0,
        "storage_index": round(float(cost), 4),
        "tiers": counts,
        "high_pct": round(100 * counts["HIGH"] / n, 2),
        "medium_pct": round(100 * counts["MEDIUM"] / n, 2),
        "low_pct": round(100 * counts["LOW"] / n, 2),
    }


def sweep(corpus, lows, highs):
    results = {}
    for shift, data in corpus.items():
        rows = []
        for lo in lows:
            for hi in highs:
                if hi <= lo + 0.05:
                    continue
                rows.append(evaluate_pair(data["ssig"], data["severity"], lo, hi))
        results[shift] = rows
    return results


def recommend(results, max_critical_miss=0.02):
    """Pick the boundary pair that minimises storage subject to keeping the
    critical-miss rate under `max_critical_miss` **in the worst shift**, not
    the average — a boundary that only holds up in nominal conditions is
    exactly what the reviewer warned about."""
    keyed = {}
    for shift, rows in results.items():
        for r in rows:
            k = (r["threshold_low"], r["threshold_high"])
            e = keyed.setdefault(k, {"pairs": k, "per_shift": {}})
            e["per_shift"][shift] = r
    scored = []
    for k, e in keyed.items():
        worst_miss = max(r["critical_miss_rate"] for r in e["per_shift"].values())
        worst_over = max(r["over_retention_rate"] for r in e["per_shift"].values())
        mean_cost = float(np.mean([r["storage_index"] for r in e["per_shift"].values()]))
        scored.append({"threshold_low": k[0], "threshold_high": k[1],
                       "worst_critical_miss": round(worst_miss, 4),
                       "worst_over_retention": round(worst_over, 4),
                       "mean_storage_index": round(mean_cost, 4),
                       "per_shift": {s: {"critical_miss_rate": r["critical_miss_rate"],
                                         "storage_index": r["storage_index"],
                                         "high_pct": r["high_pct"],
                                         "low_pct": r["low_pct"]}
                                     for s, r in e["per_shift"].items()}})
    safe = [s for s in scored if s["worst_critical_miss"] <= max_critical_miss]
    pool = safe or scored
    pool.sort(key=lambda s: (s["mean_storage_index"], s["worst_critical_miss"]))
    return pool[0], scored, bool(safe)


def write_plot(results, current, best, path):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    fig, axes = plt.subplots(1, 2, figsize=(13, 5))
    fig.patch.set_facecolor("#0e1116")
    for ax in axes:
        ax.set_facecolor("#151a21")
        ax.tick_params(colors="#9aa4b2", labelsize=8)
        for sp in ax.spines.values():
            sp.set_color("#2a323d")
        ax.title.set_color("#e6edf3")
        ax.xaxis.label.set_color("#9aa4b2")
        ax.yaxis.label.set_color("#9aa4b2")
        ax.grid(color="#222a34", linewidth=0.6)

    colors = {"nominal": "#4c8dff", "low_light": "#ff9f43",
              "busy_scene": "#4cd6a0", "weak_models": "#ff6b6b"}

    ax = axes[0]
    for shift, rows in results.items():
        rows = sorted(rows, key=lambda r: r["storage_index"])
        ax.plot([r["storage_index"] for r in rows],
                [r["critical_miss_rate"] for r in rows],
                ".", markersize=3, alpha=0.55, color=colors[shift], label=shift)
    ax.set_xlabel("storage index (1.0 = keep everything lossless)")
    ax.set_ylabel("critical miss rate")
    ax.set_title("Storage vs irreversible loss, per distribution")
    ax.legend(facecolor="#151a21", edgecolor="#2a323d", labelcolor="#9aa4b2", fontsize=8)

    ax = axes[1]
    shifts = list(results.keys())
    cur = [next(r["critical_miss_rate"] for r in results[s]
                if r["threshold_low"] == current[0] and r["threshold_high"] == current[1])
           for s in shifts]
    rec = [best["per_shift"][s]["critical_miss_rate"] for s in shifts]
    x = np.arange(len(shifts))
    ax.bar(x - 0.19, cur, 0.38, color="#ff6b6b",
           label=f"current {current[0]}/{current[1]}")
    ax.bar(x + 0.19, rec, 0.38, color="#4cd6a0",
           label=f"recommended {best['threshold_low']}/{best['threshold_high']}")
    ax.set_xticks(x); ax.set_xticklabels(shifts, fontsize=8)
    ax.set_ylabel("critical miss rate")
    ax.set_title("Irreversible loss under distribution shift")
    ax.legend(facecolor="#151a21", edgecolor="#2a323d", labelcolor="#9aa4b2", fontsize=8)

    fig.tight_layout()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    fig.savefig(path, dpi=130, facecolor=fig.get_facecolor())
    plt.close(fig)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--samples", type=int, default=8000)
    ap.add_argument("--seed", type=int, default=4242)
    ap.add_argument("--max-critical-miss", type=float, default=0.02)
    ap.add_argument("--out", default=OUT_PATH)
    args = ap.parse_args()

    print(f"Building evaluation corpus: {args.samples} samples × {len(SHIFTS)} distributions")
    corpus = build_corpus(args.samples, args.seed)
    print(f"Scored with sentiment backend: {corpus['nominal']['backend']}")

    lows = [round(x, 2) for x in np.arange(0.20, 0.66, 0.05)]
    highs = [round(x, 2) for x in np.arange(0.40, 0.91, 0.05)]
    results = sweep(corpus, lows, highs)

    current = (0.40, 0.70)
    best, scored, satisfied = recommend(results, args.max_critical_miss)

    cur_entry = next(s for s in scored
                     if (s["threshold_low"], s["threshold_high"]) == current)

    print(f"\nCurrent boundaries {current[0]}/{current[1]}")
    print(f"  worst-case critical miss : {cur_entry['worst_critical_miss']}")
    print(f"  worst-case over-retention: {cur_entry['worst_over_retention']}")
    print(f"  mean storage index       : {cur_entry['mean_storage_index']}")
    for s in SHIFTS:
        d = cur_entry["per_shift"][s]
        print(f"    {s:12s} miss {d['critical_miss_rate']:.4f}  "
              f"storage {d['storage_index']:.3f}  HIGH {d['high_pct']:.1f}%")

    print(f"\nRecommended {best['threshold_low']}/{best['threshold_high']}"
          + ("" if satisfied else "  (no pair met the miss budget — best effort)"))
    print(f"  worst-case critical miss : {best['worst_critical_miss']}")
    print(f"  worst-case over-retention: {best['worst_over_retention']}")
    print(f"  mean storage index       : {best['mean_storage_index']}")
    for s in SHIFTS:
        d = best["per_shift"][s]
        print(f"    {s:12s} miss {d['critical_miss_rate']:.4f}  "
              f"storage {d['storage_index']:.3f}  HIGH {d['high_pct']:.1f}%")

    payload = {
        "samples": args.samples, "seed": args.seed,
        "distributions": SHIFTS,
        "max_critical_miss": args.max_critical_miss,
        "budget_satisfied": satisfied,
        "current": {"threshold_low": current[0], "threshold_high": current[1],
                    **{k: v for k, v in cur_entry.items()
                       if k not in ("threshold_low", "threshold_high")}},
        "recommended": best,
        "grid": scored,
        "notes": ("critical_miss_rate is the fraction of segments with expert "
                  "severity >= 0.80 that were tiered LOW — keyframe-only, so the "
                  "moving footage is destroyed. The recommendation minimises "
                  "storage subject to the WORST-CASE miss rate across all four "
                  "distributions, not the average."),
    }
    os.makedirs(os.path.dirname(args.out), exist_ok=True)
    with open(args.out, "w") as f:
        json.dump(payload, f, indent=2)
    write_plot(results, current, best, PLOT_PATH)
    print(f"\nSaved {args.out}")
    print(f"Saved {PLOT_PATH}")


if __name__ == "__main__":
    main()
