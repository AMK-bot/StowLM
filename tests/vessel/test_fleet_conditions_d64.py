"""D64 rev3 generator arrival fills, separate from booked departure/witness fills."""
from math import radians, tan

import pytest

from stowage.model import Tank, TankPair
from stowage.model.fixtures import build_instance
from stowage.rules.catalogue import rule_freeze_v1
from stowage.vessel.fleet import load_vessel


def _custom_pair_instance():
    instance = build_instance()
    tanks = []
    for identifier, sign in (("custom-port-water", 1), ("custom-starboard-water", -1)):
        tanks.append(Tank(id=identifier, kind="ballast", content_density_kg_m3=1025.,
            capacity_m3=100., x_aft_m=20., x_fore_m=40., adjustable=True,
            table=[dict(volume_m3=volume, lcg_m=30., tcg_m=sign*3.,
                        vcg_m=1.+volume/100., fsm_m4=0. if volume in (0., 100.) else 1.)
                   for volume in (0., 25., 50., 75., 100.)]))
    vessel = instance.vessel.model_copy(update={"tanks": tanks,
        "tank_pairs": [TankPair(id="custom-pair", port_tank=tanks[0].id,
                                starboard_tank=tanks[1].id, role="anti_heeling")]})
    return instance.model_copy(update={"stage": "B", "vessel": vessel,
        "onboard": instance.onboard.model_copy(update={"vessel_id": vessel.id,
            "tank_masses_kg": {tanks[0].id: 123., tanks[1].id: 456.}})})


def _assert_capacity_bounds(vessel, masses):
    """Every tank (HEEL, forward, consumables) within [0, capacity*density]."""
    tanks = {tank.id: tank for tank in vessel.tanks}
    assert set(masses) <= set(tanks), set(masses) - set(tanks)
    for tank in vessel.tanks:
        mass = masses.get(tank.id, 0.)
        assert 0. <= mass <= tank.capacity_m3*tank.content_density_kg_m3, (tank.id, mass)


def _assert_forward_first(groups, start, masses):
    """A forward group is touched only after every group forward of it is full.

    ``groups`` are ordered forepeak first (largest LCG). Checked on the fill
    vector itself, independently of the audit's ``forward_groups_used``.
    """
    touched = [k for k, group in enumerate(groups)
               if any(masses.get(t.id, 0.) != start.get(t.id, 0.) for t in group)]
    for k in touched:
        for earlier in groups[:k]:
            for tank in earlier:
                assert masses.get(tank.id, 0.) == tank.capacity_m3*tank.content_density_kg_m3, (
                    f"{[t.id for t in groups[k]]} filled before {tank.id} was full")


def test_off_preserves_identical_declared_arrival_fills():
    from stowage.vessel.fleet_berth import prepare_arrival
    instance = _custom_pair_instance()
    before = instance.model_dump_json()
    with rule_freeze_v1(enabled=False):
        result = prepare_arrival(instance)
    assert result is instance
    assert result.model_dump_json() == before


def test_on_prefills_explicit_pair_with_custom_ids_without_mutating_input():
    from stowage.vessel.fleet_berth import prepare_arrival
    instance = _custom_pair_instance()
    before = instance.model_dump_json()
    with rule_freeze_v1(enabled=True):
        result = prepare_arrival(instance)
    assert instance.model_dump_json() == before
    for tank in instance.vessel.tanks:
        assert result.onboard.tank_masses_kg[tank.id] == .5*tank.capacity_m3*tank.content_density_kg_m3


def test_arrival_declares_bunkered_before_operations_without_changing_consumables():
    import json
    from stowage.vessel.fleet_berth import ARRIVAL_PREFIX, prepare_arrival

    instance = _custom_pair_instance()
    with rule_freeze_v1(enabled=True):
        result = prepare_arrival(instance)
    audit, = [json.loads(parent[len(ARRIVAL_PREFIX):]) for parent in result.provenance.parents
              if parent.startswith(ARRIVAL_PREFIX)]
    assert audit["consumables_timing"] == "bunkered before cargo operations"
    assert audit["consumables"] == "preserved actual declaration; no refuelling during cargo operations"


def test_pair_prefill_reads_current_capacity_every_call():
    from stowage.vessel.fleet_berth import prepare_arrival
    instance = _custom_pair_instance()
    resized = [tank.model_copy(update={"capacity_m3": 200., "table": [row.model_copy(
        update={"volume_m3": row.volume_m3*2}) for row in tank.table]})
        for tank in instance.vessel.tanks]
    instance = instance.model_copy(update={"vessel": instance.vessel.model_copy(update={"tanks": resized})})
    with rule_freeze_v1(enabled=True):
        result = prepare_arrival(instance)
    for tank in resized:
        assert result.onboard.tank_masses_kg[tank.id] == .5*tank.capacity_m3*tank.content_density_kg_m3


def test_loaded_arrival_preserves_other_declared_tanks_and_does_not_trim(monkeypatch):
    from stowage.vessel import fleet_berth
    instance = _custom_pair_instance()
    fuel = instance.vessel.tanks[0].model_copy(update={"id": "actual-fuel",
        "kind": "fuel_oil", "adjustable": False})
    instance.vessel = instance.vessel.model_copy(update={"tanks": [*instance.vessel.tanks, fuel]})
    instance.onboard.tank_masses_kg[fuel.id] = 1234.
    def must_not_trim(*args):
        raise AssertionError("loaded arrival must retain its existing trim recipe")
    monkeypatch.setattr(fleet_berth, "_trim_solver", must_not_trim)
    with rule_freeze_v1(enabled=True):
        result = fleet_berth.prepare_arrival(instance)
    assert result.onboard.tank_masses_kg[fuel.id] == 1234.


def test_departure_initializer_uses_same_pair_prefill_only_when_on():
    from stowage.vessel.fleet_conditions import departure_tank_masses
    instance = _custom_pair_instance()
    with rule_freeze_v1(enabled=False):
        assert set(departure_tank_masses(instance.vessel).values()) == {0.}
    with rule_freeze_v1(enabled=True):
        masses = departure_tank_masses(instance.vessel)
    for tank in instance.vessel.tanks:
        assert masses[tank.id] == .5*tank.capacity_m3*tank.content_density_kg_m3


def test_full_empty_without_forward_authority_rejects_without_mutation(monkeypatch):
    from stowage.vessel import fleet_berth
    instance = _custom_pair_instance().model_copy(update={"stage": "A", "size_class": "full"})
    instance.vessel = instance.vessel.model_copy(update={"fixed_loads": []})
    before = instance.model_dump_json()
    monkeypatch.setattr(fleet_berth, "_trim_solver", lambda *args: lambda masses: 10.)
    with rule_freeze_v1(enabled=True), pytest.raises(ValueError, match="cannot reach"):
        fleet_berth.prepare_arrival(instance)
    assert instance.model_dump_json() == before


def test_real_tank_catalogue_requires_an_explicit_pair():
    from stowage.vessel.fleet_berth import prepare_arrival
    instance = _custom_pair_instance()
    instance.vessel = instance.vessel.model_copy(update={"tank_pairs": []})
    with rule_freeze_v1(enabled=True), pytest.raises(ValueError, match="explicit anti-heeling"):
        prepare_arrival(instance)


@pytest.mark.parametrize("name", ["feeder_500", "feeder_800", "feeder_1700"])
@pytest.mark.parametrize("density", [1000., 1025.])
def test_full_empty_arrival_passes_real_in_port_p1_p6_with_half_trim_margin(name, density):
    import json
    from stowage.checker.in_port_evaluator import check_state
    from stowage.checker.in_port_liquids import prepare_context
    from stowage.vessel.fleet_berth import ARRIVAL_PREFIX, prepare_arrival, _forward_groups
    vessel = load_vessel(name)
    instance = build_instance()
    instance = instance.model_copy(update={"size_class": "full", "vessel": vessel,
        "containers": [], "load_list": [], "discharge_list": [],
        "port": instance.port.model_copy(update={"water_density_kg_m3": density}),
        "onboard": instance.onboard.model_copy(update={"vessel_id": vessel.id,
            "placements": [], "covers_off": {}, "tank_masses_kg": {}})})
    with rule_freeze_v1(enabled=True):
        result = prepare_arrival(instance)
        report = check_state(result, result.onboard,
                             prepare_context(result, result.onboard), warm_start=False)
    assert report.ran and not report.error, report.error
    assert not report.violations, report.violations
    assert abs(report.metrics["trim_m"]) <= .5*vessel.hull.lpp_m*tan(radians(1.))+1e-6
    pair = next(pair for pair in vessel.tank_pairs if pair.role == "anti_heeling")
    tanks = {tank.id: tank for tank in vessel.tanks}
    for tid in (pair.port_tank, pair.starboard_tank):
        assert result.onboard.tank_masses_kg[tid] == .5*tanks[tid].capacity_m3*tanks[tid].content_density_kg_m3
    for tank in vessel.tanks:
        assert 0. <= result.onboard.tank_masses_kg.get(tank.id, 0.) <= tank.capacity_m3*tank.content_density_kg_m3
        if not tank.adjustable:
            assert result.onboard.tank_masses_kg[tank.id] == .97*tank.capacity_m3*tank.content_density_kg_m3
    audit, = [json.loads(parent[len(ARRIVAL_PREFIX):]) for parent in result.provenance.parents
              if parent.startswith(ARRIVAL_PREFIX)]
    excluded = {pair.port_tank, pair.starboard_tank}
    groups = _forward_groups(vessel, excluded)
    used = audit["forward_groups_used"]
    assert used, "the real empty ship must need forward trim ballast"
    assert used == [[tank.id for tank in group] for group in groups[:len(used)]]
    assert all(tank.id.startswith("FPT-") for tank in groups[0])
    # A later group (forward deep water) is worked only after every forepeak
    # tank is full; the explicit catalogue names make the physical order clear.
    for group in groups[:max(0, len(used)-1)]:
        for tank in group:
            assert result.onboard.tank_masses_kg[tank.id] == tank.capacity_m3*tank.content_density_kg_m3
    if len(used) > 1:
        assert all(tid.startswith("FWD-DEEP-") for tid in used[1])
    # Direct physical assertions on the emitted vector (review W2 test gap).
    from stowage.vessel.fleet_conditions import departure_tank_masses
    with rule_freeze_v1(enabled=True):
        start = departure_tank_masses(vessel)
    masses = result.onboard.tank_masses_kg
    _assert_capacity_bounds(vessel, masses)
    _assert_forward_first(groups, start, masses)
    deep = [tank for group in groups[1:] for tank in group if tank.id.startswith("FWD-DEEP-")]
    assert deep, "fleet must declare a forward deep tank behind the forepeak"
    forepeak_full = all(masses[t.id] == t.capacity_m3*t.content_density_kg_m3 for t in groups[0])
    assert forepeak_full or all(masses.get(t.id, 0.) == start.get(t.id, 0.) for t in deep)
    # Fail-first evidence: the same helpers reject a reversed (deep-first) fill
    # and an overfilled tank, so they would catch a reversed group priority.
    reversed_fill = dict(start, **{t.id: t.capacity_m3*t.content_density_kg_m3 for t in deep})
    reversed_fill.update({t.id: .5*(start.get(t.id, 0.)+t.capacity_m3*t.content_density_kg_m3)
                          for t in groups[0]})
    with pytest.raises(AssertionError, match="filled before"):
        _assert_forward_first(groups, start, reversed_fill)
    overfilled = dict(masses, **{deep[0].id: 1.0001*deep[0].capacity_m3*deep[0].content_density_kg_m3})
    with pytest.raises(AssertionError):
        _assert_capacity_bounds(vessel, overfilled)


def test_forward_first_helper_rejects_monkeypatched_reversed_order(monkeypatch):
    """A real prepare_arrival run with reversed group priority must be caught."""
    from stowage.vessel import fleet_berth
    from stowage.vessel.fleet_conditions import departure_tank_masses
    vessel = load_vessel("feeder_500")
    instance = build_instance()
    instance = instance.model_copy(update={"size_class": "full", "vessel": vessel,
        "containers": [], "load_list": [], "discharge_list": [],
        "onboard": instance.onboard.model_copy(update={"vessel_id": vessel.id,
            "placements": [], "covers_off": {}, "tank_masses_kg": {}})})
    pair = next(pair for pair in vessel.tank_pairs if pair.role == "anti_heeling")
    groups = fleet_berth._forward_groups(vessel, {pair.port_tank, pair.starboard_tank})
    original = fleet_berth._forward_groups
    monkeypatch.setattr(fleet_berth, "_forward_groups",
                        lambda *args: list(reversed(original(*args))))
    with rule_freeze_v1(enabled=True):
        start = departure_tank_masses(vessel)
        try:
            result = fleet_berth.prepare_arrival(instance)
        except ValueError:
            pytest.skip("reversed order cannot reach the trim target on this hull")
    masses = result.onboard.tank_masses_kg
    _assert_capacity_bounds(vessel, masses)  # still capacity-bounded
    with pytest.raises(AssertionError, match="filled before"):
        _assert_forward_first(groups, start, masses)


@pytest.mark.parametrize('size', ['full', 'small'])
def test_vessel_cache_separates_runtime_manual_witness_policy(monkeypatch, size):
    from stowage.vessel import fleet_cache
    from stowage.vessel.fleet_slices import load_fleet_preset
    identities = []

    def capture(name, *, slice_name, params_bytes=b"", **kwargs):
        import tomllib
        from types import SimpleNamespace
        identities.append((name, slice_name))
        # Isolate dispatch, not a physical-vessel substitute; load_vessel's
        # stale-JSON guard reads only the declared IMDG ship type.
        return SimpleNamespace(imdg_ship_type=tomllib.loads(params_bytes.decode()).get("imdg_ship_type"))

    monkeypatch.setattr(fleet_cache, 'cached_vessel', capture)
    for enabled in (False, True, False):
        with rule_freeze_v1(enabled=enabled):
            load_fleet_preset('feeder_500', size, with_hull=False)
    assert identities[0] == identities[2] == ('feeder_500', size)
    assert identities[1] != identities[0], 'ON manual witness must not reuse an OFF vessel'
