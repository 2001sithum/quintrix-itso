"""train_sentiment.py — trains the ITSO sentiment model.

Run:  python -m training.train_sentiment [--samples N] [--epochs N] [--seed N]

Trains a small MLP regressor that maps the 16-D fusion feature vector to a
0..1 sentiment/threat score, replacing the hand-written arithmetic that used to
produce it. Writes four artefacts:

    weights/sentiment_mlp.pt        checkpoint + the feature contract + metrics
    weights/sentiment_metrics.json  held-out metrics, readable by the API
    docs/assets/sentiment_training.png   training curves and error analysis
    docs/MODEL_CARD_sentiment.md    what it was trained on and where it fails

Everything is measured on a **held-out test split the model never saw**, and
the splits are scenario-stratified so a rare scenario cannot land entirely in
one split and flatter the numbers.
"""
import argparse
import json
import math
import os
import random
import datetime as dt

import numpy as np
import torch
import torch.nn as nn

from engine import fusion
from training import sentiment_dataset as ds

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WEIGHTS_DIR = os.path.join(ROOT, "weights")
ASSETS_DIR = os.path.join(ROOT, "docs", "assets")
CKPT_PATH = os.path.join(WEIGHTS_DIR, "sentiment_mlp.pt")
METRICS_PATH = os.path.join(WEIGHTS_DIR, "sentiment_metrics.json")

HIDDEN = (64, 32)
TIER_LOW, TIER_HIGH = 0.4, 0.7


class SentimentNet(nn.Module):
    """16 → 64 → 32 → 1 with a sigmoid head.

    Deliberately small. The input is 16 dense, already-semantic features and
    the corpus is 12k rows — a larger network memorises the scenario centres
    instead of learning the smooth ranking that makes this worth doing at all.
    Dropout is kept low for the same reason.
    """

    def __init__(self, n_in=fusion.N_FEATURES, hidden=HIDDEN, p_drop=0.10):
        super().__init__()
        layers, prev = [], n_in
        for h in hidden:
            layers += [nn.Linear(prev, h), nn.ReLU(), nn.Dropout(p_drop)]
            prev = h
        layers += [nn.Linear(prev, 1), nn.Sigmoid()]
        self.net = nn.Sequential(*layers)

    def forward(self, x):
        return self.net(x).squeeze(-1)


# ---------------------------------------------------------------------------
# Metrics
# ---------------------------------------------------------------------------
def _tier(v):
    return "HIGH" if v > TIER_HIGH else "MEDIUM" if v > TIER_LOW else "LOW"


def _spearman(a, b):
    """Rank correlation without a scipy dependency at inference time. Ranking
    matters more than absolute error here: the tiering only cares whether
    segment A is more significant than segment B."""
    def rank(v):
        order = sorted(range(len(v)), key=lambda i: v[i])
        r = [0.0] * len(v)
        i = 0
        while i < len(order):
            j = i
            while j + 1 < len(order) and v[order[j + 1]] == v[order[i]]:
                j += 1
            avg = (i + j) / 2.0 + 1
            for k in range(i, j + 1):
                r[order[k]] = avg
            i = j + 1
        return r
    ra, rb = rank(list(a)), rank(list(b))
    ma, mb = sum(ra) / len(ra), sum(rb) / len(rb)
    num = sum((x - ma) * (y - mb) for x, y in zip(ra, rb))
    den = math.sqrt(sum((x - ma) ** 2 for x in ra) * sum((y - mb) ** 2 for y in rb))
    return num / den if den else 0.0


def evaluate(model, X, y, groups):
    model.eval()
    with torch.no_grad():
        pred = model(torch.tensor(X, dtype=torch.float32)).numpy()
    err = pred - np.asarray(y)
    mae = float(np.mean(np.abs(err)))
    rmse = float(np.sqrt(np.mean(err ** 2)))
    ss_res = float(np.sum(err ** 2))
    ss_tot = float(np.sum((np.asarray(y) - np.mean(y)) ** 2))
    r2 = 1 - ss_res / ss_tot if ss_tot else 0.0
    tier_hits = sum(1 for p, t in zip(pred, y) if _tier(p) == _tier(t))

    per_scenario = {}
    for gi, name in enumerate(ds.SCENARIO_NAMES):
        idx = [i for i, g in enumerate(groups) if g == gi]
        if idx:
            per_scenario[name] = {
                "n": len(idx),
                "mae": round(float(np.mean(np.abs(err[idx]))), 4),
                "mean_pred": round(float(np.mean(pred[idx])), 4),
                "mean_true": round(float(np.mean(np.asarray(y)[idx])), 4),
            }

    return {
        "n": len(y),
        "mae": round(mae, 4),
        "rmse": round(rmse, 4),
        "r2": round(r2, 4),
        "spearman": round(_spearman(pred, y), 4),
        "tier_accuracy": round(tier_hits / len(y), 4),
        "within_0.10": round(float(np.mean(np.abs(err) <= 0.10)), 4),
        "per_scenario": per_scenario,
    }, pred


# ---------------------------------------------------------------------------
# Split
# ---------------------------------------------------------------------------
def stratified_split(groups, seed, fracs=(0.70, 0.15, 0.15)):
    """Scenario-stratified index split, so every scenario appears in train,
    val and test in roughly its corpus proportion."""
    rng = random.Random(seed)
    buckets = {}
    for i, g in enumerate(groups):
        buckets.setdefault(g, []).append(i)
    tr, va, te = [], [], []
    for g, idx in buckets.items():
        rng.shuffle(idx)
        n = len(idx)
        a = int(n * fracs[0])
        b = a + int(n * fracs[1])
        tr += idx[:a]; va += idx[a:b]; te += idx[b:]
    rng.shuffle(tr); rng.shuffle(va); rng.shuffle(te)
    return tr, va, te


# ---------------------------------------------------------------------------
# Plots
# ---------------------------------------------------------------------------
def write_plots(history, test_pred, test_y, test_metrics, path):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    fig, axes = plt.subplots(2, 2, figsize=(13, 9))
    fig.patch.set_facecolor("#0e1116")
    for ax in axes.ravel():
        ax.set_facecolor("#151a21")
        ax.tick_params(colors="#9aa4b2", labelsize=8)
        for sp in ax.spines.values():
            sp.set_color("#2a323d")
        ax.title.set_color("#e6edf3")
        ax.xaxis.label.set_color("#9aa4b2")
        ax.yaxis.label.set_color("#9aa4b2")
        ax.grid(color="#222a34", linewidth=0.6)

    ax = axes[0][0]
    ax.plot(history["train"], color="#4c8dff", label="train")
    ax.plot(history["val"], color="#ff6b6b", label="val")
    ax.set_title("Loss (MSE)"); ax.set_xlabel("epoch")
    ax.legend(facecolor="#151a21", edgecolor="#2a323d", labelcolor="#9aa4b2", fontsize=8)

    ax = axes[0][1]
    ax.scatter(test_y, test_pred, s=6, alpha=0.35, color="#4cd6a0", edgecolors="none")
    ax.plot([0, 1], [0, 1], color="#ff9f43", linewidth=1, linestyle="--")
    for t in (TIER_LOW, TIER_HIGH):
        ax.axhline(t, color="#3b4455", linewidth=0.6, alpha=0.5)
        ax.axvline(t, color="#3b4455", linewidth=0.6, alpha=0.5)
    ax.set_title("Predicted vs expert label (held-out test)")
    ax.set_xlabel("expert severity"); ax.set_ylabel("predicted")

    ax = axes[1][0]
    names = list(test_metrics["per_scenario"].keys())
    maes = [test_metrics["per_scenario"][n]["mae"] for n in names]
    order = sorted(range(len(names)), key=lambda i: maes[i])
    ax.barh([names[i] for i in order], [maes[i] for i in order], color="#4c8dff")
    ax.set_title("Per-scenario MAE (held-out test)")
    ax.tick_params(axis="y", labelsize=7)

    ax = axes[1][1]
    bins = np.linspace(0, 1, 11)
    idx = np.digitize(test_y, bins) - 1
    xs, ys = [], []
    for b in range(10):
        m = idx == b
        if m.sum() >= 3:
            xs.append(float(np.mean(np.asarray(test_y)[m])))
            ys.append(float(np.mean(test_pred[m])))
    ax.plot([0, 1], [0, 1], color="#ff9f43", linestyle="--", linewidth=1)
    ax.plot(xs, ys, marker="o", color="#4cd6a0", linewidth=1.5, markersize=4)
    ax.set_title("Calibration"); ax.set_xlabel("mean expert label"); ax.set_ylabel("mean prediction")

    fig.tight_layout()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    fig.savefig(path, dpi=130, facecolor=fig.get_facecolor())
    plt.close(fig)


MODEL_CARD = """# Model card — ITSO sentiment model

*Generated by `training/train_sentiment.py` on {ts}. Do not edit by hand.*

## What it does

Maps the 16-dimensional fusion feature vector for one 15-second CCTV segment
to a **sentiment/threat score in 0..1**, which `engine/fusion.py` consumes as
one of the three weighted channels of the significance score (Ssig).

## Architecture

`{arch}` — {params:,} parameters. Sigmoid output, MSE loss, Adam, early
stopping on validation loss.

## Training data

{n_total:,} samples generated by `training/sentiment_dataset.py`:
**scenario-level expert labelling with distributional sampling** over
{n_scen} surveillance scenarios. Each scenario carries a severity assigned from
its harm profile and a generator that samples plausible model outputs for it,
including weak detections, one-frame false positives, and undecided action
predictions. Labels carry annotator-style Gaussian noise.

**This is not a human-annotated video corpus.** No public dataset maps this
pipeline's specific feature space to threat scores, so the model is best
described as a *calibrated distillation of an expert severity rubric over
realistic model-output distributions*. It generalises smoothly between
scenarios and learns feature interactions the flat rubric could not express,
and it is measurable — but its ceiling is the quality of the rubric, and the
scenario severities should be re-validated against real annotated footage
before any safety-critical deployment.

Splits are scenario-stratified: {n_train:,} train / {n_val:,} val / {n_test:,} test.

## Held-out test metrics

| Metric | Value |
|---|---|
| MAE | {mae} |
| RMSE | {rmse} |
| R² | {r2} |
| Spearman ρ | {spearman} |
| Tier-band accuracy (0.4 / 0.7) | {tier_acc} |
| Within ±0.10 of label | {within} |

Per-scenario error and calibration: `docs/assets/sentiment_training.png`.

## Known limitations

- Severity bands are expert-assigned, not crowd-validated; systematic bias in
  the rubric becomes systematic bias in the model.
- Trained on synthesised feature distributions. If a deployed detector's
  confidence distribution differs materially from the sampled one, scores
  will be miscalibrated — retrain with `--samples` raised and the scenario
  generators adjusted to the observed distribution.
- The model sees no pixels. It cannot distinguish a real knife from a printed
  picture of one; that is Track 1's job, and its errors propagate here.
- Feature order is a hard contract. The checkpoint stores its own
  `feature_names`; `models/sentiment_model.py` refuses to load a checkpoint
  whose list disagrees with `engine/fusion.FEATURE_NAMES`.
"""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--samples", type=int, default=12000)
    ap.add_argument("--epochs", type=int, default=300)
    ap.add_argument("--batch", type=int, default=128)
    ap.add_argument("--lr", type=float, default=2e-3)
    ap.add_argument("--patience", type=int, default=30)
    ap.add_argument("--seed", type=int, default=17)
    args = ap.parse_args()

    torch.manual_seed(args.seed)
    np.random.seed(args.seed)
    random.seed(args.seed)

    print(f"Generating corpus: {args.samples} samples, "
          f"{len(ds.SCENARIOS)} scenarios, {fusion.N_FEATURES} features")
    X, y, groups = ds.generate(args.samples, seed=args.seed)
    X = np.asarray(X, dtype=np.float32)
    y = np.asarray(y, dtype=np.float32)

    tr, va, te = stratified_split(groups, args.seed)
    print(f"Split: {len(tr)} train / {len(va)} val / {len(te)} test")

    Xtr = torch.tensor(X[tr]); ytr = torch.tensor(y[tr])
    Xva = torch.tensor(X[va]); yva = torch.tensor(y[va])

    model = SentimentNet()
    n_params = sum(p.numel() for p in model.parameters())
    opt = torch.optim.Adam(model.parameters(), lr=args.lr, weight_decay=1e-5)
    sched = torch.optim.lr_scheduler.ReduceLROnPlateau(opt, factor=0.5, patience=10)
    lossf = nn.MSELoss()

    history = {"train": [], "val": []}
    best_val, best_state, since_best = float("inf"), None, 0

    for epoch in range(args.epochs):
        model.train()
        perm = torch.randperm(len(Xtr))
        running = 0.0
        for i in range(0, len(perm), args.batch):
            b = perm[i:i + args.batch]
            opt.zero_grad()
            loss = lossf(model(Xtr[b]), ytr[b])
            loss.backward()
            opt.step()
            running += loss.item() * len(b)
        train_loss = running / len(Xtr)

        model.eval()
        with torch.no_grad():
            val_loss = lossf(model(Xva), yva).item()
        sched.step(val_loss)
        history["train"].append(train_loss)
        history["val"].append(val_loss)

        if val_loss < best_val - 1e-5:
            best_val, since_best = val_loss, 0
            best_state = {k: v.clone() for k, v in model.state_dict().items()}
        else:
            since_best += 1
            if since_best >= args.patience:
                print(f"Early stop at epoch {epoch} (best val {best_val:.5f})")
                break
        if epoch % 20 == 0:
            print(f"  epoch {epoch:3d}  train {train_loss:.5f}  val {val_loss:.5f}")

    if best_state:
        model.load_state_dict(best_state)

    val_metrics, _ = evaluate(model, X[va], y[va], [groups[i] for i in va])
    test_metrics, test_pred = evaluate(model, X[te], y[te], [groups[i] for i in te])

    print("\nHeld-out test metrics")
    for k in ("mae", "rmse", "r2", "spearman", "tier_accuracy", "within_0.10"):
        print(f"  {k:16s} {test_metrics[k]}")

    ts = dt.datetime.now().isoformat(timespec="seconds")
    arch = f"MLP {fusion.N_FEATURES}→" + "→".join(str(h) for h in HIDDEN) + "→1"

    os.makedirs(WEIGHTS_DIR, exist_ok=True)
    torch.save({
        "state_dict": model.state_dict(),
        "feature_names": fusion.FEATURE_NAMES,   # the training/inference contract
        "hidden": list(HIDDEN),
        "arch": arch,
        "trained_at": ts,
        "corpus": {"samples": args.samples, "scenarios": ds.SCENARIO_NAMES,
                   "seed": args.seed},
        "metrics": {"val": val_metrics, "test": test_metrics},
    }, CKPT_PATH)

    with open(METRICS_PATH, "w") as f:
        json.dump({"trained_at": ts, "arch": arch, "params": n_params,
                   "corpus_samples": args.samples,
                   "splits": {"train": len(tr), "val": len(va), "test": len(te)},
                   "val": val_metrics, "test": test_metrics,
                   "history": {"train": history["train"][-120:],
                               "val": history["val"][-120:]}}, f, indent=2)

    write_plots(history, test_pred, y[te], test_metrics,
                os.path.join(ASSETS_DIR, "sentiment_training.png"))

    with open(os.path.join(ROOT, "docs", "MODEL_CARD_sentiment.md"), "w") as f:
        f.write(MODEL_CARD.format(
            ts=ts, arch=arch, params=n_params, n_total=args.samples,
            n_scen=len(ds.SCENARIOS), n_train=len(tr), n_val=len(va), n_test=len(te),
            mae=test_metrics["mae"], rmse=test_metrics["rmse"], r2=test_metrics["r2"],
            spearman=test_metrics["spearman"],
            tier_acc=test_metrics["tier_accuracy"],
            within=test_metrics["within_0.10"]))

    print(f"\nSaved {CKPT_PATH}")
    print(f"Saved {METRICS_PATH}")
    print(f"Saved docs/assets/sentiment_training.png")
    print(f"Saved docs/MODEL_CARD_sentiment.md")


if __name__ == "__main__":
    main()
