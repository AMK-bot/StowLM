"""T18: public port inputs expose the D74 timing contract without crane simulation."""

import hashlib

import pytest

from stowage.ports import list_ports, load_port, profiles
from stowage.params import params_dir
from stowage.rules.catalogue import rule_freeze_v1


DEPTHS = {"laem_chabang_like": 16.0, "singapore_like": 18.0, "small_feeder": 8.0,
          "us_west_coast": 16.0, "cat_lai_like": 10.0, "hong_kong_like": 16.0,
          "port_klang_like": 16.0}


@pytest.mark.parametrize("name", list_ports())
def test_port_timing_and_depth_are_only_exposed_after_activation(name):
    with rule_freeze_v1(enabled=False):
        legacy = load_port(name)
    assert legacy.berth_depth_m is None
    assert "berth_depth_m" not in legacy.model_dump_json()
    if legacy.quay_crane is not None:
        assert legacy.quay_crane.gross_moves_per_hour is None
        assert "gross_moves_per_hour" not in legacy.quay_crane.model_dump_json()
    with rule_freeze_v1(enabled=True):
        active = load_port(name)
    assert active.berth_depth_m == DEPTHS[name]
    assert active.max_draught_m == DEPTHS[name]
    if active.profile_kind == "next_call_environment":
        assert active.quay_crane is None
        assert active.yard_max_tiers is active.max_crane_height_tiers is None
    else:
        crane = active.quay_crane
        assert crane.gross_moves_per_hour == (20.0 if name == "small_feeder" else 25.0)
        assert crane.pre_operation_allowance_h == crane.post_operation_allowance_h == 1.0


@pytest.mark.parametrize("name,max_cranes,expected_s", [
    ("laem_chabang_like", 2, 72.0), ("laem_chabang_like", 3, 72.0),
    ("singapore_like", 3, 48.0), ("small_feeder", 2, 180.0),
])
def test_t18_lift_interval_uses_per_crane_productivity_and_both_crane_caps(name, max_cranes, expected_s):
    with rule_freeze_v1(enabled=True):
        crane = load_port(name).quay_crane
    assert 3600.0 / (crane.gross_moves_per_hour * min(crane.count_per_berth, max_cranes)) == expected_s


def test_parameter_hash_and_loaded_value_change_when_only_productivity_changes():
    original = (params_dir("ports") / "laem_chabang_like.toml").read_bytes()
    changed = original.replace(b"gross_moves_per_hour = { value = 25,", b"gross_moves_per_hour = { value = 26,", 1)
    assert changed != original
    before, _ = profiles._bundle("laem_chabang_like", original, True, "test-catalogue")
    after, _ = profiles._bundle("laem_chabang_like", changed, True, "test-catalogue")
    assert before.quay_crane.gross_moves_per_hour == 25
    assert after.quay_crane.gross_moves_per_hour == 26
    assert f"params/ports/laem_chabang_like.toml sha256:{hashlib.sha256(changed).hexdigest()}" in after.sources


@pytest.mark.parametrize("enabled", [False, True])
def test_existing_p5b_boundary_uses_the_active_berth_cap_without_physics_solver(enabled):
    """Interface boundary only: declared deep-1700 pose, not a solved condition."""
    import math
    from types import SimpleNamespace
    from stowage.checker.in_port_evaluator import draught_limits

    hull = SimpleNamespace(max_draught_m=9.52, beam_m=27.6)
    floating = SimpleNamespace(t_aft_m=9.52, t_fwd_m=9.52, heel_rad=math.radians(2))
    for name, expected_positive in (("laem_chabang_like", True), ("small_feeder", False)):
        with rule_freeze_v1(enabled=enabled):
            port = load_port(name)
        instance = SimpleNamespace(vessel=SimpleNamespace(hull=hull), port=port)
        metrics, margins, broken = draught_limits(instance, None, floating, 1.0)
        if not enabled:
            assert "berth_draught_m" not in metrics
            assert "port_draught_m" not in margins
            continue
        assert metrics["berth_draught_m"] == pytest.approx(9.52 + 13.8 * math.sin(math.radians(2)))
        assert (margins["port_draught_m"] > 0) is expected_positive
        assert any("port_draught_m" in row[3] for row in broken) is not expected_positive
