"""Pipeline stages against real generated video, plus regression coverage for
the defects found while integrating the CCTV pipeline."""
import os
import uuid

import cv2
import pytest


# --------------------------------------------------------------------------
# Stage 2 — Invaligator motion filter
# --------------------------------------------------------------------------
def _sample(path, budget=48):
    from engine import itso_engine as eng
    return eng._sample_frames(path)[0]


def test_still_video_is_filtered_out(sm, still_video):
    """REGRESSION. MOG2 classifies every pixel of its first frame as foreground
    because it has no background model yet, so taking max() across all frames
    reported a motion ratio of 1.0 for a completely static clip. Stage 2 —
    whose entire purpose is skipping static footage — passed everything, and
    the compute saving credited to it was never actually realised."""
    from engine import itso_engine as eng
    frames = _sample(still_video)
    assert len(frames) > 8
    moved, peak = eng.invaligator(frames, 0.001)
    assert moved is False, f"static clip reported motion (peak={peak})"


def test_moving_video_is_detected(sm, tiny_video):
    from engine import itso_engine as eng
    moved, peak = eng.invaligator(_sample(tiny_video), 0.001)
    assert moved is True
    assert peak > 0.001


def test_warmup_frames_are_excluded_from_the_measurement(sm, still_video):
    """The first frame's ratio must not reach the reported peak."""
    from engine import itso_engine as eng
    frames = _sample(still_video)
    mog = cv2.createBackgroundSubtractorMOG2(detectShadows=False)
    first = float((mog.apply(frames[0]) > 200).mean())
    _, peak = eng.invaligator(frames, 0.001)
    assert first > 0.9, "precondition: cold MOG2 should report near-total foreground"
    assert peak < first


def test_single_frame_spike_does_not_trip_the_gate(sm):
    """A codec keyframe refresh produces a one-frame foreground spike on
    otherwise still footage. One spike must not count as motion."""
    import numpy as np
    from engine import itso_engine as eng
    base = np.full((120, 160, 3), 40, dtype=np.uint8)
    frames = [base.copy() for _ in range(20)]
    frames[10][:] = 200                      # one wildly different frame
    moved, _ = eng.invaligator(frames, 0.001)
    assert moved is False


def test_too_few_frames_is_not_motion(sm):
    from engine import itso_engine as eng
    assert eng.invaligator([], 0.001) == (False, 0.0)


# --------------------------------------------------------------------------
# Stage 1 — segmentation
# --------------------------------------------------------------------------
def test_segmentation_is_lossless_and_does_not_inflate(sm, tiny_video, has_ffmpeg):
    """REGRESSION. Segmenting via cv2.VideoWriter re-encodes H.264 into MPEG-4
    Part 2. Measured on a 17 MB clip that produced 63 MB of segments — a 3.7x
    inflation before any tiering, against which every later savings figure was
    then computed."""
    if not has_ffmpeg:
        pytest.skip("stream-copy segmentation needs ffmpeg")
    from engine import itso_engine as eng
    fps, segs, lossless = eng.segment_video(tiny_video, 3)
    assert lossless is True
    assert len(segs) >= 2
    total = sum(os.path.getsize(p) for _, p, _, _ in segs)
    original = os.path.getsize(tiny_video)
    # Each output file carries its own MP4 container header, which is a fixed
    # ~1KB per segment regardless of content. On this deliberately tiny fixture
    # that overhead is a large *fraction*, so the budget allows for it
    # explicitly rather than using a loose percentage that would also let a
    # genuine re-encode through. The defect this guards against was 3.7x.
    budget = original * 1.05 + 2048 * len(segs)
    assert total <= budget, (
        f"segmentation inflated {original} -> {total} bytes "
        f"({total / original:.2f}x) beyond container overhead")
    assert total < original * 2, "clear sign of a re-encode rather than a copy"


def test_segment_frames_span_the_whole_segment(sm, tiny_video):
    """REGRESSION. Buffering only the first 32 frames meant detector sampling
    never saw past the first second of a 15-second segment."""
    from engine import itso_engine as eng
    # One segment spanning the whole 6s clip: 90 frames at 15fps, comfortably
    # above FRAME_BUDGET, so decimation genuinely happens.
    _, segs, _ = eng.segment_video(tiny_video, 30)
    idx, path, frames, n_frames = segs[0]
    assert n_frames > eng.FRAME_BUDGET, "precondition: segment must exceed the budget"
    assert len(frames) <= eng.FRAME_BUDGET, "buffer must stay bounded"
    assert n_frames > len(frames), "frames should be decimated from a larger count"
    # The decimated buffer must span the segment, not cluster at its start:
    # on this clip the box moves left-to-right, so the first and last sampled
    # frames differ substantially only if coverage is full.
    assert float(abs(frames[0].astype(int) - frames[-1].astype(int)).mean()) > 1.0


def test_segment_video_reports_frame_counts(sm, tiny_video):
    from engine import itso_engine as eng
    _, segs, _ = eng.segment_video(tiny_video, 3)
    assert all(n > 0 for _, _, _, n in segs)


# --------------------------------------------------------------------------
# End-to-end
# --------------------------------------------------------------------------
@pytest.fixture(scope="module")
def processed(sm, tiny_video, mk_project):
    from engine import itso_engine as eng
    import shutil
    pid = uuid.uuid4().hex
    dest = os.path.join(sm.ARCHIVE_DIR, "uploads", pid + ".mp4")
    shutil.copy(tiny_video, dest)
    sm.execute(
        "INSERT INTO footages(id,project_id,job_id,owner_id,name,filename,original_path,"
        "status,created_at,original_bytes) VALUES(?,?,?,?,?,?,?,?,?,?)",
        (pid, mk_project(), uuid.uuid4().hex, 1, "pytest", "pytest.mp4", dest,
         "uploaded", sm.now(), os.path.getsize(tiny_video)))
    eng.process_footage(pid)
    return pid


def test_project_completes(sm, processed):
    p = sm.q("SELECT * FROM footages WHERE id=?", (processed,), one=True)
    assert p["status"] == "done", "pipeline must always reach done or error"
    assert p["frames_total"] > 0


def test_original_upload_size_is_never_overwritten(sm, processed):
    """REGRESSION. The pipeline used to write the sum of its own segment files
    into projects.original_bytes, so every savings percentage was measured
    against a baseline the pipeline itself had produced."""
    p = sm.q("SELECT * FROM footages WHERE id=?", (processed,), one=True)
    assert p["original_bytes"] == os.path.getsize(p["original_path"])
    assert p["segment_bytes"] > 0


def test_all_five_stages_are_recorded(sm, processed):
    stages = sm.stage_report(processed)
    assert [s["stage"] for s in stages] == [1, 2, 3, 4, 5]
    assert all(s["name"] and s["unit"] for s in stages)


def test_frame_sampling_stage_shows_real_reduction(sm, processed):
    s3 = next(s for s in sm.stage_report(processed) if s["stage"] == 3)
    assert s3["frames_in"] > s3["frames_out"] > 0
    assert s3["reduction_percent"] > 50


def test_segments_carry_fusion_output(sm, processed):
    import json
    segs = sm.q("SELECT * FROM segments WHERE footage_id=? AND motion=1", (processed,))
    assert segs, "the moving test clip should produce motion segments"
    for s in segs:
        assert s["pipeline_profile"] == "fusion"
        assert s["action_backend"] == "r3d18"
        assert s["sentiment_backend"] in ("mlp", "rule")
        assert json.loads(s["features"]), "feature vector must be persisted"
        assert json.loads(s["fusion"])["trace"], "score must carry its reasons"
        assert 0.0 <= s["ssig"] <= 1.0
        assert s["tier"] in ("HIGH", "MEDIUM", "LOW")


def test_funnel_measures_savings_against_the_upload(sm, processed):
    from engine import analytics
    f = analytics.footage_funnel(processed)
    e = f["end_to_end"]
    p = sm.q("SELECT original_bytes FROM footages WHERE id=?", (processed,), one=True)
    assert e["uploaded_bytes"] == p["original_bytes"]
    assert e["stored_bytes"] > 0


def test_pipeline_survives_a_failing_model(sm, tiny_video, monkeypatch, mk_project):
    """Failure isolation: one broken model must not abort the run."""
    from engine import itso_engine as eng
    from models import suspicious_detector
    import shutil

    monkeypatch.setattr(suspicious_detector, "detect",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))
    pid = uuid.uuid4().hex
    dest = os.path.join(sm.ARCHIVE_DIR, "uploads", pid + ".mp4")
    shutil.copy(tiny_video, dest)
    sm.execute(
        "INSERT INTO footages(id,project_id,job_id,owner_id,name,filename,original_path,"
        "status,created_at,original_bytes) VALUES(?,?,?,?,?,?,?,?,?,?)",
        (pid, mk_project(), uuid.uuid4().hex, 1, "fail", "f.mp4", dest,
         "uploaded", sm.now(), os.path.getsize(tiny_video)))
    eng.process_footage(pid)
    status = sm.q("SELECT status FROM footages WHERE id=?", (pid,), one=True)["status"]
    assert status in ("done", "error"), "must never be left stuck in processing"
