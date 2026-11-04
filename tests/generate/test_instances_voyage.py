"""D50 producer declarations: identity, actual environment evidence and lineage."""
from importlib import import_module
from hashlib import sha256

import pytest

from stowage.frozen import StubTaintError
from stowage.io import load_instance, save_instance
from stowage.model import Instance, PortCall, Rotation, VoyageConsumption, VoyageSpec, derive_arrival_liquid_state


@pytest.fixture
def voyage_helpers():
    return import_module("stowage.generate.instances_voyage")


def test_voyage_retains_historical_generator_input_lineage(voyage_helpers, declared_instance):
    from stowage.model import Provenance
    from stowage.rules.catalogue import rule_freeze_v1

    historical = {"cargo_generator": "6.0.0", "yard_simulator": "2.0.0", "random_yard": "7.0.0"}
    supplied = Provenance(versions=historical)
    with rule_freeze_v1(enabled=True):
        voyage = voyage_helpers.build_voyage_spec(declared_instance, provenance=supplied)
    for record in (voyage, voyage.departure, voyage.arrival):
        for component, version in historical.items():
            assert version in record.provenance.input_versions[component]
            assert record.provenance.versions[component] != version
    assert supplied.versions == historical
    assert supplied.input_versions == {}


@pytest.mark.parametrize("enabled", [False, True])
def test_voyage_provenance_uses_effective_generator_epochs(voyage_helpers, declared_instance, enabled):
    from stowage.rules.catalogue import rule_freeze_v1
    from stowage.generate.cargo import effective_cargo_generator_version
    from stowage.generate.random_yard import effective_random_yard_version
    from stowage.yard import effective_yard_simulator_version

    with rule_freeze_v1(enabled=enabled):
        voyage = voyage_helpers.build_voyage_spec(declared_instance)
        expected = {"cargo_generator": effective_cargo_generator_version(),
                    "random_yard": effective_random_yard_version(),
                    "yard_simulator": effective_yard_simulator_version()}
    for record in (voyage, voyage.departure, voyage.arrival):
        for key, value in expected.items():
            if enabled:
                assert record.provenance.versions[key] == value
            else:
                assert key not in record.provenance.versions


@pytest.fixture
def declared_instance(empty_ship_instance):
    from stowage.vessel.fleet_voyage import build_voyage_policy

    candidate = empty_ship_instance.model_copy(deep=True)
    # Explicit synthetic two-tank group distinguishes D57 proportional retention
    # from the old sequential draw-down. No physics verdict is made.
    second_fuel = next(tank for tank in candidate.vessel.tanks if tank.kind == "fuel_oil").model_copy(deep=True)
    second_fuel.id = "TEST-FO-2"
    for sounding in second_fuel.table:
        sounding.lcg_m += 5.0
    candidate.vessel.tanks.append(second_fuel)
    candidate.onboard.tank_masses_kg[second_fuel.id] = 10000.0
    candidate.vessel.voyage_policy = build_voyage_policy(candidate.vessel.tanks,
        parameter_source="TEST: actual synthetic fixture tank catalog", vessel_id=candidate.vessel.id)
    candidate.port.sources = ["TEST: explicit synthetic fixture departure environment"]
    candidate.voyage = None
    candidate.rotation = Rotation(service="test-repeat", calls=[
        PortCall(locode=candidate.port.locode, index=0),
        PortCall(locode="SGSIN", index=1), PortCall(locode=candidate.port.locode, index=2),
        PortCall(locode="SGSIN", index=3)])
    # This fixture tests voyage declarations, so use the declared next call as
    # every synthetic cargo POD; retain all cargo masses and yard placements.
    for box in candidate.containers:
        box.pod = "SGSIN"
    return Instance.model_validate(candidate.model_dump())


def test_default_voyage_roundtrip_and_group_retention(voyage_helpers, declared_instance, tmp_path):
    before = declared_instance.model_dump()
    candidate = voyage_helpers.attach_default_voyage(declared_instance)
    assert declared_instance.model_dump() == before
    assert candidate.voyage.consumption is None
    assert candidate.voyage.ballast_operations == []
    assert candidate.voyage.arrival.call_index == 1
    assert candidate.voyage.arrival.locode == "SGSIN"
    assert VoyageSpec.model_validate_json(candidate.voyage.model_dump_json()) == candidate.voyage
    path = save_instance(candidate, tmp_path / "voyage.json")
    assert load_instance(path) == candidate
    arrival = derive_arrival_liquid_state(candidate.onboard, candidate.voyage, vessel=candidate.vessel)
    policy = candidate.vessel.voyage_policy
    for group in policy.groups:
        departure = sum(candidate.onboard.tank_masses_kg.get(t, 0.0) for t in group.tank_ids)
        assert sum(arrival.tank_masses_kg.get(t, 0.0) for t in group.tank_ids) == pytest.approx(0.1 * departure)
    fuel = next(group for group in policy.groups if group.kind == "fuel_oil")
    assert len(fuel.tank_ids) == 2
    for tank_id in fuel.tank_ids:
        assert arrival.tank_masses_kg[tank_id] == pytest.approx(
            0.1 * candidate.onboard.tank_masses_kg[tank_id])
    consumables = {tank for group in policy.groups for tank in group.tank_ids}
    assert {k: v for k, v in arrival.tank_masses_kg.items() if k not in consumables} == {
        k: v for k, v in candidate.onboard.tank_masses_kg.items() if k not in consumables}


def test_next_environment_uses_actual_profile_density_and_sources(voyage_helpers, declared_instance, monkeypatch):
    actual_load = voyage_helpers.load_port

    def changed_profile(name):
        port = actual_load(name)
        if port.locode == "SGSIN":
            port.water_density_kg_m3 = 1007.0
            port.sources.append("TEST: explicit density measurement source")
        return port

    monkeypatch.setattr(voyage_helpers, "load_port", changed_profile)
    voyage = voyage_helpers.build_voyage_spec(declared_instance)
    assert voyage.arrival.water_density_kg_m3 == 1007.0
    assert voyage.departure.water_density_kg_m3 == declared_instance.port.water_density_kg_m3
    assert "TEST: explicit density measurement source" in voyage.arrival.provenance.parents
    assert voyage.arrival.density_id != voyage.departure.density_id


def test_repeated_locode_has_distinct_call_and_condition_ids(voyage_helpers, declared_instance):
    first = voyage_helpers.build_voyage_spec(declared_instance)
    later = voyage_helpers.build_voyage_spec(instance_id=declared_instance.id,
        rotation=declared_instance.rotation, port=declared_instance.port, call_index=2,
        provenance=declared_instance.provenance)
    assert first.arrival.locode == later.arrival.locode == "SGSIN"
    assert first.arrival.call_index == 1 and later.arrival.call_index == 3
    assert first.arrival.id != later.arrival.id
    assert first == voyage_helpers.build_voyage_spec(declared_instance)


def test_voyage_preserves_nested_taint_and_clean_save_rejects(voyage_helpers, declared_instance, tmp_path):
    declared_instance.vessel.voyage_policy.provenance.stubs["voyage.test"] = "TEST: tainted policy"
    candidate = voyage_helpers.attach_default_voyage(declared_instance)
    assert candidate.provenance.stubs["voyage.test"] == "TEST: tainted policy"
    assert candidate.voyage.provenance.stubs["voyage.test"] == "TEST: tainted policy"
    with pytest.raises(StubTaintError, match="taint|stub"):
        save_instance(candidate, tmp_path / "tainted.json")


@pytest.mark.parametrize("locode,archetype,density", [
    ("VNSGN", "cat_lai_like", 1000.0),
    ("HKHKG", "hong_kong_like", 1025.0),
    ("MYPKG", "port_klang_like", 1025.0),
])
def test_declared_next_environment_density_lineage_and_roundtrip(
    voyage_helpers, declared_instance, repo_root, tmp_path, locode, archetype, density,
):
    from stowage.ports.profiles import load_port

    declared_instance.rotation.calls[1].locode = locode
    candidate = voyage_helpers.attach_default_voyage(declared_instance)
    arrival = candidate.voyage.arrival
    profile = load_port(archetype)
    assert profile.locode == arrival.locode == locode
    assert arrival.call_index == declared_instance.call_index + 1
    assert profile.water_density_kg_m3 == arrival.water_density_kg_m3 == density
    source_path = f"params/ports/{archetype}.toml"
    source_pin = f"{source_path} sha256:{sha256((repo_root / source_path).read_bytes()).hexdigest()}"
    assert source_pin in profile.sources
    assert set(profile.sources) <= set(arrival.provenance.parents)
    assert any("ASSUMPTION" in source and "no measured dock data" in source for source in profile.sources)
    assert profile.yard.blocks == [] and profile.equipment == []
    assert arrival.environment_id and arrival.density_id
    assert arrival.environment_id != candidate.voyage.departure.environment_id
    assert arrival.density_id != candidate.voyage.departure.density_id
    assert candidate.voyage.consumption is None and candidate.voyage.ballast_operations == []
    assert VoyageSpec.model_validate_json(candidate.voyage.model_dump_json()) == candidate.voyage
    assert voyage_helpers.build_voyage_spec(declared_instance) == candidate.voyage
    assert load_instance(save_instance(candidate, tmp_path / f"{locode}.json")) == candidate


def test_missing_next_profile_fails_closed(voyage_helpers, declared_instance):
    locode = "ZZUNK"
    declared_instance.rotation.calls[1].locode = locode
    with pytest.raises(ValueError, match=f"next-call environment.*{locode}"):
        voyage_helpers.build_voyage_spec(declared_instance)


def test_missing_next_call_or_vessel_policy_is_not_invented(voyage_helpers, declared_instance):
    declared_instance.rotation.calls = declared_instance.rotation.calls[:1]
    with pytest.raises(ValueError, match="next call"):
        voyage_helpers.build_voyage_spec(declared_instance)
    declared_instance.rotation.calls.append(PortCall(locode="SGSIN", index=1))
    declared_instance.vessel.voyage_policy = None
    with pytest.raises(ValueError, match="voyage_policy"):
        voyage_helpers.attach_default_voyage(declared_instance)


def test_explicit_next_profile_must_match_actual_next_call(voyage_helpers, declared_instance):
    from stowage.ports.profiles import load_port

    with pytest.raises(ValueError, match="LOCODE disagrees"):
        voyage_helpers.build_voyage_spec(declared_instance, next_port=load_port("us_west_coast"))


def test_existing_explicit_consumption_is_preserved(voyage_helpers, declared_instance):
    candidate = voyage_helpers.attach_default_voyage(declared_instance)
    candidate.voyage.consumption = [VoyageConsumption(group_id=group.id, mass_kg=0.0)
                                  for group in candidate.vessel.voyage_policy.groups]
    result = voyage_helpers.attach_default_voyage(candidate)
    assert result == candidate
    assert result is not candidate


def test_simulated_output_declares_voyage_before_and_after_snapshot(monkeypatch):
    from stowage.generate import instances
    from stowage.vessel.fleet_slices import load_fleet_preset

    geometry = load_fleet_preset("feeder_500", "full", with_hull=False)
    monkeypatch.setattr(instances, "_full_vessel", lambda name: geometry)
    actual_screen = instances.screen_load_list
    monkeypatch.setattr(instances, "screen_load_list", lambda *a, **kw: [
        reason for reason in actual_screen(*a, **kw) if reason != "checker.missing_vessel_data"])
    # Isolate the producer declaration from hull/placement feasibility claims.
    monkeypatch.setattr(instances, "completion_failures", lambda candidate: ())
    actual_simulate = instances.simulate_port
    snapshots = []

    def inspect_templates(calls, *args, **kwargs):
        snapshots.extend(call.instance.voyage for call in calls)
        return actual_simulate(calls, *args, **kwargs)

    monkeypatch.setattr(instances, "simulate_port", inspect_templates)
    result = instances.generate_simulated_instance(seed=2, n_boxes=2, tiny_dg_enabled=False)
    assert result.voyage is not None
    assert all(voyage is not None for voyage in snapshots)
    assert result.voyage.arrival.locode == result.rotation.calls[1].locode == "SGSIN"
    assert result.voyage.departure.water_density_kg_m3 == result.port.water_density_kg_m3
