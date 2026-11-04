"""Generation identity regressions, isolated from hull/screen feasibility.

These tests stop at the cargo or simulation boundary; they make no assertion
that a diagnostic geometry fixture is a feasible production instance.
"""
from __future__ import annotations

import pytest

from stowage.generate import instances
from stowage.generate.random_yard import SIZE_LADDER, capacity_load_size
from stowage.params import load_param_file
from stowage.vessel.fleet_slices import load_fleet_preset


class BoundaryReached(Exception):
    pass


@pytest.fixture(scope="module")
def geometry():
    return load_fleet_preset("feeder_500", "full", with_hull=False)


@pytest.fixture
def diagnostic(monkeypatch, geometry):
    monkeypatch.setattr(instances, "_full_vessel", lambda name: geometry)


def capture_cargo_seed(monkeypatch, *, attempt=0, budget=None, **controls):
    found = []

    def capture(*args, **kwargs):
        found.append(kwargs["seed"])
        raise BoundaryReached

    monkeypatch.setattr(instances, "generate_load_list", capture)
    # Jump directly to the permitted late retry; no million-candidate campaign.
    monkeypatch.setattr(instances, "range", lambda count: [attempt], raising=False)
    with pytest.raises(BoundaryReached):
        instances.generate_simulated_instance(**{"seed": 0, **controls},
                                              max_attempts=attempt + 1 if budget is None else budget)
    return found[0]


def test_candidate_retry_streams_do_not_alias_adjacent_shards(monkeypatch, diagnostic):
    late = capture_cargo_seed(monkeypatch, seed=0, attempt=1_000_000)
    next_shard = capture_cargo_seed(monkeypatch, seed=1)
    assert late != next_shard


@pytest.mark.parametrize("controls", [
    {"noise": 0.8}, {"n_boxes": 2}, {"dense_20ft": True},
    {"tiny_dg_enabled": False}, {"without_dg": True},
    {"size": "small"}, {"port_name": "us_west_coast"},
    {"vessel_name": "feeder_800"},
])
def test_generation_controls_namespace_cargo_draw(monkeypatch, diagnostic, controls):
    baseline = capture_cargo_seed(monkeypatch)
    changed = capture_cargo_seed(monkeypatch, **controls)
    assert baseline != changed


def test_retry_stream_is_reproducible(monkeypatch, diagnostic):
    assert capture_cargo_seed(monkeypatch, attempt=6) == capture_cargo_seed(monkeypatch, attempt=6)


def test_larger_retry_budget_preserves_candidate_prefix(monkeypatch, diagnostic):
    assert capture_cargo_seed(monkeypatch, budget=1) == capture_cargo_seed(monkeypatch, budget=200)


def test_noise_namespaces_route_before_cargo(monkeypatch, diagnostic):
    from random import Random
    route_seeds = []

    def record_rng(seed):
        route_seeds.append(seed)
        return Random(seed)

    monkeypatch.setattr(instances, "Random", record_rng)
    capture_cargo_seed(monkeypatch, noise=0.2)
    capture_cargo_seed(monkeypatch, noise=0.8)
    assert len(route_seeds) == 2
    assert route_seeds[0] != route_seeds[1]


def test_default_simulated_demand_uses_d36_region_policy(monkeypatch, diagnostic, geometry):
    expected = capacity_load_size(instances._tiny_region(geometry), SIZE_LADDER["tiny"],
                                 _dry20_params(), full_vessel=geometry)
    audit = instances.GenerationReport()
    with pytest.raises(BoundaryReached):
        def stop(*args, **kwargs):
            raise BoundaryReached
        monkeypatch.setattr(instances, "generate_load_list", stop)
        instances.generate_simulated_instance(seed=0, report=audit)
    assert (audit.requested_targets, audit.teu_budget, audit.length_slot_budgets) == expected
    assert audit.booking_reference["policy"] == "D36_scaled_full_export_v1"


def _dry20_params():
    from copy import deepcopy
    params = deepcopy(load_param_file("cargo", "feeder_export"))
    params["equipment"] = [next(e for e in params["equipment"] if e["id"] == "dry_20")]
    return params


def test_empty_tiny_profile_is_explicit_in_family_and_provenance(monkeypatch, diagnostic):
    captured = []
    monkeypatch.setattr(instances, "recondition_slice", lambda vessel, **kw: vessel)
    monkeypatch.setattr(instances, "screen_load_list", lambda *a, **kw: [])
    # Explicit diagnostic quantity isolates profile labels from D36 mass bands.
    def stop(calls, *args, **kwargs):
        captured.append(calls[0].instance)
        raise BoundaryReached
    monkeypatch.setattr(instances, "simulate_port", stop)
    with pytest.raises(BoundaryReached):
        instances.generate_simulated_instance(seed=3, tiny_dg_enabled=False, n_boxes=2)
    result = captured[0]
    assert result.family["cargo_profile"] == "empty_dry20"
    assert result.family["equipment_profile"] == "empty_dry20"
    assert result.provenance.versions["cargo_profile"] == "empty_dry20"
    assert result.provenance.versions["equipment_profile"] == "empty_dry20"
    assert result.family["requested_n_boxes"] == "2"
    assert result.family["tiny_dg_enabled"] == "false"


def test_random_stream_purposes_are_separate(monkeypatch, diagnostic):
    from random import Random

    seeds = []
    actual_generate = instances.generate_load_list

    def record_rng(seed):
        seeds.append(seed)
        return Random(seed)

    def record_cargo(*args, **kwargs):
        seeds.append(kwargs["seed"])
        return actual_generate(*args, **kwargs)

    def stop(*args, **kwargs):
        seeds.append(kwargs["seed"])
        raise BoundaryReached

    monkeypatch.setattr(instances, "Random", record_rng)
    monkeypatch.setattr(instances, "generate_load_list", record_cargo)
    monkeypatch.setattr(instances, "recondition_slice", lambda vessel, **kw: vessel)
    monkeypatch.setattr(instances, "screen_load_list", lambda *a, **kw: [])
    monkeypatch.setattr(instances, "simulate_port", stop)
    with pytest.raises(BoundaryReached):
        instances.generate_simulated_instance(seed=3, tiny_dg_enabled=False, n_boxes=2)
    # Route, target cargo, background cargo, gate and simulation must all use
    # separate namespaces, rather than restarting one RNG for related draws.
    assert len(seeds) == len(set(seeds)) == 5


def test_full_vessel_revalidates_builder_on_every_generation(monkeypatch):
    built = []

    def build(name, *, with_hull):
        vessel = object()
        built.append((name, with_hull, vessel))
        return vessel

    monkeypatch.setattr(instances, "load_vessel", build)
    first = instances._full_vessel("changing-source")
    second = instances._full_vessel("changing-source")
    assert first is not second
    assert [(name, hull) for name, hull, _ in built] == [("changing-source", True)] * 2


def test_d36_regional_underweight_booking_keeps_hard_mass_band(monkeypatch, diagnostic):
    actual_generate = instances.generate_load_list

    def underweight(*args, **kwargs):
        return [box.model_copy(update={"gross_mass_kg": box.tare_kg})
                for box in actual_generate(*args, **kwargs)]

    def must_not_simulate(*args, **kwargs):
        raise BoundaryReached("underweight booking escaped its regional mass band")

    monkeypatch.setattr(instances, "generate_load_list", underweight)
    monkeypatch.setattr(instances, "recondition_slice", lambda vessel, **kw: vessel)
    monkeypatch.setattr(instances, "screen_load_list", lambda *a, **kw: [])
    monkeypatch.setattr(instances, "simulate_port", must_not_simulate)
    report = instances.GenerationReport()
    with pytest.raises(ValueError, match="screening exhausted"):
        instances.generate_simulated_instance(seed=3, max_attempts=1, report=report)
    assert report.rejections["iscode.loading.displacement"] == 1
    assert not report.accepted
