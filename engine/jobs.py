"""jobs.py — bounded job queue for pipeline runs.

## Why this exists

The upload endpoint used to start a bare `threading.Thread` per project. That
is fine for a handful of uploads and fails completely under the stated target
of 100 concurrent projects. Measured on an 8-core host, submitting 100 uploads
at once produced:

    accepted 100/100 · completed 0 · stuck 100 · 903s elapsed · 0 segments

Nothing errored — the work simply never progressed. Each thread independently
spawns an ffmpeg subprocess for segmentation and loads its own view of three
torch models, so 100 threads meant ~100 concurrent ffmpeg processes and 100
threads contending for model-load locks, each with torch's own intra-op thread
pool underneath. The machine thrashed instead of working.

Unbounded concurrency was never the right design here: the pipeline is
CPU-bound, so running more jobs than cores cannot increase throughput, it can
only increase contention and memory pressure.

## What this does

A single bounded `ThreadPoolExecutor` with a queue in front of it. Uploads are
accepted immediately (the API still returns straight away), marked `queued`,
and picked up as workers free. Throughput is set by `max_concurrent_jobs`,
which defaults to a conservative fraction of the core count.

The API contract is unchanged — a project still moves to `done` or `error` on
its own — but it now gains an explicit `queued` state, and
`GET /api/status` reports queue depth so the backlog is visible rather than
inferred from projects that appear frozen.
"""
import os
import threading
from concurrent.futures import ThreadPoolExecutor

from engine import storage_manager as sm

_executor = None
_lock = threading.Lock()
_queued = set()
_running = set()
_state_lock = threading.Lock()


def default_workers():
    """Conservative default: half the cores, at least 2, at most 6.

    The pipeline is CPU-bound and each job additionally shells out to ffmpeg,
    which parallelises internally. Matching worker count to core count
    oversubscribes badly once ffmpeg's own threads are counted, so the default
    deliberately leaves headroom for them and for serving HTTP.
    """
    cores = os.cpu_count() or 4
    return max(2, min(6, cores // 2))


def max_workers():
    """Pool size: env override first, then config, then derived from cores.

    The env override exists for constrained hosts. A free Hugging Face Space
    gets 2 vCPU, where the derived default of 2 workers plus ffmpeg's own
    threads oversubscribes badly — QUINTRIX_MAX_JOBS=1 there keeps it usable.
    """
    env = os.environ.get("QUINTRIX_MAX_JOBS")
    if env:
        try:
            return max(1, min(32, int(env)))
        except ValueError:
            pass
    try:
        v = int(sm.get_cfg("max_concurrent_jobs", int, default_workers()))
        return max(1, min(32, v))
    except Exception:
        return default_workers()


def _pool():
    global _executor
    with _lock:
        if _executor is None:
            n = max_workers()
            _executor = ThreadPoolExecutor(max_workers=n,
                                           thread_name_prefix="itso-job")
            sm.log("system", f"Pipeline worker pool started with {n} worker(s)",
                   context={"workers": n, "cores": os.cpu_count()})
        return _executor


def limit_torch_threads():
    """Stop torch from oversubscribing the machine.

    Each worker runs its own torch ops, and torch defaults to one intra-op
    thread per core *per caller*. With several workers that multiplies into far
    more threads than cores, which is a large part of why the unbounded version
    stalled.
    """
    try:
        import torch
        per = max(1, (os.cpu_count() or 4) // max_workers())
        torch.set_num_threads(per)
    except Exception:                                       # pragma: no cover
        pass


def queue_depth():
    with _state_lock:
        return {"queued": len(_queued), "running": len(_running),
                "workers": max_workers()}


def submit(footage_id, fn=None):
    """Queue a project for processing. Returns its queue position."""
    from engine import itso_engine

    fn = fn or itso_engine.process_footage
    with _state_lock:
        if footage_id in _queued or footage_id in _running:
            return len(_queued)
        _queued.add(footage_id)
        position = len(_queued)

    sm.execute("UPDATE footages SET status='queued' WHERE id=?", (footage_id,))

    def run():
        with _state_lock:
            _queued.discard(footage_id)
            _running.add(footage_id)
        try:
            fn(footage_id)
        except Exception as e:                              # pragma: no cover
            # process_footage handles its own errors; this is the last resort
            # so a worker thread can never die silently and leak a slot.
            sm.execute("UPDATE footages SET status='error' WHERE id=?", (footage_id,))
            sm.log("error", f"Job crashed for {footage_id}: {e}", severity="error",
                   context={"project": footage_id})
        finally:
            with _state_lock:
                _running.discard(footage_id)

    _pool().submit(run)
    return position


def shutdown(wait=True):
    """Used by tests to drain the pool deterministically."""
    global _executor
    with _lock:
        if _executor is not None:
            _executor.shutdown(wait=wait)
            _executor = None


def recover_orphans():
    """Re-queue work that was in flight when the process last stopped.

    The pool lives inside the server process, so a restart (deploy, crash,
    container replacement) abandons anything queued or mid-run: those projects
    stay `queued`/`processing` forever and never finish. On startup we find
    them and put them back on the queue.

    Safe to run repeatedly — `submit()` ignores a project already tracked, and
    `process_footage` rebuilds a project's rows from scratch anyway.
    """
    rows = sm.q("SELECT id, name FROM footages WHERE status IN ('queued','processing')")
    if not rows:
        return []
    sm.log("system", f"Recovering {len(rows)} project(s) abandoned by a restart",
           context={"projects": [r["id"] for r in rows]})
    for r in rows:
        submit(r["id"])
    return [r["id"] for r in rows]
