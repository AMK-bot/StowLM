"""T18 loading/cache contracts use real geometry assembly, without hydro builds."""

import json
import re

import pytest

from stowage.model import Vessel
from stowage.rules.catalogue import rule_freeze_v1
from stowage.vessel import fleet, fleet_cache


@pytest.fixture(autouse=True)
def isolated_cache(monkeypatch, tmp_path):
    monkeypatch.setenv("STOWAGE_VESSEL_CACHE_DIR", str(tmp_path / "vessels"))
    fleet_cache.clear_vessel_memo()
    yield
    fleet_cache.clear_vessel_memo()


@pytest.mark.parametrize("name,pump,heel,cranes", [
    ("feeder_500", 500, 600, 2), ("feeder_800", 700, 1000, 2), ("feeder_1700", 1000, 1500, 3),
])
def test_t18_fleet_loader_attaches_only_when_activated_and_round_trips(name, pump, heel, cranes):
    with rule_freeze_v1(enabled=False):
        off = fleet.load_vessel(name, with_hull=False)
    assert off.ballast_system is None
    assert "ballast_system" not in off.model_dump()
    with rule_freeze_v1():
        on = fleet.load_vessel(name, with_hull=False)
        assert on.ballast_system is not None
        assert (on.ballast_system.ballast_pump_m3_h, on.ballast_system.heel_transfer_m3_h,
                on.ballast_system.max_cranes) == (pump, heel, cranes)
        assert Vessel.model_validate_json(on.model_dump_json()) == on
    with rule_freeze_v1(enabled=False):
        assert fleet.load_vessel(name, with_hull=False).model_dump_json() == off.model_dump_json()


def test_legacy_missing_raw_system_is_unknown_without_numeric_fallback(monkeypatch):
    payload = fleet._parameter_bytes("feeder_500")
    payload = re.sub(rb"\n\[ballast_system\]\n.*?(?=\n\[|\Z)", b"\n", payload, flags=re.S)
    monkeypatch.setattr(fleet, "_parameter_bytes", lambda name: payload)
    with rule_freeze_v1():
        vessel = fleet.load_vessel("feeder_500", with_hull=False)
    assert vessel.ballast_system is None
    assert "ballast_system" not in json.loads(vessel.model_dump_json())


def test_ballast_rates_participate_in_cache_identity_and_mutable_results_are_isolated(monkeypatch):
    original = fleet._parameter_bytes("feeder_500")
    changed = original.replace(b"ballast_pump_m3_h = 500", b"ballast_pump_m3_h = 501", 1)
    assert changed != original
    assert fleet_cache._cache_key("feeder_500", "full.rule-freeze-v1", False, changed) != fleet_cache._cache_key(
        "feeder_500", "full.rule-freeze-v1", False, original)
    with rule_freeze_v1():
        before = fleet.load_vessel("feeder_500", with_hull=False)
        before.ballast_system.ballast_pump_m3_h = 999
        assert fleet.load_vessel("feeder_500", with_hull=False).ballast_system.ballast_pump_m3_h == 500
        monkeypatch.setattr(fleet, "_parameter_bytes", lambda name: changed)
        assert fleet.load_vessel("feeder_500", with_hull=False).ballast_system.ballast_pump_m3_h == 501
