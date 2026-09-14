"""API surface: auth, RBAC, the new endpoints, and the review-queue lifecycle."""
import os
import uuid

import pytest


# --------------------------------------------------------------------------
# Auth / RBAC
# --------------------------------------------------------------------------
def test_health_is_public(client):
    assert client.get("/api/health").json()["ok"] is True


def test_new_endpoints_require_authentication(client):
    for path in ("/api/models", "/api/analytics", "/api/savings/projection",
                 "/api/review-queue", "/api/calibration"):
        assert client.get(path).status_code == 401, path


def test_review_queue_is_denied_to_plain_users(client, operator):
    assert client.get("/api/review-queue", headers=operator["User"]).status_code == 403
    assert client.get("/api/review-queue",
                      headers=operator["SecurityOperator"]).status_code == 200


def test_calibration_apply_is_admin_only(client, operator):
    assert client.post("/api/calibration/apply",
                       headers=operator["SecurityOperator"]).status_code == 403


def test_sweep_is_admin_only(client, operator):
    assert client.post("/api/review-queue/sweep",
                       headers=operator["SecurityOperator"]).status_code == 403


# --------------------------------------------------------------------------
# Model registry
# --------------------------------------------------------------------------
def test_model_registry_names_the_deployed_action_model(client, auth):
    r = client.get("/api/models", headers=auth).json()
    assert r["active_profile"] in ("fusion", "legacy")
    assert r["deployed_action_model"]
    # the ambiguity the reviewer flagged must be resolvable from the API alone
    assert "R3D-18" in r["deployed_action_model"] or "X3D" in r["deployed_action_model"]


def test_registry_reports_the_feature_contract(client, auth):
    from engine import fusion
    r = client.get("/api/models", headers=auth).json()
    assert r["feature_contract"]["names"] == fusion.FEATURE_NAMES
    assert r["feature_contract"]["n"] == fusion.N_FEATURES


def test_every_fusion_model_declares_its_stage_and_io(client, auth):
    r = client.get("/api/models", headers=auth).json()
    for m in r["fusion"] + r["legacy"] + r["stage_ops"]:
        assert m["stage"] in (1, 2, 3, 4, 5)
        assert m["input"] and m["output"] and m["role"]


# --------------------------------------------------------------------------
# What-if scoring
# --------------------------------------------------------------------------
def test_simulate_score_returns_a_full_decision(client, auth):
    r = client.post("/api/simulate/score", headers=auth, json={
        "action_severity": 0.9, "action_top_conf": 0.85, "t1_max_threat": 0.9,
        "t1_weapon_conf": 0.9, "t1_persistence": 0.8, "ctx_people": 0.5,
        "ctx_person_conf": 0.9, "motion_ratio": 0.4})
    assert r.status_code == 200
    d = r.json()
    assert 0.0 <= d["ssig"] <= 1.0
    assert d["tier"] in ("HIGH", "MEDIUM", "LOW")
    assert d["trace"] and d["features"]
    assert "rule_score" in d and "delta_vs_rule" in d


def test_simulate_score_tolerates_an_empty_body(client, auth):
    """Missing features must default to zero, not 500."""
    r = client.post("/api/simulate/score", headers=auth, json={})
    assert r.status_code == 200
    assert r.json()["ssig"] >= 0.0


def test_simulate_score_ranks_a_weapon_above_an_empty_scene(client, auth):
    quiet = client.post("/api/simulate/score", headers=auth,
                        json={"action_p_normal": 0.95}).json()
    armed = client.post("/api/simulate/score", headers=auth, json={
        "action_severity": 0.95, "action_top_conf": 0.9, "t1_max_threat": 0.95,
        "t1_weapon_conf": 0.95, "t1_persistence": 0.9}).json()
    assert armed["ssig"] > quiet["ssig"]


# --------------------------------------------------------------------------
# Savings projection
# --------------------------------------------------------------------------
def test_projection_returns_both_growth_models(client, auth):
    d = client.get("/api/savings/projection?months=24&growth=6", headers=auth).json()
    assert len(d["flat_fleet"]) == 24 and len(d["growing_fleet"]) == 24
    assert d["assumptions"]["why_exponential"]


def test_flat_fleet_is_linear_and_growing_fleet_is_exponential(client, auth):
    """The exponential claim must come from fleet growth, and be checkable.

    Asserted on the cumulative *ingest* series rather than the saved series:
    ingest is independent of the measured savings rate, which is zero on a
    corpus where nothing has been tiered yet and would flatten both curves for
    reasons unrelated to the shape under test.
    """
    d = client.get("/api/savings/projection?months=36&growth=6", headers=auth).json()
    flat = [r["baseline_gb"] for r in d["flat_fleet"]]
    grow = [r["baseline_gb"] for r in d["growing_fleet"]]

    # linear series: equal first differences
    d1 = [flat[i + 1] - flat[i] for i in range(len(flat) - 1)]
    # Relative tolerance: the series is rounded to 2dp after float
    # accumulation, so identical steps differ in the last cent. 0.1% is far
    # below the spread an exponential series would show.
    assert max(d1) - min(d1) <= max(d1) * 1e-3, \
        "flat-fleet ingest should be linear"

    # geometric series: first differences grow by (1+g) each step
    d2 = [grow[i + 1] - grow[i] for i in range(len(grow) - 1)]
    assert all(d2[i + 1] >= d2[i] for i in range(len(d2) - 1)), \
        "growing-fleet ingest should accelerate"
    assert d2[-1] > d2[0] * 1.5, "acceleration should be substantial over 36 months"
    assert grow[-1] > flat[-1]
    assert d["doubling_months"] == pytest.approx(11.9, abs=0.2)


def test_growing_fleet_saving_tracks_the_savings_rate(client, auth):
    d = client.get("/api/savings/projection?months=24&growth=8", headers=auth).json()
    rate = d["savings_rate"]
    last = d["growing_fleet"][-1]
    assert last["saved_gb"] == pytest.approx(
        last["baseline_gb"] - last["itso_gb"], rel=1e-3)
    assert last["itso_gb"] == pytest.approx(last["baseline_gb"] * (1 - rate), rel=1e-3)


def test_zero_growth_collapses_the_two_curves(client, auth):
    d = client.get("/api/savings/projection?months=12&growth=0", headers=auth).json()
    assert d["doubling_months"] is None
    assert d["growing_fleet"][-1]["saved_gb"] == pytest.approx(
        d["flat_fleet"][-1]["saved_gb"], rel=1e-6)


def test_projection_months_are_clamped(client, auth):
    assert len(client.get("/api/savings/projection?months=9999",
                          headers=auth).json()["flat_fleet"]) == 120


# --------------------------------------------------------------------------
# Calibration evidence
# --------------------------------------------------------------------------
def test_calibration_reports_current_and_recommended(client, auth):
    r = client.get("/api/calibration", headers=auth)
    if r.status_code == 404:
        pytest.skip("no calibration run recorded")
    d = r.json()
    assert d["current"]["threshold_low"] < d["current"]["threshold_high"]
    assert d["recommended"]["threshold_low"] < d["recommended"]["threshold_high"]
    assert len(d["distributions"]) >= 2
    for e in (d["current"], d["recommended"]):
        assert 0.0 <= e["worst_critical_miss"] <= 1.0


# --------------------------------------------------------------------------
# Review queue lifecycle (Tier-3 safeguard)
# --------------------------------------------------------------------------
@pytest.fixture
def held_segment(sm):
    """A segment parked in the review queue with a real file behind it."""
    pid = uuid.uuid4().hex
    sid = uuid.uuid4().hex
    sm.execute("INSERT INTO footages(id,job_id,owner_id,name,filename,original_path,"
               "status,created_at,original_bytes) VALUES(?,?,?,?,?,?,?,?,?)",
               (pid, uuid.uuid4().hex, 1, "held", "h.mp4", "", "done", sm.now(), 100))
    sm.execute("INSERT INTO segments(id,footage_id,idx,ts,motion,tier,ssig) "
               "VALUES(?,?,?,?,?,?,?)", (sid, pid, 0, sm.now(), 0, "LOW", 0.11))
    path = os.path.join(sm.ARCHIVE_DIR, "pending_purge", sid + ".mp4")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as f:
        f.write(b"\x00" * 4096)
    sm.queue_for_purge(sid, pid, "LOW", 0.11, path, 4096, 24, "test hold")
    qid = sm.q("SELECT id FROM purge_queue WHERE segment_id=?", (sid,), one=True)["id"]
    return {"qid": qid, "sid": sid, "pid": pid, "path": path}


def test_low_tier_video_is_held_not_deleted(client, auth, held_segment):
    items = client.get("/api/review-queue", headers=auth).json()["items"]
    row = next(i for i in items if i["id"] == held_segment["qid"])
    assert row["exists"] is True, "the footage must still be on disk during the hold"
    assert row["purge_after"] > row["queued_at"]


def test_restore_keeps_the_footage_and_promotes_the_tier(client, auth, sm, held_segment):
    r = client.post(f"/api/review-queue/{held_segment['qid']}/restore",
                    headers=auth, data={"tier": "MEDIUM"})
    assert r.status_code == 200
    seg = sm.q("SELECT * FROM segments WHERE id=?", (held_segment["sid"],), one=True)
    assert seg["tier"] == "MEDIUM"
    assert seg["tier_manual"] == 1
    assert seg["purge_state"] == "restored"
    assert seg["stored_bytes"] > 0, "restored footage must be re-tiered to disk"


def test_restore_rejects_an_invalid_target_tier(client, auth, held_segment):
    r = client.post(f"/api/review-queue/{held_segment['qid']}/restore",
                    headers=auth, data={"tier": "LOW"})
    assert r.status_code == 400


def test_confirm_deletion_removes_the_file(client, auth, sm, held_segment):
    r = client.post(f"/api/review-queue/{held_segment['qid']}/purge", headers=auth)
    assert r.status_code == 200
    assert not os.path.exists(held_segment["path"])
    seg = sm.q("SELECT purge_state FROM segments WHERE id=?",
               (held_segment["sid"],), one=True)
    assert seg["purge_state"] == "purged"


def test_a_queue_entry_cannot_be_actioned_twice(client, auth, held_segment):
    client.post(f"/api/review-queue/{held_segment['qid']}/purge", headers=auth)
    again = client.post(f"/api/review-queue/{held_segment['qid']}/restore",
                        headers=auth, data={"tier": "MEDIUM"})
    assert again.status_code == 400


def test_sweep_only_deletes_entries_past_their_deadline(client, auth, sm, held_segment):
    """A 24-hour hold must survive a sweep run now."""
    from engine import itso_engine as eng
    n, _ = eng.run_purge_sweep()
    assert os.path.exists(held_segment["path"]), "sweep deleted a hold before its deadline"
    # expire it, then sweep again
    sm.execute("UPDATE purge_queue SET purge_after=? WHERE id=?",
               ("2000-01-01T00:00:00", held_segment["qid"]))
    n2, freed = eng.run_purge_sweep()
    assert n2 >= 1 and freed >= 4096
    assert not os.path.exists(held_segment["path"])


def test_grace_of_zero_restores_immediate_deletion(sm):
    assert sm.queue_for_purge("x", "y", "LOW", 0.0, "/tmp/nope", 0, 0) is None
