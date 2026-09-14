"""run_all.py — measured evidence for the stated deployment targets.

Run:  python -m benchmarks.run_all [--projects 100] [--availability-seconds 60]

The project states three deployment targets — 99.5% availability, 100
concurrent projects, and cross-platform support on Linux/macOS/Windows — as
goals. They were not validated by experiment. This module runs the experiments
and writes `benchmarks/results.json`, which `GET /api/validation` serves and
docs/VALIDATION.md reports.

Three experiments:

1. **Availability** — sustained concurrent request load against the live app
   for a fixed window; reports success rate, latency percentiles, and the
   error budget implied by the observed rate.

2. **Concurrency** — N projects submitted at once through the real upload
   endpoint, each running the real pipeline on a background thread. Reports
   how many completed, how many were lost, and throughput. This is the target
   that actually stresses the design: every upload spawns a thread, so the
   question is whether the system degrades gracefully or drops work.

3. **Platform** — records the host this run happened on. A single machine
   cannot prove cross-platform support, so this only contributes one row; the
   Linux and Windows rows come from the CI matrix in
   `.github/workflows/ci-cd.yml`, which runs the same suite on all three and
   uploads its results. `docs/VALIDATION.md` states plainly which rows are
   measured and which are still outstanding.
"""
import argparse
import concurrent.futures as cf
import json
import os
import platform
import shutil
import statistics
import subprocess
import sys
import tempfile
import time
import uuid

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "benchmarks", "results.json")


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------
def make_clip(path, seconds=2, size="160x120", fps=12):
    """A tiny synthetic clip with real motion. Small on purpose: the
    concurrency experiment is about scheduling and thread behaviour, not about
    how long one YOLO forward pass takes."""
    subprocess.run([
        "ffmpeg", "-y", "-f", "lavfi",
        "-i", f"color=c=#101820:s={size}:d={seconds}:r={fps}",
        "-f", "lavfi", "-i", f"color=c=#e0e0e0:s=24x24:d={seconds}:r={fps}",
        "-filter_complex", f"[0][1]overlay=x='(t/{seconds})*130':y=48",
        "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
        "-g", str(fps), path,
    ], capture_output=True, check=True)
    return path


# ---------------------------------------------------------------------------
# Experiment 1 — availability
# ---------------------------------------------------------------------------
def experiment_availability(client, headers, seconds=60, workers=16):
    """Sustained mixed read load. Availability is measured as the fraction of
    requests answered with a non-5xx status inside the window."""
    paths = ["/api/health", "/api/analytics", "/api/projects", "/api/models",
             "/api/status", "/api/storage", "/api/savings/projection?months=12"]
    deadline = time.time() + seconds
    latencies, ok, failed, errors = [], 0, 0, {}

    def worker(i):
        nonlocal ok, failed
        local = []
        n = 0
        while time.time() < deadline:
            p = paths[n % len(paths)]
            n += 1
            t0 = time.perf_counter()
            try:
                r = client.get(p, headers=headers)
                dt = (time.perf_counter() - t0) * 1000
                local.append(dt)
                if r.status_code < 500:
                    ok += 1
                else:
                    failed += 1
                    errors[str(r.status_code)] = errors.get(str(r.status_code), 0) + 1
            except Exception as e:                          # pragma: no cover
                failed += 1
                k = type(e).__name__
                errors[k] = errors.get(k, 0) + 1
        return local

    t0 = time.time()
    with cf.ThreadPoolExecutor(max_workers=workers) as ex:
        for chunk in ex.map(worker, range(workers)):
            latencies.extend(chunk)
    elapsed = time.time() - t0

    total = ok + failed
    latencies.sort()

    def pct(p):
        if not latencies:
            return 0.0
        return round(latencies[min(len(latencies) - 1, int(len(latencies) * p))], 2)

    availability = (ok / total * 100) if total else 0.0
    return {
        "target": "99.5% availability",
        "window_seconds": round(elapsed, 1),
        "concurrent_workers": workers,
        "requests": total,
        "succeeded": ok,
        "failed": failed,
        "availability_percent": round(availability, 4),
        "meets_target": availability >= 99.5,
        "throughput_rps": round(total / elapsed, 1) if elapsed else 0,
        "latency_ms": {"p50": pct(0.50), "p90": pct(0.90),
                       "p99": pct(0.99), "max": round(latencies[-1], 2) if latencies else 0},
        "errors": errors,
        "note": ("Measured against the in-process ASGI app under concurrent "
                 "read load. This exercises application availability, not "
                 "infrastructure availability — network, host and restart "
                 "behaviour are out of scope for this experiment."),
    }


# ---------------------------------------------------------------------------
# Experiment 2 — concurrent projects
# ---------------------------------------------------------------------------
def experiment_concurrency(client, headers, clip, n_projects=100, timeout=900):
    """Submit `n_projects` uploads simultaneously and wait for every one to
    reach a terminal state. The pass condition is that none are *lost*: every
    accepted upload must end as done or error, never stuck in processing."""
    submit_errors = {}
    ids = []

    def submit(i):
        try:
            with open(clip, "rb") as f:
                r = client.post("/api/projects", headers=headers,
                                files={"file": (f"load_{i}.mp4", f, "video/mp4")})
            if r.status_code == 200:
                return r.json()["project_id"]  # accepted; may be queued
            submit_errors[str(r.status_code)] = submit_errors.get(str(r.status_code), 0) + 1
        except Exception as e:                              # pragma: no cover
            k = type(e).__name__
            submit_errors[k] = submit_errors.get(k, 0) + 1
        return None

    t0 = time.time()
    with cf.ThreadPoolExecutor(max_workers=min(32, n_projects)) as ex:
        ids = [pid for pid in ex.map(submit, range(n_projects)) if pid]
    submit_elapsed = time.time() - t0

    from engine import storage_manager as sm
    from engine import jobs
    done, errored, peak_queue = 0, 0, 0
    deadline = time.time() + timeout
    placeholders = ",".join("?" * len(ids))
    while time.time() < deadline:
        rows = sm.q(f"SELECT status, COUNT(*) n FROM projects WHERE id IN "
                    f"({placeholders}) GROUP BY status", tuple(ids))
        counts = {r["status"]: r["n"] for r in rows}
        done = counts.get("done", 0)
        errored = counts.get("error", 0)
        peak_queue = max(peak_queue, counts.get("queued", 0))
        if done + errored >= len(ids):
            break
        time.sleep(1)
    elapsed = time.time() - t0

    # Anything neither done nor errored is work the system accepted and then
    # failed to deliver — the failure mode this experiment exists to catch.
    stuck = len(ids) - done - errored
    segments = sm.q("SELECT COUNT(*) n FROM segments WHERE project_id IN (%s)"
                    % ",".join("?" * len(ids)), tuple(ids), one=True)["n"] if ids else 0

    return {
        "target": f"{n_projects} concurrent projects",
        "requested": n_projects,
        "accepted": len(ids),
        "completed": done,
        "errored": errored,
        "stuck_in_processing": stuck,
        "segments_produced": segments,
        "worker_pool_size": jobs.max_workers(),
        "peak_queue_depth": peak_queue,
        "submit_seconds": round(submit_elapsed, 2),
        "total_seconds": round(elapsed, 1),
        "throughput_projects_per_min": round(done / (elapsed / 60), 1) if elapsed else 0,
        "submit_errors": submit_errors,
        # "No work lost" is the property that matters: a queue that drops
        # uploads under load is far worse than one that is merely slow.
        "meets_target": len(ids) == n_projects and stuck == 0,
        "note": ("Uploads are accepted immediately and queued onto a bounded "
                 "worker pool. Clips are deliberately small so the experiment "
                 "measures scheduling and completion under simultaneous load "
                 "rather than single-inference latency. The pass condition is "
                 "that no accepted work is lost: every upload must reach done "
                 "or error, never remain stuck."),
    }


# ---------------------------------------------------------------------------
# Experiment 3 — platform
# ---------------------------------------------------------------------------
def experiment_platform():
    from engine import itso_engine
    try:
        import torch
        torch_v = torch.__version__
    except Exception:                                       # pragma: no cover
        torch_v = "unavailable"
    return {
        "target": "cross-platform: Linux, macOS, Windows",
        "os": platform.system(),
        "release": platform.release(),
        "machine": platform.machine(),
        "python": platform.python_version(),
        "torch": torch_v,
        "device_selected": itso_engine.select_device(),
        "ffmpeg": bool(shutil.which("ffmpeg")),
        "ci": bool(os.environ.get("CI")),
        "note": ("One host produces one row. The Linux and Windows rows come "
                 "from the CI matrix, which runs this same module on "
                 "ubuntu-latest and windows-latest and uploads its results."),
    }


# ---------------------------------------------------------------------------
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--projects", type=int, default=100)
    ap.add_argument("--availability-seconds", type=int, default=45)
    ap.add_argument("--workers", type=int, default=16)
    ap.add_argument("--skip-concurrency", action="store_true")
    ap.add_argument("--out", default=OUT)
    args = ap.parse_args()

    if not shutil.which("ffmpeg"):
        print("ffmpeg is required to generate benchmark clips", file=sys.stderr)
        return 1

    # Always run against a throwaway DB and archive: the concurrency
    # experiment creates hundreds of rows and files.
    work = tempfile.mkdtemp(prefix="itso-bench-")
    os.environ["QUINTRIX_DB"] = os.path.join(work, "bench.db")
    os.environ["QUINTRIX_ARCHIVE"] = os.path.join(work, "archive")

    from fastapi.testclient import TestClient
    import server

    client = TestClient(server.app)
    headers = {"x-session": client.post(
        "/api/login", data={"username": "admin", "password": "admin123"}
    ).json()["token"]}

    results = {"started_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
               "platform": experiment_platform()}

    print(f"Platform: {results['platform']['os']} "
          f"{results['platform']['machine']} · python "
          f"{results['platform']['python']} · device "
          f"{results['platform']['device_selected']}")

    print(f"\n[1/2] Availability — {args.availability_seconds}s @ {args.workers} workers")
    results["availability"] = experiment_availability(
        client, headers, args.availability_seconds, args.workers)
    a = results["availability"]
    print(f"  {a['requests']} requests · {a['availability_percent']}% available "
          f"· p50 {a['latency_ms']['p50']}ms p99 {a['latency_ms']['p99']}ms "
          f"· {'PASS' if a['meets_target'] else 'FAIL'}")

    if args.skip_concurrency:
        results["concurrency"] = {"skipped": True}
    else:
        print(f"\n[2/2] Concurrency — {args.projects} simultaneous projects")
        clip = make_clip(os.path.join(work, "clip.mp4"))
        results["concurrency"] = experiment_concurrency(
            client, headers, clip, args.projects)
        c = results["concurrency"]
        print(f"  accepted {c['accepted']}/{c['requested']} · completed {c['completed']} "
              f"· stuck {c['stuck_in_processing']} · {c['total_seconds']}s "
              f"· {c['throughput_projects_per_min']}/min "
              f"· pool {c['worker_pool_size']} · peak queue {c['peak_queue_depth']} "
              f"· {'PASS' if c['meets_target'] else 'FAIL'}")

    os.makedirs(os.path.dirname(args.out), exist_ok=True)
    # Merge with any previous run so a CI matrix can accumulate platform rows.
    existing = {}
    if os.path.exists(args.out):
        try:
            with open(args.out) as f:
                existing = json.load(f)
        except Exception:
            existing = {}
    rows = existing.get("platform_matrix", [])
    rows = [r for r in rows if r.get("os") != results["platform"]["os"]]
    rows.append(results["platform"])
    results["platform_matrix"] = sorted(rows, key=lambda r: r["os"])

    with open(args.out, "w") as f:
        json.dump(results, f, indent=2)
    print(f"\nSaved {args.out}")
    shutil.rmtree(work, ignore_errors=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
