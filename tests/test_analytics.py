"""Deep analytics and the detection-overlay payload."""
import json
import uuid

import pytest

from engine import analytics


@pytest.fixture(scope="module")
def seeded(sm):
    """Two fusion segments with known content, so aggregations are checkable
    against hand-countable expectations rather than whatever the corpus holds."""
    pid = uuid.uuid4().hex
    sm.execute("INSERT INTO footages(id,job_id,owner_id,name,filename,original_path,"
               "status,created_at,original_bytes,stored_bytes) VALUES(?,?,?,?,?,?,?,?,?,?)",
               (pid, uuid.uuid4().hex, 1, "an", "an.mp4", "", "done", sm.now(), 1000, 300))

    def seg(idx, objects, actions, ssig, tier, fusion, sentiment=0.5):
        sid = uuid.uuid4().hex
        sm.execute(
            "INSERT INTO segments(id,footage_id,idx,start_time,end_time,ts,motion,"
            "objects,actions,sentiment_score,ssig,tier,threat_level,hazard_level,"
            "metadata,fusion,features,pipeline_profile,original_bytes,stored_bytes)"
            " VALUES(?,?,?,?,?,?,1,?,?,?,?,?,?,?,?,?,?,'fusion',?,?)",
            (sid, pid, idx, idx * 15, idx * 15 + 15, sm.now(),
             json.dumps(objects), json.dumps(actions), sentiment, ssig, tier,
             "high" if ssig > .7 else "low", "critical" if ssig > .7 else "none",
             json.dumps({"overlay": [{"t": 0.0, "boxes": [
                 {"label": "Gun", "conf": 0.8, "track": 1, "critical": True,
                  "box": [0.1, 0.1, 0.4, 0.6]}]}]}),
             json.dumps(fusion), "{}", 500, 150))
        return sid

    s1 = seg(0,
             [{"label": "Gun", "confidence": 0.8, "frame_hits": 9, "track": 1, "critical": True},
              {"label": "person", "confidence": 0.9, "frame_hits": 12, "track": 2, "instances": 4}],
             [{"action": "Robbery", "confidence": 0.7}], 0.88, "HIGH",
             {"trace": [{"channel": "action", "weight": 0.4, "value": 0.6},
                        {"channel": "objects", "weight": 0.35, "value": 0.8},
                        {"channel": "sentiment", "weight": 0.25, "value": 0.7}],
              "modifiers": [{"name": "weapon", "amount": 0.2, "why": "weapon"},
                            {"name": "crowd", "amount": 0.07, "why": "crowd"}]})
    s2 = seg(1,
             [{"label": "person", "confidence": 0.7, "frame_hits": 5, "track": 2, "instances": 1}],
             [{"action": "Normal", "confidence": 0.9}], 0.2, "LOW",
             {"trace": [{"channel": "action", "weight": 0.4, "value": 0.0},
                        {"channel": "objects", "weight": 0.35, "value": 0.0},
                        {"channel": "sentiment", "weight": 0.25, "value": 0.2}],
              "modifiers": []})
    return {"project": pid, "segments": [s1, s2]}


# --------------------------------------------------------------------------
# Detections
# --------------------------------------------------------------------------
def test_tracks_are_attributed_correctly(seeded):
    d = analytics.detection_analytics(seeded["project"])
    assert [o["label"] for o in d["track1"]] == ["Gun"]
    assert [o["label"] for o in d["track2"]] == ["person"]


def test_legacy_objects_are_not_filed_under_the_threat_detector(sm):
    """REGRESSION. Legacy segments have no `track` field, and the legacy COCO
    detector marks `person` and `car` as critical. Bucketing on `critical`
    filed pedestrians under the purpose-trained threat detector."""
    pid = uuid.uuid4().hex
    sm.execute("INSERT INTO footages(id,job_id,owner_id,name,filename,original_path,"
               "status,created_at) VALUES(?,?,?,?,?,?,?,?)",
               (pid, pid, 1, "legacy", "l.mp4", "", "done", sm.now()))
    sm.execute("INSERT INTO segments(id,footage_id,idx,ts,motion,objects,actions,"
               "ssig,tier,pipeline_profile) VALUES(?,?,0,?,1,?,?,0.5,'MEDIUM','legacy')",
               (uuid.uuid4().hex, pid, sm.now(),
                json.dumps([{"label": "person", "confidence": 0.9, "critical": True},
                            {"label": "car", "confidence": 0.8, "critical": True}]),
                json.dumps([{"action": "robot dancing", "confidence": 0.4}])))
    d = analytics.detection_analytics(pid)
    assert d["track1"] == [], "no Track 1 class was present in this segment"
    assert {o["label"] for o in d["track2"]} == {"person", "car"}


def test_profile_filter_separates_the_corpora(sm, seeded):
    everything = analytics.detection_analytics()
    fusion_only = analytics.detection_analytics(profile="fusion")
    assert fusion_only["profile"] == "fusion"
    assert fusion_only["segments_analysed"] <= everything["segments_analysed"]


def test_prevalence_and_confidence_are_derived(seeded):
    d = analytics.detection_analytics(seeded["project"])
    gun = d["track1"][0]
    assert gun["segments"] == 1
    assert gun["prevalence"] == 50.0          # 1 of 2 segments
    assert gun["avg_conf"] == pytest.approx(0.8)
    assert gun["avg_frame_hits"] == pytest.approx(9.0)


def test_co_occurrence_pairs_are_symmetric_and_deduped(seeded):
    d = analytics.detection_analytics(seeded["project"])
    pairs = {(p["a"], p["b"]) for p in d["co_occurrence"]}
    assert ("Gun", "person") in pairs
    assert ("person", "Gun") not in pairs, "each pair should appear once"


def test_histograms_cover_the_unit_interval(seeded):
    d = analytics.detection_analytics(seeded["project"])
    h = d["confidence_histogram"]["track1"]
    assert h[0]["lo"] == 0.0 and h[-1]["hi"] == 1.0
    assert sum(b["count"] for b in h) == 1


# --------------------------------------------------------------------------
# Decision logic
# --------------------------------------------------------------------------
def test_modifier_fire_rates(seeded):
    d = analytics.decision_analytics(seeded["project"])
    names = {m["name"]: m for m in d["modifiers"]}
    assert names["weapon"]["fired"] == 1
    assert names["weapon"]["fire_rate"] == 50.0     # 1 of 2 traced segments
    assert names["weapon"]["avg_amount"] == pytest.approx(0.2)


def test_channel_shares_sum_to_100(seeded):
    d = analytics.decision_analytics(seeded["project"])
    assert sum(c["share_percent"] for c in d["channels"]) == pytest.approx(100, abs=0.1)


def test_ssig_histogram_and_stats(seeded):
    d = analytics.decision_analytics(seeded["project"])
    assert sum(b["count"] for b in d["ssig_histogram"]) == d["segments_analysed"]
    assert d["ssig_stats"]["min"] == pytest.approx(0.2)
    assert d["ssig_stats"]["max"] == pytest.approx(0.88)


def test_segments_without_a_trace_are_counted_separately(sm, seeded):
    d = analytics.decision_analytics()
    assert d["segments_with_trace"] <= d["segments_analysed"]


def test_boundary_sensitivity_uses_live_thresholds(sm, seeded):
    sm.set_config("threshold_low", "0.2")
    sm.set_config("threshold_high", "0.9")
    try:
        d = analytics.decision_analytics(seeded["project"])
        assert d["thresholds"] == {"low": 0.2, "high": 0.9}
        # the 0.2 segment sits exactly on the low boundary
        assert d["boundary_sensitivity"]["near_low"] >= 1
    finally:
        sm.set_config("threshold_low", "0.4")
        sm.set_config("threshold_high", "0.7")


# --------------------------------------------------------------------------
# Timeline + overlay
# --------------------------------------------------------------------------
def test_timeline_arrays_are_parallel(seeded):
    t = analytics.timeline_analytics(seeded["project"])
    n = t["n"]
    for key in ("idx", "ssig", "sentiment", "motion", "tier", "stored_bytes"):
        assert len(t[key]) == n, f"{key} is not parallel to the others"


def test_overlay_endpoint_returns_normalised_boxes(client, auth, seeded):
    r = client.get(f"/api/segments/{seeded['segments'][0]}/overlay", headers=auth)
    assert r.status_code == 200
    d = r.json()
    assert d["available"] is True
    box = d["frames"][0]["boxes"][0]
    assert box["label"] == "Gun" and box["track"] == 1 and box["critical"] is True
    assert all(0.0 <= v <= 1.0 for v in box["box"]), "boxes must be normalised for the client"


def test_overlay_degrades_for_segments_without_capture(client, auth, sm):
    """Legacy segments must return an empty timeline with an explanation, not a
    404 — the player falls back to plain video rather than erroring."""
    pid, sid = uuid.uuid4().hex, uuid.uuid4().hex
    sm.execute("INSERT INTO footages(id,job_id,owner_id,name,filename,original_path,"
               "status,created_at) VALUES(?,?,?,?,?,?,?,?)",
               (pid, pid, 1, "old", "o.mp4", "", "done", sm.now()))
    sm.execute("INSERT INTO segments(id,footage_id,idx,ts,motion,metadata,tier) "
               "VALUES(?,?,0,?,1,'{}','LOW')", (sid, pid, sm.now()))
    d = client.get(f"/api/segments/{sid}/overlay", headers=auth).json()
    assert d["available"] is False
    assert d["frames"] == []
    assert "reprocess" in (d["note"] or "").lower()


def test_overlay_404s_for_an_unknown_segment(client, auth):
    assert client.get("/api/segments/nope/overlay", headers=auth).status_code == 404


def test_alert_analytics_requires_an_operator_role(client, operator):
    assert client.get("/api/analytics/alerts",
                      headers=operator["User"]).status_code == 403
