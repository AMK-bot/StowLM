"""Simulated-instance contracts with geometry-only diagnostic vessel fixtures.

The fixture deliberately omits hull-table assembly to keep these unit tests
small. The fixture excludes the corresponding missing-hull screening reason
while retaining every cargo and geometry screen. Passing these tests is not
a ship loading feasibility certificate: the full checker must decide that
after a solver produces a plan.
"""
from __future__ import annotations

from copy import deepcopy

import pytest

from stowage.generate import instances
from stowage.generate.random_yard import SIZE_LADDER, capacity_load_size
from stowage.params import load_param_file
from stowage.io import load_instance, save_instance
from stowage.model import Instance, VoyageSpec
from stowage.vessel.fleet_slices import load_fleet_preset


@pytest.fixture(scope="module")
def geometry():
    return load_fleet_preset("feeder_500", "full", with_hull=False)


@pytest.fixture(autouse=True)
def explicit_geometry_diagnostic(monkeypatch, geometry):
    monkeypatch.setattr(instances, "_full_vessel", lambda name: geometry)
    actual_screen = instances.screen_load_list

    def geometry_screen(vessel, boxes, **kwargs):
        # Diagnostic only: no hydrostatic feasibility claim without hull tables.
        return [reason for reason in actual_screen(vessel, boxes, **kwargs)
                if reason != "checker.missing_vessel_data"]

    monkeypatch.setattr(instances, "screen_load_list", geometry_screen)


@pytest.mark.parametrize("enabled", [False, True])
def test_simulated_emits_certified_arrival_only_with_switch_on(monkeypatch, enabled):
    from stowage.generate import background_acceptance as gate
    from stowage.rules.catalogue import rule_freeze_v1

    seen, certified = [], []
    def prepare(booked, audit):
        seen.append(booked)
        arrival = booked.model_copy(deep=True)
        arrival.onboard.tank_masses_kg["TEST-CERTIFIED-ARRIVAL"] = 4321.
        certified.append(arrival)
        return arrival
    monkeypatch.setattr(instances, "accept_background_candidate", prepare)
    report = instances.GenerationReport()
    with rule_freeze_v1(enabled=enabled):
        emitted = instances.generate_simulated_instance(seed=3, n_boxes=3,
            without_dg=True, report=report)
    assert len(seen) == 1
    if enabled:
        assert seen[0].load_list == emitted.load_list
        assert emitted is certified[0]
        assert emitted.onboard.tank_masses_kg["TEST-CERTIFIED-ARRIVAL"] == 4321.
        assert "TEST-CERTIFIED-ARRIVAL" not in seen[0].onboard.tank_masses_kg
    else:
        assert seen[0].load_list == []
        assert emitted is not certified[0]
        assert "TEST-CERTIFIED-ARRIVAL" not in emitted.onboard.tank_masses_kg


def test_simulated_unknown_gate_stops_once_and_preserves_unknown_audit(monkeypatch):
    from dataclasses import asdict
    from stowage.generate import background_acceptance as gate
    from stowage.rules.catalogue import rule_freeze_v1

    report = instances.GenerationReport()
    assert asdict(report)["background_unknowns"] == {}
    seen = []
    monkeypatch.setattr(instances, "accept_background_candidate", gate.accept_background_candidate)
    monkeypatch.setattr(gate, "requires_background_gate", lambda *args, **kwargs: True)
    monkeypatch.setattr(gate, "_prepare_arrival_instance", lambda candidate: candidate)
    def unknown(candidate, **kwargs):
        seen.append((candidate, report.attempts))
        return gate.BackgroundAcceptance(False, ("background.compute.deadline",), "unknown",
                                          {}, {}, 0., unknown=True)
    monkeypatch.setattr(gate, "evaluate_background", unknown)
    with rule_freeze_v1(enabled=True), pytest.raises(gate.BackgroundAcceptanceUnknown):
        instances.generate_simulated_instance(seed=3, n_boxes=3, without_dg=True,
            report=report, max_attempts=50)
    assert len(seen) == 1 and seen[0][0].load_list
    assert report.attempts == seen[0][1]
    assert report.background_unknowns == {"background.compute.deadline": 1}
    assert asdict(report)["background_unknowns"] == report.background_unknowns
    assert report.background_rejections == {}
    assert "background.acceptance" not in report.rejections and not report.accepted


@pytest.mark.parametrize('size', ['tiny', 'full'])
def test_simulated_emits_real_arrival_fills_with_half_full_heel_pair(monkeypatch, size):
    """Real hull/arrival initializer; only the gate verdict is isolated here."""
    from stowage.generate import background_acceptance as gate
    from stowage.rules.catalogue import rule_freeze_v1
    from stowage.vessel.fleet_berth import prepare_arrival, ARRIVAL_PREFIX

    monkeypatch.setattr(instances, '_full_vessel',
        lambda name: load_fleet_preset(name, 'full', with_hull=True))
    monkeypatch.setattr(instances, 'accept_background_candidate', gate.accept_background_candidate)
    seen = []
    def accepted(booked, *, arrival_instance, **kwargs):
        seen.append((booked, arrival_instance))
        return gate.BackgroundAcceptance(True, (), 'ok', {}, {}, 0.)
    monkeypatch.setattr(gate, 'evaluate_background', accepted)
    report = instances.GenerationReport()
    with rule_freeze_v1(enabled=True):
        emitted = instances.generate_simulated_instance(size, seed=3, n_boxes=3,
            without_dg=True, report=report)
        (booked, arrival), = seen
        expected = prepare_arrival(booked)
    assert emitted is arrival and booked.load_list == emitted.load_list
    assert emitted.onboard.tank_masses_kg == expected.onboard.tank_masses_kg
    assert report.background_last['booking_tank_masses_kg'] == booked.onboard.tank_masses_kg
    assert report.background_last['arrival_tank_masses_kg'] == emitted.onboard.tank_masses_kg
    tanks = {tank.id: tank for tank in emitted.vessel.tanks}
    pair = next(pair for pair in emitted.vessel.tank_pairs if pair.role == 'anti_heeling')
    for tank_id in (pair.port_tank, pair.starboard_tank):
        tank = tanks[tank_id]
        assert emitted.onboard.tank_masses_kg[tank_id] == .5 * tank.capacity_m3 * tank.content_density_kg_m3
    assert all(0 <= mass <= tanks[tank_id].capacity_m3 * tanks[tank_id].content_density_kg_m3
               for tank_id, mass in emitted.onboard.tank_masses_kg.items())
    if size == 'full':
        import json
        arrival_audit = json.loads(next(parent.removeprefix(ARRIVAL_PREFIX)
            for parent in emitted.provenance.parents if parent.startswith(ARRIVAL_PREFIX)))
        assert arrival_audit['full_empty_trim'] is True
        assert abs(arrival_audit['trim_m']) <= arrival_audit['half_trim_limit_m']


# Real-hull medium serialization has bounded production integration evidence in
# experiments/instances-sim/fix-wave/d48-d49/manifest.json (all three ships).
@pytest.mark.parametrize("size", ["tiny", "small", "full"])
def test_rungs_snapshot_and_roundtrip(size, tmp_path, geometry):
    audit = instances.GenerationReport()
    # This round-trip test uses a non-DG demand profile. Default DG eligibility
    # and fail-closed screening have independent coverage below and in screens.
    instance = instances.generate_simulated_instance(
        size, seed=1, report=audit, without_dg=True,
        max_attempts=200,
    )
    assert instances.SIZE_LADDER is SIZE_LADDER
    assert instance.size_class == size
    assert instance.stage == "A"
    assert not instance.onboard.placements
    assert not instance.discharge_list
    assert instance.voyage is not None
    voyage = instance.voyage
    assert voyage.departure.phase == "departure"
    assert voyage.departure.call_index == instance.call_index
    assert voyage.departure.locode == instance.port.locode
    assert voyage.departure.water_density_kg_m3 == instance.port.water_density_kg_m3
    assert voyage.arrival.phase == "arrival"
    assert voyage.arrival.call_index == instance.call_index + 1
    assert voyage.arrival.locode == instance.rotation.calls[instance.call_index + 1].locode
    assert voyage.departure.id != voyage.arrival.id
    assert voyage.consumption is None and voyage.ballast_operations == []
    assert VoyageSpec.model_validate_json(voyage.model_dump_json()) == voyage
    params = deepcopy(load_param_file("cargo", "feeder_export"))
    params["rotations"] = [next(route for route in params["rotations"]
                                if route["id"] == instance.rotation.service)]
    if size == "tiny":
        params["equipment"] = [next(item for item in params["equipment"] if item["id"] == "dry_20")]
        params["equipment"][0]["share"]["value"] = 1.0
    expected, budget, lengths = capacity_load_size(
        instance.vessel, SIZE_LADDER[size], params, full_vessel=geometry)
    assert len(instance.load_list) == audit.requested_targets == expected
    assert instance.family["n_boxes"] == str(expected)
    assert audit.capacity_teu == len(instance.vessel.cells)
    assert instance.family["booking_policy"] == "D36_scaled_full_export_v1"
    assert audit.booking_reference["region_capacity_teu"] == audit.capacity_teu
    assert audit.booking_reference["requested_teu_fraction"] == float(instance.family["booking_requested_teu_fraction"])
    assert audit.booking_utilisation["teu_fraction"] == float(instance.family["booking_utilisation_teu"])
    assert 0.75 <= audit.booking_utilisation["cargo_mass_fraction"] <= 0.9
    assert audit.teu_budget == budget
    assert audit.length_slot_budgets == lengths
    targets = [box for box in instance.containers if box.id in instance.load_list]
    assert audit.realised_teu == sum(box.length_ft / 20 for box in targets)
    assert audit.realised_teu <= budget
    assert all(sum(box.length_ft == length for box in targets) <= limit
               for length, limit in lengths.items())
    assert audit.completion_checks >= 1
    assert audit.c2_screened is False
    assert not instances.completion_failures(instance)
    assert 3 <= len(instance.rotation.calls) <= 5
    assert instance.rotation.calls[instance.call_index].locode == instance.port.locode
    assert any(call.locode.startswith("TH") for call in instance.rotation.calls)
    yard_ids = [cid for stack in instance.yard.stacks for cid in stack.boxes]
    yard_ids += instance.yard.quay_buffer
    assert set(instance.load_list) <= set(yard_ids)
    assert len(yard_ids) == len(set(yard_ids))
    assert instance.yard.time_s == 0.0
    assert Instance.model_validate(instance.model_dump()) == instance
    assert not instance.provenance.tainted
    assert audit.attempts >= 1
    assert audit.simulation_statistics["gate_lifts_before_snapshot"] >= len(instance.load_list)
    assert 0 <= audit.screening_rate < 1
    path = save_instance(instance, tmp_path / f"{size}.json")
    assert load_instance(path) == instance


def test_family_and_deterministic_bytes(tmp_path):
    args = dict(seed=13, noise=0.35)
    first = instances.generate_simulated_instance(**args)
    second = instances.generate_simulated_instance(**args)
    assert first.model_dump_json() == second.model_dump_json()
    assert first.voyage is not None and first.voyage == second.voyage
    assert set(first.family) >= {"vessel", "port", "size", "noise", "seed", "generator"}
    assert first.family["vessel"] == "feeder_500"
    assert first.family["port"] == "laem_chabang_like"
    assert first.family["size"] == "tiny"
    assert float(first.family["noise"]) == 0.35
    assert first.family["seed"] == "13"
    assert first.family["generator"]
    a = save_instance(first, tmp_path / "a.json")
    b = save_instance(second, tmp_path / "b.json")
    assert a.read_bytes() == b.read_bytes()


@pytest.mark.parametrize("seed", [-1, 1.5, True, "1", None])
def test_invalid_seed(seed):
    with pytest.raises(ValueError, match="seed"):
        instances.generate_simulated_instance(seed=seed)


@pytest.mark.parametrize("noise", [-0.1, 1.1, float("nan"), float("inf")])
def test_invalid_noise(noise):
    with pytest.raises(ValueError, match="noise"):
        instances.generate_simulated_instance(seed=0, noise=noise)


@pytest.mark.parametrize("attempts", [0, -1, 1.5, True])
def test_invalid_attempt_limit(attempts):
    with pytest.raises(ValueError, match="max_attempts"):
        instances.generate_simulated_instance(seed=0, max_attempts=attempts)


def test_invalid_size():
    with pytest.raises(ValueError, match="size"):
        instances.generate_simulated_instance("unknown", seed=0)


def test_screening_rejections_audited(monkeypatch):
    actual = instances.screen_load_list
    calls = 0

    def reject_first(vessel, boxes, **kwargs):
        nonlocal calls
        calls += 1
        return ["expert.physical.slot_occupancy"] if calls == 1 else actual(vessel, boxes, **kwargs)

    monkeypatch.setattr(instances, "screen_load_list", reject_first)
    audit = instances.GenerationReport()
    instances.generate_simulated_instance(seed=2, report=audit, tiny_dg_enabled=False, n_boxes=3)
    assert calls == audit.attempts == 2
    assert audit.screening_rate == 0.5
    assert audit.rejections == {"expert.physical.slot_occupancy": 1}


def test_screening_exhaustion_audited(monkeypatch):
    monkeypatch.setattr(instances, "screen_load_list", lambda vessel, boxes, **kwargs: ["expert.physical.reefer_plug"])
    audit = instances.GenerationReport()
    with pytest.raises(ValueError, match="exhausted"):
        instances.generate_simulated_instance(seed=2, report=audit, max_attempts=3, tiny_dg_enabled=False, n_boxes=3)
    assert audit.attempts == 3
    assert audit.screening_rate == 1
    assert audit.rejections == {"expert.physical.reefer_plug": 3}


def test_explicit_non_dg_profile_is_audited():
    audit = instances.GenerationReport()
    instance = instances.generate_simulated_instance("small", seed=1, report=audit, without_dg=True)
    assert all(box.dg is None for box in instance.containers)
    assert instance.family["cargo_profile"] == "no_dg"
    assert any("dg" in text.lower() for text in audit.conditioning)


@pytest.mark.parametrize("n_boxes", [0, -1, 1.5, True, "6"])
def test_invalid_target_count(n_boxes):
    with pytest.raises(ValueError, match="n_boxes"):
        instances.generate_simulated_instance(seed=0, n_boxes=n_boxes)


def test_target_override_scales_yard_without_mutating_ladder():
    audit = instances.GenerationReport()
    rung = SIZE_LADDER["small"]
    # An explicit feasible target changes demand, without changing the region.
    instance = instances.generate_simulated_instance("small", seed=8, n_boxes=2,
                                                     report=audit, without_dg=True)
    assert len(instance.load_list) == audit.requested_targets == 2
    assert instance.family["n_boxes"] == "2"
    assert instance.family["size"] == "small"
    assert instance.vessel.slice_of
    assert len(instance.port.yard.blocks) >= 1
    assert SIZE_LADDER["small"] is rung
    assert rung.boxes == 36
    assert any("physical capacity unchanged" in item for item in audit.conditioning)


def test_default_dg_prevalence_is_retained(monkeypatch):
    actual_generate = instances.generate_load_list
    eligible_shares = []

    def record_share(*args, **kwargs):
        if len(kwargs["params"]["equipment"]) > 1:
            eligible_shares.append(kwargs["params"]["dg"]["share_of_eligible_loaded_boxes"]["value"])
        return actual_generate(*args, **kwargs)

    monkeypatch.setattr(instances, "generate_load_list", record_share)
    instance = instances.generate_simulated_instance("small", seed=12)
    assert eligible_shares and all(share > 0 for share in eligible_shares)
    assert instance.family["cargo_profile"] == "feeder_export"


def test_explicit_tiny_eligibility_conditioning():
    audit = instances.GenerationReport()
    instance = instances.generate_simulated_instance("tiny", seed=3, report=audit, tiny_dg_enabled=False, n_boxes=3)
    assert all(box.empty and box.length_ft == 20 and box.dg is None for box in instance.containers)
    assert any("ineligible for DG sampling" in item for item in audit.conditioning)


@pytest.mark.parametrize("dense_20ft", [1, "yes", None])
def test_invalid_dense_profile(dense_20ft):
    with pytest.raises(ValueError, match="dense_20ft"):
        instances.generate_simulated_instance(seed=0, dense_20ft=dense_20ft)


def test_dense_profile_retains_dg_eligibility_and_loaded_share(monkeypatch):
    actual_generate = instances.generate_load_list
    recorded = []

    def record(*args, **kwargs):
        params = kwargs["params"]
        if kwargs.get("id_offset", 0) == 0:
            recorded.append(params)
        return actual_generate(*args, **kwargs)

    monkeypatch.setattr(instances, "generate_load_list", record)
    audit = instances.GenerationReport()
    instance = instances.generate_simulated_instance("small", seed=4, dense_20ft=True, report=audit)
    assert all(box.length_ft == 20 for box in instance.containers)
    assert any(not box.empty for box in instance.containers if box.id in instance.load_list)
    assert all(p["dg"]["share_of_eligible_loaded_boxes"]["value"] > 0 for p in recorded)
    assert all(p["equipment"][0]["empty_share"]["value"] == 0.2 for p in recorded)
    assert instance.family["equipment_profile"] == "dense_20ft"
    assert any("eligible DG probability retained" in item for item in audit.conditioning)


def test_small_port_preserves_geometry_and_expands_synthetic_footprint():
    audit = instances.GenerationReport()
    instance = instances.generate_simulated_instance("full", seed=5, port_name="small_feeder",
                                                     n_boxes=40, dense_20ft=True, report=audit)
    assert len(instance.load_list) == 40
    assert all(block.n_bays <= 20 and block.n_rows <= 4 for block in instance.port.yard.blocks)
    assert len(instance.port.yard.blocks) >= SIZE_LADDER["full"].yard_blocks


@pytest.mark.parametrize("vessel_name", ["feeder_500", "feeder_800", "feeder_1700"])
def test_actual_kcs_tiny_low_crane_port(vessel_name, monkeypatch):
    # Keep the production tiny selector intact; only hull assembly is omitted.
    geometry = load_fleet_preset(vessel_name, "full", with_hull=False)
    monkeypatch.setattr(instances, "_full_vessel", lambda name: geometry)
    instance = instances.generate_simulated_instance("tiny", seed=3, vessel_name=vessel_name,
                                                     port_name="small_feeder")
    assert len(instance.vessel.cells) == 6
    assert len(instance.load_list) == int(instance.family["n_boxes"])
    assert instance.family["region_capacity_teu"] == "6"
    assert instance.family["booking_policy"] == "D36_scaled_full_export_v1"
    assert 0.75 <= float(instance.family["booking_utilisation_mass"]) <= 0.9
    assert len(instance.vessel.stacks) == 4
    assert sum(not stack.on_deck for stack in instance.vessel.stacks) == 2
    assert sorted(len(stack.cell_ids) for stack in instance.vessel.stacks) == [1, 1, 2, 2]
    assert len(instance.vessel.hatch_covers) == 1
    original = {cell.id: cell for cell in geometry.cells}
    assert all(cell == original[cell.id] for cell in instance.vessel.cells)
    assert instance.family["vessel"] == vessel_name


@pytest.mark.parametrize("tiny_dg_enabled", [1, "yes", None])
def test_invalid_tiny_dg_enabled(tiny_dg_enabled):
    with pytest.raises(ValueError, match="tiny_dg_enabled"):
        instances.generate_simulated_instance(seed=0, tiny_dg_enabled=tiny_dg_enabled)


def test_tiny_dg_profile_retains_original_sampling_and_determinism(monkeypatch):
    actual_generate = instances.generate_load_list
    samples = []

    def record(*args, **kwargs):
        if kwargs.get("id_offset", 0) == 0:
            samples.append(kwargs["params"])
        return actual_generate(*args, **kwargs)

    monkeypatch.setattr(instances, "generate_load_list", record)
    audit = instances.GenerationReport()
    first = instances.generate_simulated_instance("tiny", seed=17, tiny_dg_enabled=True, report=audit)
    second = instances.generate_simulated_instance("tiny", seed=17, tiny_dg_enabled=True)
    assert first.model_dump_json() == second.model_dump_json()
    assert samples and all(p["equipment"][0]["empty_share"]["value"] == 0.2 for p in samples)
    assert all(len(p["equipment"]) == 1 and p["equipment"][0]["id"] == "dry_20" for p in samples)
    assert all(p["dg"]["share_of_eligible_loaded_boxes"]["value"] > 0 for p in samples)
    assert first.family["equipment_profile"] == "dry20_with_dg"
    assert any("tiny conditions equipment" in item and "DG probability retained" in item for item in audit.conditioning)


def test_depth_zero_rejection_counted_per_reason(monkeypatch):
    calls = 0

    def reject_first(instance):
        nonlocal calls
        calls += 1
        return ("c1c",) if calls == 1 else ()

    monkeypatch.setattr(instances, "completion_failures", reject_first)
    audit = instances.GenerationReport()
    instance = instances.generate_simulated_instance(seed=2, report=audit, tiny_dg_enabled=False, n_boxes=3)
    assert len(instance.load_list) == audit.requested_targets
    assert audit.accepted and audit.attempts == 2
    assert audit.completion_checks == calls == 2
    assert audit.completion_rejections == {"c1c": 1}
    assert audit.rejections == {"completion.c1": 1}
    assert audit.screening_rate == 0.5
    assert audit.c2_screened is False


def test_completion_exhaustion_is_audited(monkeypatch):
    monkeypatch.setattr(instances, "completion_failures", lambda instance: ("c1c", "c1f"))
    audit = instances.GenerationReport()
    with pytest.raises(ValueError, match="c1c"):
        instances.generate_simulated_instance(seed=2, report=audit, max_attempts=3, tiny_dg_enabled=False, n_boxes=3)
    assert audit.attempts == audit.completion_checks == 3
    assert not audit.accepted and audit.screening_rate == 1
    assert audit.completion_rejections == {"c1c": 3, "c1f": 3}
    assert audit.rejections == {"completion.c1": 3}
    assert audit.c2_screened is False


def test_completion_configuration_omits_c2_and_action_search(monkeypatch):
    import stowage.feasibility as feasibility

    observed = {}

    class Engine:
        def completion_check(self):
            return {"c1a": True, "c1c": False, "c2": False}

        def dead_end(self):
            pytest.fail("depth-zero completion screen must not enumerate actions")

    def make(instance, space, *, config):
        observed["config"] = config
        return Engine()

    instance = instances.generate_simulated_instance(seed=2)
    monkeypatch.setattr(feasibility, "make_feasibility_engine", make)
    assert instances.completion_failures(instance) == ("c1c",)
    assert observed["config"].lookahead == 0
    assert observed["config"].extra["global_screen"] == 0


def test_rung_split_identity_is_unchanged():
    from eval.splits import InstanceSpec, spec_from_instance, spec_key, split_of

    instance = instances.generate_simulated_instance("small", seed=1, without_dg=True)
    expected = InstanceSpec(generator="simulated", rung="small", vessel="feeder_500",
                            slice="small", port="laem_chabang_like", noise=0.2,
                            blocking_ratio=None, seed=1, cargo_profile="no_dg")
    actual = spec_from_instance(instance)
    assert spec_key(actual) == spec_key(expected)
    assert split_of(actual) == split_of(expected)


def test_individually_impossible_dg_shipment_redraw_keeps_other_bookings(monkeypatch):
    from stowage.model import DangerousGoods

    actual_generate = instances.generate_load_list
    candidates = []
    original_condition = instances.recondition_slice
    reservations = []

    def cargo(*args, **kwargs):
        boxes = actual_generate(*args, **kwargs)
        if kwargs.get("id_offset", 0) == 0 and len(boxes) > 1:
            boxes[0] = boxes[0].model_copy(update={
                "dg": DangerousGoods(un_number="0336", imdg_class="1.4G", stowage_category="02"),
                "empty": False,
                "gross_mass_kg": boxes[0].tare_kg + 1000,
            })
        return boxes

    def condition(vessel, *, expected_load_mass_kg, seed):
        assert isinstance(seed, int) and seed >= 0
        assert len(vessel.cells) == 6
        reservations.append(expected_load_mass_kg)
        return original_condition(vessel, expected_load_mass_kg=expected_load_mass_kg, seed=seed)

    def completion(instance):
        targets = [b for b in instance.containers if b.id in instance.load_list]
        if len(targets) > 1:
            candidates.append({b.id: b.model_dump() for b in targets})
        return ("c1a",) if any(b.dg is not None for b in targets) else ()

    monkeypatch.setattr(instances, "generate_load_list", cargo)
    monkeypatch.setattr(instances, "screen_load_list", lambda *args, **kwargs: [])
    monkeypatch.setattr(instances, "completion_failures", completion)
    monkeypatch.setattr(instances, "recondition_slice", condition)
    audit = instances.GenerationReport()
    # Explicit quantity isolates shipment-redraw accounting from D36's
    # production mass/utilisation conditioning; the fixture changes cargo mass.
    instance = instances.generate_simulated_instance(seed=3, report=audit, tiny_dg_enabled=False, n_boxes=3)
    assert audit.accepted and audit.attempts == 2
    assert audit.shipment_redraws == 1
    assert audit.completion_rejections == {"c1a": 1}
    assert audit.completion_checks == 3  # two whole candidates and the DG-only probe
    assert len(candidates) == 2
    first, second = candidates
    changed = [cid for cid in first if first[cid] != second[cid]]
    assert len(changed) == 1
    assert first[changed[0]]["dg"] is not None
    assert second[changed[0]]["dg"] is None
    assert set(instance.load_list) == set(first) == set(second)


    assert len(reservations) == 2
    assert reservations == [sum(box["gross_mass_kg"] for box in candidate.values())
                            for candidate in candidates]
    assert reservations[0] - reservations[1] == 1000


@pytest.mark.parametrize("size", ["tiny", "small"])
def test_candidate_condition_uses_only_booking_mass_and_departure_tanks(
    size, monkeypatch, geometry,
):
    """A rejected booking must not leave the next draw's ship condition stale."""
    from stowage.vessel.fleet_conditions import departure_tank_masses
    from stowage.model.fixtures import build_instance
    from stowage.vessel.fleet_voyage import build_voyage_policy

    # Geometry-only fleet fixtures omit tanks; reuse validated model fixtures.
    diagnostic_vessel = build_instance().vessel
    diagnostic_tanks = diagnostic_vessel.tanks
    # D50 binds consumable membership to the actual tank catalog. Replace the
    # geometry-only empty policy together with the diagnostic tanks.
    diagnostic_policy = build_voyage_policy(
        diagnostic_tanks, parameter_source="synthetic model fixture",
        vessel_id=diagnostic_vessel.id,
    )

    original_generate = instances.generate_load_list
    bookings = []
    conditioned = []
    tank_states = []
    screened = []
    completed = []
    marker = "test candidate condition:"

    def cargo(*args, **kwargs):
        boxes = original_generate(*args, **kwargs)
        if kwargs.get("id_offset", 0) == 0:
            # Different booking masses make stale conditioning observable.
            boxes = [box.model_copy(update={
                "gross_mass_kg": box.tare_kg + 1000.0 * (len(bookings) + 1),
                "empty": False,
            }) for box in boxes]
            bookings.append(boxes)
        return boxes

    def condition(selected, *, expected_load_mass_kg, seed):
        assert isinstance(seed, int) and seed >= 0
        assert not any(source.startswith(marker) for source in selected.sources)
        expected = instances._decision_region(geometry, size)
        if size == "tiny":
            expected = instances._tiny_region(geometry)
        assert selected.cells == expected.cells
        assert selected.stacks == expected.stacks
        assert expected_load_mass_kg == sum(box.gross_mass_kg for box in bookings[-1])
        result = selected.model_copy(update={
            "sources": [*selected.sources, f"{marker}{len(conditioned)}"],
            "tanks": diagnostic_tanks,
            "voyage_policy": diagnostic_policy,
        }, deep=True)
        conditioned.append(result)
        return result

    def tanks(vessel):
        assert vessel is conditioned[-1]
        # Use valid live-tank ids and deliberately distinct contents per draw.
        masses = {key: value * (1.0 - len(tank_states) / 100)
                  for key, value in departure_tank_masses(vessel).items()}
        tank_states.append(masses)
        return masses

    def screen(vessel, boxes, **kwargs):
        assert vessel is conditioned[-1]
        assert kwargs["tank_masses_kg"] == tank_states[-1]
        assert [box.id for box in boxes] == [box.id for box in bookings[-1]]
        screened.append(vessel)
        return []

    def complete(instance):
        assert instance.vessel == conditioned[-1]
        assert instance.onboard.tank_masses_kg == tank_states[-1]
        targets = [box for box in instance.containers if box.id in instance.load_list]
        background = [box for box in instance.containers if box.id not in instance.load_list]
        assert background  # Yard background exists, but does not enter the reservation.
        assert sum(box.gross_mass_kg for box in targets) == sum(
            box.gross_mass_kg for box in bookings[-1])
        completed.append(instance)
        return ("c1c",) if len(completed) == 1 else ()

    monkeypatch.setattr(instances, "generate_load_list", cargo)
    monkeypatch.setattr(instances, "recondition_slice", condition)
    monkeypatch.setattr(instances, "departure_tank_masses", tanks)
    monkeypatch.setattr(instances, "screen_load_list", screen)
    monkeypatch.setattr(instances, "completion_failures", complete)
    report = instances.GenerationReport()
    instance = instances.generate_simulated_instance(
        size, seed=23, report=report, without_dg=True, dense_20ft=True,
        tiny_dg_enabled=False, n_boxes=3, max_attempts=3,
    )
    assert report.attempts == 2
    assert len(bookings) == len(conditioned) == len(tank_states) == len(screened) == len(completed) == 2
    assert tank_states[0] != tank_states[1]
    assert instance.vessel == conditioned[1]
    assert instance.onboard.tank_masses_kg == tank_states[1]
    assert report.completion_rejections == {"c1c": 1}


def test_balancing_failure_rejects_booking_then_preserves_family(monkeypatch):
    actual_condition = instances.recondition_slice
    actual_cargo = instances.generate_load_list
    conditioning_seeds = []
    booking_seeds = []

    def cargo(*args, **kwargs):
        if kwargs.get("id_offset", 0) == 0:
            booking_seeds.append(kwargs["seed"])
        return actual_cargo(*args, **kwargs)

    def condition(vessel, **kwargs):
        conditioning_seeds.append(kwargs["seed"])
        if len(conditioning_seeds) == 1:
            raise ValueError("cannot balance slice background within physical cargo capacities: test booking")
        return actual_condition(vessel, **kwargs)

    monkeypatch.setattr(instances, "generate_load_list", cargo)
    monkeypatch.setattr(instances, "recondition_slice", condition)
    audit = instances.GenerationReport(balancing_rejections=99)
    instance = instances.generate_simulated_instance(
        seed=3, report=audit, tiny_dg_enabled=False, n_boxes=3,
    )
    assert audit.accepted and audit.attempts == 2
    assert audit.balancing_rejections == 1
    assert audit.rejections == {"expert.physical.slot_occupancy": 1}
    assert audit.completion_checks == 1
    assert audit.screening_rate == 0.5
    identity = instance.family["generation_identity"]
    assert booking_seeds == [instances._stream_seed(identity, "cargo", attempt=attempt)
                             for attempt in range(2)]
    assert conditioning_seeds == [instances._stream_seed(identity, "slice", attempt=attempt)
                                  for attempt in range(2)]
    assert instance.seed == 3
    assert instance.family["seed"] == "3"
    assert instance.family["size"] == "tiny"
    assert instance.family["vessel"] == "feeder_500"
    assert instance.family["port"] == "laem_chabang_like"
    assert instance.family["generator"] == instances.INSTANCES_VERSION
    assert instance.provenance.versions["instances"] == instances.INSTANCES_VERSION


@pytest.mark.parametrize("size", ["tiny", "medium"])
def test_balancing_failure_exhaustion_is_counted_without_running_later_screens(monkeypatch, size):
    def condition(*args, **kwargs):
        raise ValueError("cannot balance slice background within physical cargo capacities: test booking")

    def unexpected(*args, **kwargs):
        pytest.fail("an unbalanced background must not reach downstream screens")

    monkeypatch.setattr(instances, "recondition_slice", condition)
    monkeypatch.setattr(instances, "screen_load_list", unexpected)
    monkeypatch.setattr(instances, "completion_failures", unexpected)
    audit = instances.GenerationReport()
    with pytest.raises(ValueError, match="screening exhausted 3 attempts"):
        instances.generate_simulated_instance(
            size, seed=3, report=audit, max_attempts=3, tiny_dg_enabled=False, n_boxes=3,
        )
    assert audit.attempts == audit.balancing_rejections == 3
    assert audit.rejections == {"expert.physical.slot_occupancy": 3}
    assert audit.screening_rate == 1
    assert not audit.accepted
    assert audit.completion_checks == 0
    assert audit.completion_rejections == {}
    assert audit.timings_s["screening_s"] > 0
    assert audit.timings_s["total_s"] >= audit.timings_s["screening_s"]


@pytest.mark.parametrize("message", [
    "unrecognized vessel parameters",
    "cannot balance slice background: unrelated failure",
    "wrapper: cannot balance slice background within physical cargo capacities",
])
def test_other_reconditioning_value_errors_are_not_resampled(monkeypatch, message):
    error = ValueError(message)

    def condition(*args, **kwargs):
        raise error

    monkeypatch.setattr(instances, "recondition_slice", condition)
    audit = instances.GenerationReport()
    with pytest.raises(ValueError) as raised:
        instances.generate_simulated_instance(seed=3, report=audit, tiny_dg_enabled=False, n_boxes=3)
    assert raised.value is error
    assert audit.attempts == 1
    assert audit.balancing_rejections == 0
    assert audit.rejections == {}


def test_generation_report_preserves_every_random_producer_field():
    from dataclasses import asdict
    from stowage.generate.random_yard import RandomGenerationReport

    producer = asdict(RandomGenerationReport())
    adapter = asdict(instances.GenerationReport())
    assert producer.keys() <= adapter.keys()
    assert 'producer_report' in adapter


@pytest.mark.parametrize('enabled', [False, True])
def test_simulated_effective_versions_and_active_cargo_audit(enabled):
    from stowage.rules.catalogue import rule_freeze_v1
    from stowage.generate.cargo import effective_cargo_generator_version
    from stowage.yard import effective_yard_simulator_version

    report = instances.GenerationReport()
    with rule_freeze_v1(enabled=enabled):
        result = instances.generate_simulated_instance(seed=3, n_boxes=3, without_dg=True, report=report)
        assert result.provenance.versions['cargo_generator'] == effective_cargo_generator_version()
        assert result.provenance.versions['yard_simulator'] == effective_yard_simulator_version()
    if enabled:
        assert 'before simulated-instance screening' in report.cargo_draws['emission_definition']
        assert report.cargo_draws['stages']['emitted']['n_boxes'] >= 3
        assert report.cargo_draws['admitted']['n_boxes'] == len(result.load_list)
        assert all(call.archetype for call in result.rotation.calls)
        assert result.family['generator_kind'] == 'simulated'
    else:
        assert report.cargo_draws == {}


def test_simulated_dg_stress_profile_is_separate_and_binds_before_screen(monkeypatch):
    from stowage.rules.catalogue import rule_freeze_v1
    from stowage.generate.random_cargo import RandomCargoDraws

    bound = []
    actual = RandomCargoDraws.bind
    def bind(draws, candidate):
        result = actual(draws, candidate)
        bound.append(result)
        return result
    def screen(vessel, boxes, **kwargs):
        assert bound and all(box.dg is not None for box in boxes)
        assert {box.id for box in boxes} <= {box.id for box in bound[-1].containers}
        return []
    monkeypatch.setattr(RandomCargoDraws, 'bind', bind)
    monkeypatch.setattr(instances, 'screen_load_list', screen)
    monkeypatch.setattr(instances, 'completion_failures', lambda candidate: ())
    report = instances.GenerationReport()
    with rule_freeze_v1(enabled=True):
        result = instances.generate_simulated_instance('small', seed=3, n_boxes=3,
            cargo_profile='dg_stress', report=report)
    assert result.family['cargo_profile'] == 'dg_stress'
    assert result.provenance.versions['cargo_profile'] == 'dg_stress'
    assert len(bound) >= 2 and bound[-1] == result
    assert all(box.dg for box in result.containers if box.id in result.load_list)
    assert report.cargo_draws['admitted']['n_boxes'] == 3


@pytest.mark.parametrize('enabled', [False, True])
def test_simulated_medium_requests_necessary_c2_only_when_active(monkeypatch, enabled):
    from stowage.rules.catalogue import rule_freeze_v1
    from stowage.generate import random_screen

    seen = []
    def necessary(candidate, *, diagnostics):
        seen.append(candidate)
        return ()
    monkeypatch.setattr(random_screen, 'necessary_completion_failures', necessary)
    monkeypatch.setattr(instances, 'completion_failures', lambda candidate: ())
    report = instances.GenerationReport()
    with rule_freeze_v1(enabled=enabled):
        instances.generate_simulated_instance('medium', seed=3, n_boxes=3,
            without_dg=True, report=report)
    assert report.c2_requested is enabled
    assert report.c2_screened is enabled
    assert report.c2_checks == int(enabled)
    assert len(seen) == int(enabled)


def test_simulated_off_state_seeded_bytes_match_pre_migration(repo_root):
    from tests.generate.test_instances_off_compat import (
        current_code_frozen_input_digest, HISTORICAL_OUTPUT_SHA)

    # Freeze dependencies/inputs as well as the original digest: new authored
    # input facts and their hashes must not be falsely erased from provenance.
    assert current_code_frozen_input_digest(repo_root) == HISTORICAL_OUTPUT_SHA


def test_simulated_off_current_inputs_match_historical_producer(repo_root, monkeypatch, geometry):
    from tests.generate.test_instances_off_compat import historical_producer
    from stowage.rules.catalogue import rule_freeze_v1

    historical = historical_producer(repo_root, monkeypatch)
    monkeypatch.setattr(historical, '_full_vessel', lambda name: geometry)
    monkeypatch.setattr(historical, 'screen_load_list', instances.screen_load_list)
    monkeypatch.setattr(historical, 'accept_background_candidate', lambda candidate, audit: candidate)
    with rule_freeze_v1(enabled=False):
        for size, seed in [('tiny', 3), ('small', 7), ('medium', 13)]:
            current = instances.generate_simulated_instance(size, seed=seed, n_boxes=3,
                without_dg=True).model_dump_json()
            legacy = historical.generate_simulated_instance(size, seed=seed, n_boxes=3,
                without_dg=True).model_dump_json()
            assert current == legacy


@pytest.mark.parametrize('failure', ['c2', 'c2.unavailable'])
def test_simulated_active_medium_c2_failure_is_audited(monkeypatch, failure):
    from stowage.rules.catalogue import rule_freeze_v1
    from stowage.generate import random_screen

    def necessary(candidate, *, diagnostics):
        diagnostics['error'] = 'test missing model'
        return (failure,)
    monkeypatch.setattr(random_screen, 'necessary_completion_failures', necessary)
    monkeypatch.setattr(instances, 'completion_failures', lambda candidate: ())
    report = instances.GenerationReport()
    with rule_freeze_v1(enabled=True), pytest.raises(ValueError,
            match='C2 unavailable' if failure == 'c2.unavailable' else 'screening exhausted'):
        instances.generate_simulated_instance('medium', seed=3, n_boxes=3,
            without_dg=True, report=report, max_attempts=2)
    assert report.c2_requested and report.c2_screened and not report.accepted
    assert report.c2_direction == 'necessary'
    assert report.c2_checks == (1 if failure == 'c2.unavailable' else 2)
    assert report.completion_rejections[failure] == report.c2_checks
    assert 'admitted' not in report.cargo_draws
    if failure == 'c2.unavailable':
        assert report.c2_errors == {'test missing model': 1}
        assert report.rejections['checker.missing_vessel_data'] == 1
    else:
        assert report.rejections['completion.c2'] == 2


def test_simulated_direct_delivery_survives_snapshot_without_yard_position(monkeypatch):
    from stowage.rules.catalogue import rule_freeze_v1
    from stowage.generate import cargo

    original = cargo.dg_stress_profile
    def direct_profile():
        profile = original()
        profile['dg']['classes'] = [row for row in profile['dg']['classes'] if row['id'] == '1.4S']
        profile['dg']['classes'][0]['share']['value'] = 1.0
        return profile
    monkeypatch.setattr(cargo, 'dg_stress_profile', direct_profile)
    # This adapter test keeps physical/full-checker admission separate. The DGL
    # fixture retains unknown SP364 and no placement permission is invented.
    monkeypatch.setattr(instances, 'screen_load_list', lambda *args, **kwargs: [])
    monkeypatch.setattr(instances, 'completion_failures', lambda candidate: ())
    with rule_freeze_v1(enabled=True):
        result = instances.generate_simulated_instance('small', seed=3, n_boxes=3,
            cargo_profile='dg_stress', max_attempts=2)
    selected = [box for box in result.containers if box.id in result.load_list]
    assert len(selected) == 3
    assert all(box.direct_delivery and box.dg.imdg_class == '1.4S' for box in selected)
    assert not set(result.load_list) & {cid for stack in result.yard.stacks for cid in stack.boxes}


def test_simulated_active_audit_retains_rejected_draws_separately_from_admitted(monkeypatch):
    from stowage.rules.catalogue import rule_freeze_v1

    count = 0
    def reject_first(*args, **kwargs):
        nonlocal count
        count += 1
        return ['test.rejected'] if count == 1 else []
    monkeypatch.setattr(instances, 'screen_load_list', reject_first)
    monkeypatch.setattr(instances, 'completion_failures', lambda candidate: ())
    report = instances.GenerationReport()
    with rule_freeze_v1(enabled=True):
        instances.generate_simulated_instance(seed=3, n_boxes=3, without_dg=True, report=report)
    assert report.attempts == 2
    assert report.cargo_draws['initial_requested']['n_boxes'] == 8  # 2 bookings plus accepted background
    assert report.cargo_draws['stages']['emitted']['n_boxes'] == 8
    assert report.cargo_draws['admitted']['n_boxes'] == 3
    assert report.rejections == {'test.rejected': 1}


def test_simulated_campaign_identity_is_bound_before_every_screen(monkeypatch):
    from stowage.rules.catalogue import rule_freeze_v1
    from stowage.generate import cargo
    from stowage.rules.imdg.scenarios import context_for, provenance_matches

    original = cargo.dg_stress_profile
    def h1_profile():
        profile = original()
        profile['dg']['classes'] = [row for row in profile['dg']['classes'] if row['id'] == '4.3']
        profile['dg']['classes'][0]['share']['value'] = 1.0
        return profile
    monkeypatch.setattr(cargo, 'dg_stress_profile', h1_profile)
    monkeypatch.setattr(instances, 'screen_load_list', lambda *args, **kwargs: [])
    observed = []
    def completion(candidate):
        assert candidate.id == 'v3.simulated_yard.small.3'
        config = candidate.rule_overrides['imdg.stowage.3_2']
        for box in candidate.containers:
            if box.dg is None:
                continue
            assessment = config['containers'][box.id]['entry_assessments']['primary']['h1']
            source = assessment['provenance']
            registered = config['assessment_scenarios'][source['scenario_id']]
            records = registered if isinstance(registered, (list, tuple)) else [registered]
            matching = [record for record in records if record['container_id'] == box.id
                        and record['entry_path'] == 'primary']
            assert matching
            for record in matching:
                context = context_for(candidate, box, 'primary', record['allowed_slots'][0])
                assert provenance_matches(source, context, config['assessment_scenarios'])
                assert record['instance_id'] == candidate.id
        observed.append(candidate)
        return ()
    monkeypatch.setattr(instances, 'completion_failures', completion)
    with rule_freeze_v1(enabled=True):
        result = instances.generate_simulated_instance('small', seed=3, n_boxes=3,
            cargo_profile='dg_stress', instance_id='v3.simulated_yard.small.3')
    assert observed and observed[-1] == result


def test_simulated_d72_preserves_full_detached_evidence(monkeypatch):
    from dataclasses import asdict
    import json
    from stowage.rules.catalogue import rule_freeze_v1

    observed = []
    original = instances.simulate_port
    def simulate(*args, **kwargs):
        assert kwargs['congestion_mix_share'] == 0.25
        result = original(*args, **kwargs)
        # A future simulator audit field must survive without adapter edits.
        result.provenance.congestion_mixing['future_counter'] = 17
        observed.append(result)
        return result
    monkeypatch.setattr(instances, 'simulate_port', simulate)
    report = instances.GenerationReport()
    with rule_freeze_v1(enabled=True):
        result = instances.generate_simulated_instance(seed=3, n_boxes=3,
            without_dg=True, report=report)
    expected = deepcopy(observed[-1].provenance.congestion_mixing)
    assert asdict(report)['congestion_mixing'] == expected
    evidence = next(value.removeprefix('yard.simulator.congestion_mixing:')
                    for value in result.provenance.parents
                    if value.startswith('yard.simulator.congestion_mixing:'))
    assert json.loads(evidence) == expected
    assert result.family['congestion_mix_share'] == repr(expected['congestion_mix_share'])
    assert result.family['achieved_congestion_mix_share'] == repr(expected['achieved_share'])
    assert Instance.model_validate_json(result.model_dump_json()).provenance == result.provenance
    observed[-1].provenance.congestion_mixing['future_counter'] = 99
    assert report.congestion_mixing == expected
    report.congestion_mixing['future_counter'] = 101
    assert json.loads(next(value.removeprefix('yard.simulator.congestion_mixing:')
        for value in result.provenance.parents
        if value.startswith('yard.simulator.congestion_mixing:'))) == expected


@pytest.mark.parametrize('enabled', [False, True])
def test_simulated_d72_share_changes_identity_only_when_active(monkeypatch, enabled):
    from stowage.rules.catalogue import rule_freeze_v1

    observed = []
    original = instances.simulate_port
    def simulate(*args, **kwargs):
        observed.append(kwargs)
        return original(*args, **kwargs)
    monkeypatch.setattr(instances, 'simulate_port', simulate)
    with rule_freeze_v1(enabled=enabled):
        first = instances.generate_simulated_instance(seed=3, n_boxes=3,
            without_dg=True, congestion_mix_share=0.25)
        if enabled:
            second = instances.generate_simulated_instance(seed=3, n_boxes=3,
                without_dg=True, congestion_mix_share=0.0)
        else:
            with pytest.raises(ValueError, match='congestion_mix_share requires'):
                instances.generate_simulated_instance(seed=3, n_boxes=3,
                    without_dg=True, congestion_mix_share=0.0)
    if enabled:
        assert first.family['generation_identity'] != second.family['generation_identity']
        assert first.family['congestion_mix_share'] == '0.25'
        assert second.family['congestion_mix_share'] == '0.0'
        assert {call['congestion_mix_share'] for call in observed} == {0.25, 0.0}
    else:
        assert all('congestion_mix_share' not in call for call in observed)
        assert 'congestion_mix_share' not in first.family


@pytest.mark.parametrize('share', [-0.1, 1.1, float('nan'), float('inf'), True, '0.25'])
def test_simulated_d72_invalid_share(share):
    with pytest.raises(ValueError, match='congestion_mix_share'):
        instances.generate_simulated_instance(seed=3, congestion_mix_share=share)


@pytest.mark.parametrize('enabled', [False, True])
def test_simulated_foreign_yard_nondg_limit_is_labelled_in_provenance(enabled):
    from stowage.rules.catalogue import rule_freeze_v1

    with rule_freeze_v1(enabled=enabled):
        result = instances.generate_simulated_instance(seed=3, n_boxes=3, without_dg=True)
    if enabled:
        assert ('APPROXIMATION: foreign yard boxes are non-DG; yard DG segregation not modelled'
                in result.provenance.parents)
        assert all(box.dg is None for box in result.containers if box.id not in result.load_list)
    else:
        assert not any('foreign yard boxes' in value for value in result.provenance.parents)
