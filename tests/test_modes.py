"""The five operating modes: definition integrity, detection, and apply."""
import json

import pytest

from engine import modes


def test_five_modes_in_a_deliberate_order():
    assert modes.ORDER == ["forensic", "security", "balanced", "economy", "edge"]
    assert set(modes.MODES) == set(modes.ORDER)


@pytest.mark.parametrize("mid", modes.ORDER)
def test_every_mode_is_fully_documented(mid):
    """A mode without a stated cost is a trap — the whole point is that the
    trade-off is explicit."""
    m = modes.MODES[mid]
    for field in ("name", "tagline", "icon", "accent", "purpose", "cost"):
        assert m[field], f"{mid} missing {field}"
    assert len(m["use_when"]) >= 2
    assert set(m["expected"]) == {"critical_miss", "storage_index", "compute"}


@pytest.mark.parametrize("mid", modes.ORDER)
def test_every_mode_sets_exactly_the_owned_keys(mid):
    """A mode must not half-configure the system: if it owns a key it has to
    set it, and it must not reach outside its own surface."""
    assert set(modes.MODES[mid]["config"]) == set(modes.OWNED_KEYS)


@pytest.mark.parametrize("mid", modes.ORDER)
def test_thresholds_are_ordered_and_in_range(mid):
    c = modes.MODES[mid]["config"]
    lo, hi = float(c["threshold_low"]), float(c["threshold_high"])
    assert 0.0 < lo < hi < 1.0, f"{mid}: {lo}/{hi} is not a valid band"
    assert 0.0 < float(c["alert_threshold"]) <= 1.0


@pytest.mark.parametrize("mid", modes.ORDER)
def test_context_rules_are_valid_json(mid):
    r = json.loads(modes.MODES[mid]["config"]["context_rules"])
    assert {"night_boost", "weapon_boost", "crowd_boost"} <= set(r)
    assert all(0 <= v <= 1 for v in r.values())


def test_modes_are_ordered_from_most_to_least_retentive():
    """Forensic should keep the most and Economy the least; a mode list whose
    ordering does not match its own thresholds would mislead an operator."""
    lows = [float(modes.MODES[m]["config"]["threshold_low"]) for m in
            ("forensic", "security", "balanced", "economy")]
    assert lows == sorted(lows), "threshold_low should rise across the retention axis"


def test_security_matches_the_calibration_recommendation():
    """High Security exists to carry the sweep's recommendation. If the sweep
    is re-run and moves, this test is the reminder to update the mode."""
    import os
    path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                        "weights", "threshold_calibration.json")
    if not os.path.exists(path):
        pytest.skip("no calibration run recorded")
    with open(path) as f:
        rec = json.load(f)["recommended"]
    c = modes.MODES["security"]["config"]
    assert float(c["threshold_low"]) == pytest.approx(rec["threshold_low"])
    assert float(c["threshold_high"]) == pytest.approx(rec["threshold_high"])


def test_edge_is_the_cheapest_to_compute():
    strides = {m: float(modes.MODES[m]["config"]["detection_stride_seconds"])
               for m in modes.ORDER}
    assert strides["edge"] == max(strides.values())
    assert strides["forensic"] == min(strides.values())


# --------------------------------------------------------------------------
# Detection + apply
# --------------------------------------------------------------------------
def test_apply_then_detect_round_trips(sm):
    for mid in modes.ORDER:
        modes.apply_mode(mid, user="pytest")
        assert modes.detect_current()["mode"] == mid, f"{mid} did not round-trip"


def test_editing_one_key_makes_it_custom(sm):
    modes.apply_mode("balanced", user="pytest")
    assert modes.detect_current()["mode"] == "balanced"
    sm.set_config("threshold_high", "0.123")
    assert modes.detect_current()["mode"] == "custom"


def test_numeric_formatting_does_not_create_a_false_custom(sm):
    """"0.70" and "0.7" are the same setting; a naive string compare would
    report Custom after a harmless round-trip through the config form."""
    modes.apply_mode("balanced", user="pytest")
    sm.set_config("threshold_high", "0.7000")
    assert modes.detect_current()["mode"] == "balanced"


def test_differences_name_the_specific_keys(sm):
    modes.apply_mode("balanced", user="pytest")
    sm.set_config("threshold_high", "0.55")
    diffs = modes.detect_current()["differences"]["balanced"]
    assert [d["key"] for d in diffs] == ["threshold_high"]
    assert diffs[0]["current"] == "0.55"
    assert diffs[0]["mode_value"] == "0.70"


def test_preview_does_not_change_anything(sm):
    modes.apply_mode("balanced", user="pytest")
    before = sm.get_config()
    modes.preview("economy")
    assert sm.get_config() == before


def test_apply_reports_what_changed(sm):
    modes.apply_mode("balanced", user="pytest")
    r = modes.apply_mode("economy", user="pytest")
    keys = {c["key"] for c in r["changed"]}
    assert "threshold_low" in keys
    assert r["name"] == "Storage Economy"


def test_apply_leaves_unowned_keys_alone(sm):
    sm.set_config("object_conf_threshold", "0.77")
    modes.apply_mode("edge", user="pytest")
    assert sm.get_config()["object_conf_threshold"] == "0.77"


def test_unknown_mode_is_rejected(sm):
    with pytest.raises(ValueError):
        modes.apply_mode("nope")
    with pytest.raises(ValueError):
        modes.preview("nope")


def test_apply_is_audited(sm):
    modes.apply_mode("security", user="pytest")
    row = sm.q("SELECT * FROM logs WHERE type='audit' AND message LIKE '%High Security%' "
               "ORDER BY id DESC LIMIT 1", one=True)
    assert row and row["user"] == "pytest"


# --------------------------------------------------------------------------
# API
# --------------------------------------------------------------------------
def test_modes_endpoint_lists_all_five(client, auth):
    d = client.get("/api/modes", headers=auth).json()
    assert len(d["modes"]) == 5
    assert d["current"] in modes.ORDER + ["custom"]
    assert sum(1 for m in d["modes"] if m["active"]) <= 1


def test_apply_requires_administrator(client, operator):
    r = client.post("/api/modes/balanced/apply", headers=operator["SecurityOperator"])
    assert r.status_code == 403


def test_preview_is_readable_by_any_role(client, operator):
    r = client.get("/api/modes/economy/preview", headers=operator["User"])
    assert r.status_code == 200
    assert "changes" in r.json()


def test_unknown_mode_404s(client, auth):
    assert client.get("/api/modes/nope/preview", headers=auth).status_code == 404
    assert client.post("/api/modes/nope/apply", headers=auth).status_code == 404


@pytest.fixture(autouse=True)
def _restore_config(sm):
    """Modes rewrite live config; put it back so ordering between test files
    cannot change another module's expectations."""
    before = sm.get_config()
    yield
    for k, v in before.items():
        sm.set_config(k, v)
