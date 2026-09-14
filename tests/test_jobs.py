"""The bounded job queue.

REGRESSION COVERAGE. Uploads used to spawn one unbounded thread each. Measured
on an 8-core host, 100 simultaneous uploads produced: 100 accepted, **0
completed**, 100 stuck, 903s elapsed, 0 segments. Nothing errored — ~100
concurrent ffmpeg subprocesses and 100 threads contending for model-load locks
simply never made progress. These tests pin the properties that fix depends on.
"""
import threading
import time

import pytest

from engine import jobs


def test_worker_count_is_bounded_and_sane():
    n = jobs.max_workers()
    assert 1 <= n <= 32
    assert n <= (jobs.os.cpu_count() or 4), \
        "more workers than cores cannot raise throughput on a CPU-bound pipeline"


def test_worker_count_honours_config(sm):
    sm.set_config("max_concurrent_jobs", "3")
    try:
        assert jobs.max_workers() == 3
    finally:
        sm.set_config("max_concurrent_jobs", "")
    # empty means "derive it", not "zero workers"
    assert jobs.max_workers() >= 2


def test_unset_config_falls_back_to_the_derived_default(sm):
    sm.set_config("max_concurrent_jobs", "")
    assert jobs.max_workers() == jobs.default_workers()


def test_garbage_config_does_not_break_the_pool(sm):
    sm.set_config("max_concurrent_jobs", "not-a-number")
    try:
        assert jobs.max_workers() == jobs.default_workers()
    finally:
        sm.set_config("max_concurrent_jobs", "")


def test_concurrency_never_exceeds_the_pool_size(sm):
    """The property the fix exists to guarantee: however many jobs are
    submitted at once, only `max_workers` run concurrently."""
    sm.set_config("max_concurrent_jobs", "3")
    jobs.shutdown(wait=True)
    peak = {"n": 0}
    live = {"n": 0}
    lock = threading.Lock()
    done = threading.Event()
    total = 20

    def slow(pid):
        with lock:
            live["n"] += 1
            peak["n"] = max(peak["n"], live["n"])
        time.sleep(0.12)
        with lock:
            live["n"] -= 1

    try:
        ids = []
        for i in range(total):
            pid = f"job_{i}"
            sm.execute("INSERT INTO footages(id,job_id,owner_id,name,filename,"
                       "original_path,status,created_at) VALUES(?,?,?,?,?,?,?,?)",
                       (pid, pid, 1, pid, "x.mp4", "", "uploaded", sm.now()))
            ids.append(pid)
            jobs.submit(pid, fn=slow)

        deadline = time.time() + 30
        while time.time() < deadline:
            if jobs.queue_depth()["queued"] == 0 and jobs.queue_depth()["running"] == 0:
                break
            time.sleep(0.05)

        assert peak["n"] <= 3, f"pool of 3 ran {peak['n']} jobs at once"
        assert peak["n"] >= 2, "pool should actually run jobs in parallel"
    finally:
        sm.set_config("max_concurrent_jobs", "")
        jobs.shutdown(wait=True)


def test_every_submitted_job_runs_exactly_once(sm):
    """No accepted work may be lost — the failure the 100-project experiment
    caught was silent loss, not errors."""
    sm.set_config("max_concurrent_jobs", "4")
    jobs.shutdown(wait=True)
    seen = []
    lock = threading.Lock()

    def record(pid):
        with lock:
            seen.append(pid)

    try:
        ids = [f"once_{i}" for i in range(25)]
        for pid in ids:
            sm.execute("INSERT INTO footages(id,job_id,owner_id,name,filename,"
                       "original_path,status,created_at) VALUES(?,?,?,?,?,?,?,?)",
                       (pid, pid, 1, pid, "x.mp4", "", "uploaded", sm.now()))
            jobs.submit(pid, fn=record)
        deadline = time.time() + 30
        while time.time() < deadline and len(seen) < len(ids):
            time.sleep(0.05)
        assert sorted(seen) == sorted(ids)
        assert len(seen) == len(set(seen)), "a job ran more than once"
    finally:
        sm.set_config("max_concurrent_jobs", "")
        jobs.shutdown(wait=True)


def test_submitting_the_same_project_twice_is_idempotent(sm):
    sm.set_config("max_concurrent_jobs", "2")
    jobs.shutdown(wait=True)
    calls = []
    started = threading.Event()

    def slow(pid):
        calls.append(pid)
        started.set()
        time.sleep(0.3)

    try:
        pid = "dupe_1"
        sm.execute("INSERT INTO footages(id,job_id,owner_id,name,filename,"
                   "original_path,status,created_at) VALUES(?,?,?,?,?,?,?,?)",
                   (pid, pid, 1, pid, "x.mp4", "", "uploaded", sm.now()))
        jobs.submit(pid, fn=slow)
        started.wait(5)
        jobs.submit(pid, fn=slow)      # while still running — must be ignored
        time.sleep(0.6)
        assert calls.count(pid) == 1
    finally:
        sm.set_config("max_concurrent_jobs", "")
        jobs.shutdown(wait=True)


def test_a_crashing_job_frees_its_slot_and_marks_the_project(sm):
    sm.set_config("max_concurrent_jobs", "2")
    jobs.shutdown(wait=True)

    def boom(pid):
        raise RuntimeError("kaboom")

    try:
        pid = "crash_1"
        sm.execute("INSERT INTO footages(id,job_id,owner_id,name,filename,"
                   "original_path,status,created_at) VALUES(?,?,?,?,?,?,?,?)",
                   (pid, pid, 1, pid, "x.mp4", "", "uploaded", sm.now()))
        jobs.submit(pid, fn=boom)
        deadline = time.time() + 10
        while time.time() < deadline and jobs.queue_depth()["running"]:
            time.sleep(0.05)
        assert jobs.queue_depth()["running"] == 0, "crashed job leaked its slot"
        status = sm.q("SELECT status FROM footages WHERE id=?", (pid,), one=True)["status"]
        assert status == "error"
    finally:
        sm.set_config("max_concurrent_jobs", "")
        jobs.shutdown(wait=True)


def test_upload_returns_queued_not_processing(client, auth, sandbox, has_ffmpeg):
    """The API contract changed with the pool: work is accepted immediately but
    starts when a worker frees up."""
    if not has_ffmpeg:
        pytest.skip("ffmpeg needed to build an upload fixture")
    import subprocess
    clip = str(sandbox / "queued.mp4")
    subprocess.run(["ffmpeg", "-y", "-f", "lavfi",
                    "-i", "color=c=black:s=160x120:d=1:r=10",
                    "-c:v", "libx264", "-preset", "ultrafast",
                    "-pix_fmt", "yuv420p", clip], capture_output=True, check=True)
    with open(clip, "rb") as f:
        r = client.post("/api/footages", headers=auth,
                        files={"file": ("queued.mp4", f, "video/mp4")},
                        data={"project_name": "Job Queue Tests"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "queued"
    assert body["queue_position"] >= 1
    assert body["footage_id"] and body["project_id"], \
        "an upload must always land inside a project"


def test_status_endpoint_reports_queue_depth(client, auth):
    d = client.get("/api/status", headers=auth).json()
    assert "queue" in d
    assert set(d["queue"]) == {"queued", "running", "workers"}
    assert d["queue"]["workers"] >= 1
