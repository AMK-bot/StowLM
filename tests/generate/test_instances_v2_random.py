"""Adapter tests exercise real KCS geometry, not a fabricated permissive hull."""
from dataclasses import asdict

import pytest

from stowage.generate import instances_v2_random as adapter
from stowage.generate import random_yard
from stowage.generate.instances import GenerationReport
from stowage.model import Instance
from stowage.model.vessel import Tank
from stowage.params import load_param_file
from stowage.ports.profiles import effective_yard_tiers, load_port
from stowage.vessel.fleet_slices import load_fleet_preset, slice_preset
from stowage.vessel.fleet_voyage import build_voyage_policy


@pytest.mark.parametrize('enabled', [False, True])
def test_adapter_preserves_unknown_gate_counts_without_redraw(monkeypatch, enabled):
    from stowage.generate.background_acceptance import BackgroundAcceptance, BackgroundAcceptanceUnknown
    from stowage.rules.catalogue import rule_freeze_v1

    calls = []
    def unknown(size, *, report, **kwargs):
        calls.append(size)
        report.attempts = 1
        report.background_unknowns = {'background.compute.deadline': 1}
        report.background_last = {'status': 'unknown'}
        raise BackgroundAcceptanceUnknown(BackgroundAcceptance(False,
            ('background.compute.deadline',), 'unknown', {}, {}, 0., unknown=True))
    monkeypatch.setattr(random_yard, 'generate_random_instance', unknown)
    report = GenerationReport()
    with rule_freeze_v1(enabled=enabled), pytest.raises(BackgroundAcceptanceUnknown):
        adapter.generate_random_v2('tiny', seed=3, noise=.2, vessel_name='feeder_500',
            port_name='laem_chabang_like', report=report, max_attempts=50)
    assert calls == ['tiny']
    assert report.attempts == 1 and not report.accepted
    assert asdict(report)['background_unknowns'] == {'background.compute.deadline': 1}
    assert report.background_last == {'status': 'unknown'}
    assert report.background_rejections == {} and report.rejections == {}

@pytest.fixture(scope="module")
def geometry():
    return load_fleet_preset("feeder_500", "full", with_hull=False)


def test_tiny_region_reciprocal_and_original_untouched(geometry):
    before = geometry.model_dump()
    tiny = adapter.select_tiny_region(geometry)
    assert len(tiny.cells) == 6
    assert all(cell.accepts_20 for cell in tiny.cells)
    assert all(s.cell_ids == next(t.cell_ids for t in geometry.stacks if t.id == s.id)[:len(s.cell_ids)] for s in tiny.stacks)
    assert geometry.model_dump() == before
    assert tiny.fixed_loads == geometry.fixed_loads
    assert tiny.hull is None  # this diagnostic never supplies invented physics


@pytest.fixture
def diagnostic(monkeypatch, geometry):
    monkeypatch.setattr(random_yard, "_full_vessel", lambda name: geometry)
    actual = adapter.screen_load_list
    # Geometry-only diagnostic explicitly excludes missing hull; other rules remain.
    monkeypatch.setattr(adapter, "screen_load_list", lambda *args, **kwargs: [r for r in actual(*args, **kwargs) if r != "checker.missing_vessel_data"])


def test_generator_restores_original_module_and_determinism(diagnostic):
    before = {name: getattr(random_yard, name) for name in ("SIZE_LADDER", "_tiny_region", "screen_load_list", "load_param_file", "slice_preset", "scaled_yard", "capacity_load_size")}
    audit = GenerationReport()
    kwargs = dict(seed=3, noise=.3, vessel_name="feeder_500", port_name="small_feeder", report=audit)
    first = adapter.generate_random_v2("tiny", **kwargs)
    first_audit = (audit.attempts, dict(audit.rejections), dict(audit.completion_rejections), audit.shipment_redraws)
    second = adapter.generate_random_v2("tiny", **kwargs)
    assert first.model_dump() == second.model_dump()
    assert Instance.model_validate(first.model_dump()) == first
    assert first.voyage is not None
    assert Instance.model_validate_json(first.model_dump_json()) == first
    assert first.voyage.consumption is None
    assert first.voyage.ballast_operations == []
    assert first.voyage.departure.call_index == first.call_index
    assert first.voyage.arrival.call_index == first.call_index + 1
    assert first.voyage.departure.locode == first.rotation.calls[first.call_index].locode
    assert first.voyage.arrival.locode == first.rotation.calls[first.call_index + 1].locode
    assert first.voyage.departure.id != first.voyage.arrival.id
    assert len(first.load_list) == audit.requested_targets
    assert audit.accepted and audit.attempts >= 1
    assert first_audit == (audit.attempts, audit.rejections, audit.completion_rejections, audit.shipment_redraws)
    assert audit.capacity_teu == 6
    assert audit.teu_budget == audit.length_slot_budgets[20] == 5
    assert audit.realised_teu == len(first.load_list)
    assert audit.booking_reference["policy"] == "D36_scaled_full_export_v1"
    assert audit.booking_reference["region_capacity_teu"] == audit.capacity_teu
    assert audit.booking_reference["requested_teu_fraction"] == pytest.approx(
        float(first.family["booking_requested_teu_fraction"]))
    assert audit.booking_utilisation["teu_fraction"] == pytest.approx(
        audit.realised_teu / audit.capacity_teu)
    assert 0.75 <= audit.booking_utilisation["cargo_mass_fraction"] <= 0.90
    assert asdict(audit)["booking_reference"] == audit.booking_reference
    assert asdict(audit)["booking_utilisation"] == audit.booking_utilisation
    assert audit.completion_checks >= 1 and audit.c2_screened is False
    assert first.family["n_boxes"] == str(len(first.load_list))
    assert all(getattr(random_yard, name) is value for name, value in before.items())
    assert any("loaded/empty mix" in item for item in audit.conditioning)


def test_candidate_provision_count_is_audited_and_failure_restores(diagnostic, monkeypatch):
    original = random_yard.screen_load_list
    monkeypatch.setattr(adapter, "count_unresolved_provisions", lambda boxes: 1)
    monkeypatch.setattr(adapter, "screen_load_list", lambda *args, **kwargs: ["imdg.stowage.3_2"])
    audit = GenerationReport()
    with pytest.raises(ValueError, match="exhausted"):
        adapter.generate_random_v2("tiny", seed=1, noise=.2, vessel_name="feeder_500", port_name="small_feeder", report=audit, max_attempts=2)
    assert audit.attempts == 2 and audit.provision_rejections == 2
    assert audit.rejections == {"imdg.stowage.3_2": 2}
    assert not audit.accepted
    assert random_yard.screen_load_list is original


@pytest.mark.parametrize("message,rejections", [
    ("cannot balance slice background within physical cargo capacities: infeasible", {"background.construction": 2}),
    ("unrelated initializer error", {}),
])
def test_background_initialization_failure_preserves_audit_and_restores_hooks(diagnostic, monkeypatch, message, rejections):
    before = {name: getattr(random_yard, name) for name in
              ("SIZE_LADDER", "load_param_file", "screen_load_list", "scaled_yard", "generate_load_list", "capacity_load_size")}
    failure = ValueError(message)

    def fail(*args, **kwargs):
        raise failure

    monkeypatch.setattr(random_yard, "recondition_slice", fail)
    report = GenerationReport()
    with pytest.raises(ValueError) as caught:
        adapter.generate_random_v2("tiny", seed=1, noise=.2,
            vessel_name="feeder_500", port_name="small_feeder", report=report, max_attempts=2)
    if rejections:
        assert "screening exhausted 2 attempts" in str(caught.value)
    else:
        assert caught.value is failure
    assert report.attempts == (2 if rejections else 1) and report.rejections == rejections
    assert report.balancing_rejections == (2 if rejections else 0)
    assert not report.accepted and report.completion_checks == 0
    assert 0 < report.requested_targets < report.capacity_teu == 6
    assert report.booking_reference["policy"] == "D36_scaled_full_export_v1"
    assert all(getattr(random_yard, name) is value for name, value in before.items())


def test_dense_demand_keeps_dg_and_original_empty_share(diagnostic, monkeypatch):
    actual = random_yard.generate_load_list
    captured = []
    def record(*args, **kwargs):
        captured.append(kwargs["params"])
        return actual(*args, **kwargs)
    monkeypatch.setattr(random_yard, "generate_load_list", record)
    audit = GenerationReport()
    result = adapter.generate_random_v2("full", seed=14, noise=.2, vessel_name="feeder_500", port_name="small_feeder", n_boxes=12, dense_20ft=True, report=audit)
    assert len(result.load_list) == 12
    assert result.family["n_boxes"] == "12" and audit.requested_targets == 12
    assert all([e["id"] for e in p["equipment"]] == ["dry_20"] for p in captured)
    assert captured[0]["equipment"][0]["empty_share"]["value"] < 1
    assert captured[0]["dg"]["share_of_eligible_loaded_boxes"]["value"] > 0
    assert result.family["cargo_profile"] == "dense_dry20_dg"
    assert result.family["equipment_profile"] == "dense_20ft"
    assert result.family["requested_n_boxes"] == "12"
    assert result.family["dense_20ft"] == "true"
    assert result.provenance.versions["equipment_profile"] == "dense_20ft"
    assert result.provenance.versions["generation_identity"] == result.family["generation_identity"]
    assert any("retaining" in line for line in audit.conditioning)


def test_tiny_dg_parameters_audit_and_restore(diagnostic, monkeypatch):
    actual = random_yard.generate_load_list
    captured = []
    def record(*args, **kwargs):
        captured.append(kwargs["params"])
        return actual(*args, **kwargs)
    monkeypatch.setattr(random_yard, "generate_load_list", record)
    report = GenerationReport()
    adapter.generate_random_v2("tiny", seed=15, noise=.2, vessel_name="feeder_500", port_name="small_feeder", tiny_dg_enabled=True, report=report)
    assert random_yard.generate_load_list is record
    assert all(p["equipment"][0]["empty_share"]["value"] == .2 for p in captured)
    assert all(p["dg"]["share_of_eligible_loaded_boxes"]["value"] > 0 for p in captured)
    assert any("original empty share and DG sampling" in item for item in report.conditioning)
    assert not any("exactly six boxes" in item for item in report.conditioning)


def test_tiny_dg_can_survive_screen_deterministically(diagnostic):
    kwargs = dict(noise=.2, vessel_name="feeder_500", port_name="small_feeder", tiny_dg_enabled=True)
    found = None
    for seed in range(30):
        result = adapter.generate_random_v2("tiny", seed=seed, **kwargs)
        targets = set(result.load_list)
        if any(box.dg is not None for box in result.containers if box.id in targets):
            found = (seed, result)
            break
    assert found is not None
    assert adapter.generate_random_v2("tiny", seed=found[0], **kwargs).model_dump() == found[1].model_dump()


def test_tiny_dg_flag_requires_boolean():
    with pytest.raises(ValueError, match="tiny_dg_enabled"):
        adapter.generate_random_v2("tiny", seed=0, noise=.2, vessel_name="feeder_500", port_name="small_feeder", tiny_dg_enabled=1)


@pytest.mark.parametrize("size", ["small", "medium", "full"])
def test_default_counts_derive_from_capacity(diagnostic, geometry, size):
    audit = GenerationReport()
    result = None
    try:
        result = adapter.generate_random_v2(size, seed=1, noise=.2, vessel_name="feeder_500", port_name="small_feeder", report=audit)
    except ValueError as error:
        # Sizing does not certify that the fleet's background LP can balance
        # this booking. Preserve and verify that exact physical rejection.
        if not str(error).startswith("cannot balance slice background within physical cargo capacities:"):
            raise
        assert not audit.accepted and audit.balancing_rejections == 1
        assert audit.rejections["expert.physical.slot_occupancy"] >= 1
    vessel = result.vessel if result is not None else random_yard._decision_region(geometry, size)
    params = random_yard.load_param_file("cargo", "feeder_export")
    count, budget, limits = random_yard.capacity_load_size(vessel, random_yard.SIZE_LADDER[size], params,
                                                        full_vessel=geometry)
    assert count == audit.requested_targets
    if result is not None:
        assert audit.accepted and audit.balancing_rejections == 0
        assert len(result.load_list) == count
        assert result.family["n_boxes"] == str(count)
        assert audit.booking_utilisation["teu_fraction"] == pytest.approx(audit.realised_teu / audit.capacity_teu)
        assert 0.75 <= audit.booking_utilisation["cargo_mass_fraction"] <= 0.90
    assert audit.booking_reference["policy"] == "D36_scaled_full_export_v1"
    assert audit.booking_reference["region_capacity_teu"] == audit.capacity_teu
    assert audit.capacity_teu == len(vessel.cells)
    assert audit.teu_budget == budget and audit.length_slot_budgets == limits
    assert audit.realised_teu <= budget and audit.c2_screened is False


@pytest.mark.parametrize("size,vessel_name,port_name,expected_targets", [
    ("small", "feeder_1700", "laem_chabang_like", 58),
    ("medium", "feeder_800", "laem_chabang_like", 229),
    ("medium", "feeder_1700", "laem_chabang_like", 428),
])
def test_regional_booking_expands_yard_without_changing_demand_or_capabilities(
        monkeypatch, size, vessel_name, port_name, expected_targets):
    """Real regional geometry and C1; hull acceptance is tested separately.

    These are the bookings which exceeded the adapter's legacy 36/150-box
    footprint. These use the original Laem Chabang-like source port.
    """
    geometry = load_fleet_preset(vessel_name, "full", with_hull=False)
    monkeypatch.setattr(random_yard, "_full_vessel", lambda name: geometry)
    source = load_port(port_name)
    source_before = source.model_dump()
    actual_screen = adapter.screen_load_list
    screened_ports = []

    def screen(vessel, boxes, **kwargs):
        screened_ports.append(kwargs["port"])
        return [reason for reason in actual_screen(vessel, boxes, **kwargs)
                if reason != "checker.missing_vessel_data"]

    monkeypatch.setattr(adapter, "screen_load_list", screen)
    report = GenerationReport()
    result = adapter.generate_random_v2(size, seed=1, noise=.2,
        vessel_name=vessel_name, port_name=port_name, report=report)
    assert report.accepted and report.completion_checks > 0
    assert len(result.load_list) == report.requested_targets == expected_targets
    assert sum((block.n_bays // 2) * block.n_rows
               for block in result.port.yard.blocks) >= expected_targets + 2
    # The adapter's extra screen must inspect the expanded emitted profile.
    assert screened_ports and all(port == result.port for port in screened_ports)
    assert result.port.model_dump(exclude={"yard"}) == source.model_dump(exclude={"yard"})
    originals = {block.id: block for block in source.yard.blocks}
    for block in result.port.yard.blocks:
        original = originals.get(block.id, source.yard.blocks[-1])
        assert block.n_bays <= original.n_bays and block.n_rows <= original.n_rows
        assert block.equipment_id == original.equipment_id
        assert block.allowed_lengths_ft == original.allowed_lengths_ft
        assert block.dg_block == original.dg_block
        assert block.reefer_rows == [row for row in original.reefer_rows if row <= block.n_rows]
        assert block.max_tiers == effective_yard_tiers(source, original)
    assert load_port(port_name).model_dump() == source_before


def test_narrow_port_repeated_blocks_survive_later_export_expansion(monkeypatch):
    """Footprint-only boundary; small-feeder/1700 is not a feasible-port claim."""
    source = load_port("small_feeder")
    before = source.model_dump()
    captured = {}

    class YardReady(Exception):
        pass

    def driver(size, **kwargs):
        rung = random_yard.SIZE_LADDER[size]
        initial = random_yard.scaled_yard(source, blocks=rung.yard_blocks,
                                         bays=rung.yard_bays, rows=rung.yard_rows)
        assert any(block.id.startswith("V") for block in initial.yard.blocks)
        expanded = random_yard._export_yard(initial, rung, 428)
        captured["port"] = expanded
        # Exercise the adapter's screening closure after the second expansion.
        random_yard.screen_load_list(None, [])
        raise YardReady

    def screen(vessel, boxes, **kwargs):
        assert kwargs["port"] is captured["port"]
        return []

    monkeypatch.setattr(random_yard, "generate_random_instance", driver)
    monkeypatch.setattr(adapter, "screen_load_list", screen)
    monkeypatch.setattr(adapter, "booking_tank_masses", lambda *args: {})
    monkeypatch.setattr(adapter, "export_rejections", lambda *args: [])
    with pytest.raises(YardReady):
        adapter.generate_random_v2("medium", seed=1, noise=.2,
            vessel_name="feeder_1700", port_name="small_feeder")
    expanded = captured["port"]
    assert len(expanded.yard.blocks) == 11
    assert len({block.id for block in expanded.yard.blocks}) == 11
    assert sum((block.n_bays // 2) * block.n_rows
               for block in expanded.yard.blocks) >= 430
    assert expanded.model_dump(exclude={"yard"}) == source.model_dump(exclude={"yard"})
    assert all(block.n_bays == 20 and block.n_rows == 4
               for block in expanded.yard.blocks)
    assert source.model_dump() == before


def test_explicit_count_cannot_be_silently_reduced(diagnostic, geometry):
    original = random_yard.capacity_load_size
    region = random_yard._decision_region(geometry, "small")
    requested = len(region.cells) + 1
    with pytest.raises(ValueError, match=f"n_boxes={requested} exceeds capacity-derived demand budget"):
        adapter.generate_random_v2("small", seed=1, noise=.2, vessel_name="feeder_500", port_name="small_feeder", n_boxes=requested)
    assert random_yard.capacity_load_size is original


def test_explicit_count_can_override_default_half_fill(diagnostic, geometry):
    import math

    region = random_yard._decision_region(geometry, "small")
    half_budget = math.floor(len(region.cells) * random_yard.SIZE_LADDER["small"].fill_fraction)
    requested = half_budget + 1
    audit = GenerationReport()
    result = adapter.generate_random_v2("small", seed=1, noise=.2,
        vessel_name="feeder_500", port_name="small_feeder", n_boxes=requested,
        dense_20ft=True, report=audit)
    assert len(result.load_list) == audit.requested_targets == requested
    assert result.family["n_boxes"] == str(requested)
    assert audit.teu_budget == len(result.vessel.cells)
    assert requested > half_budget


def test_depth_zero_completion_rejections_propagate(diagnostic, monkeypatch):
    outcomes = iter([("c1f",), ()])
    monkeypatch.setattr(random_yard, "completion_failures", lambda instance: next(outcomes))
    audit = GenerationReport()
    adapter.generate_random_v2("tiny", seed=1, noise=.2, vessel_name="feeder_500", port_name="small_feeder", report=audit)
    assert audit.accepted and audit.attempts == 2
    assert audit.completion_checks == 2 and audit.completion_rejections == {"c1f": 1}
    assert audit.rejections == {"completion.c1": 1}
    assert audit.shipment_redraws == 0 and audit.c2_screened is False


def test_early_unsupported_dg_redraw_retains_complete_candidate_screen(diagnostic, monkeypatch):
    """An early IMDG rejection must reach shipment conditioning before C1."""
    from stowage.model import DangerousGoods

    original_cargo = random_yard.generate_load_list
    whole_candidates = []
    shipment_draws = []
    singleton_probes = []
    completion_targets = []
    initial_bad = []

    def cargo(*args, **kwargs):
        boxes = original_cargo(*args, **kwargs)
        # Controlled test bookings: one initial unsupported DG declaration,
        # followed by a fresh source draw without that declaration.
        boxes = [box.model_copy(update={"dg": None, "category": None}) for box in boxes]
        if len(boxes) > 1:
            boxes[0] = boxes[0].model_copy(update={
                "dg": DangerousGoods(un_number="0336", imdg_class="1.4G", stowage_category="02"),
                "empty": False, "gross_mass_kg": boxes[0].tare_kg + 1000,
            })
            initial_bad.append(boxes[0])
        else:
            shipment_draws.append((dict(kwargs), boxes[0].model_dump()))
        return boxes

    def screen(vessel, boxes, **kwargs):
        declarations = {box.id: box.model_dump() for box in boxes}
        if len(boxes) > 1:
            whole_candidates.append(declarations)
        else:
            singleton_probes.append(declarations)
        return ["imdg.stowage.3_2"] if any(box.dg is not None for box in boxes) else []

    def completion(instance):
        completion_targets.append(set(instance.load_list))
        return ()

    monkeypatch.setattr(random_yard, "generate_load_list", cargo)
    monkeypatch.setattr(adapter, "screen_load_list", screen)
    monkeypatch.setattr(random_yard, "completion_failures", completion)
    report = GenerationReport()
    result = adapter.generate_random_v2("tiny", seed=3, noise=.2,
        vessel_name="feeder_500", port_name="small_feeder", report=report,
        # This unit fixture changes shipment mass to exercise redraw accounting.
        # Explicit diagnostic quantity isolates it from D36's unchanged default
        # utilisation band; default-booking tests above retain that hard screen.
        n_boxes=4, max_attempts=2)
    assert report.accepted and report.attempts == 2
    assert report.shipment_redraws == len(shipment_draws) == 1
    assert report.rejections == {"imdg.stowage.3_2": 1}
    # This is a candidate-level rate numerator: probing the same rejected
    # shipment again must not count a second rejected whole candidate.
    assert report.provision_rejections == 1
    assert not report.completion_rejections and report.completion_checks == 1
    assert len(whole_candidates) == 2 and len(singleton_probes) == 1
    first, second = whole_candidates
    assert set(first) == set(second) == set(result.load_list)
    changed = [cid for cid in first if first[cid] != second[cid]]
    assert changed == [initial_bad[0].id]
    assert initial_bad[0].model_dump() == first[changed[0]]
    assert singleton_probes == [{changed[0]: first[changed[0]]}]
    assert second[changed[0]]["dg"] is None
    assert completion_targets == [set(result.load_list)]
    assert {box.id: box.model_dump() for box in result.containers
            if box.id in result.load_list} == second


def test_extra_screen_uses_departure_consumables_and_reserved_ballast(diagnostic, geometry, monkeypatch):
    # Geometry-only vessels omit tanks; attach the fleet's actual parameter
    # tables without rebuilding or inventing a hull for this screen diagnostic.
    tanks = [Tank.model_validate({key: value for key, value in item.items()
                                 if key in Tank.model_fields})
             for item in load_param_file("fleet", "feeder_500")["tanks"]]
    # D50 binds the policy to this exact catalog; replace the geometry-only
    # empty policy together with its tanks, using the fleet's actual builder.
    policy = build_voyage_policy(tanks, parameter_source="params/fleet/feeder_500.toml",
                                 vessel_id=geometry.id)
    vessel = geometry.model_copy(update={"tanks": tanks, "voyage_policy": policy})
    monkeypatch.setattr(random_yard, "_full_vessel", lambda name: vessel)
    actual = adapter.screen_load_list
    captured = []

    def record(vessel, boxes, **kwargs):
        captured.append((vessel, kwargs["tank_masses_kg"]))
        return actual(vessel, boxes, **kwargs)

    monkeypatch.setattr(adapter, "screen_load_list", record)
    result = adapter.generate_random_v2("tiny", seed=1, noise=.2,
        vessel_name="feeder_500", port_name="small_feeder")
    assert captured
    assert result.provenance.versions["random_yard_v2"] == adapter.RANDOM_V2_VERSION
    assert result.voyage is not None
    assert result.voyage.consumption is None and not result.voyage.ballast_operations
    from stowage.model import derive_arrival_liquid_state
    arrival = derive_arrival_liquid_state(result.onboard, result.voyage, vessel=result.vessel)
    consumables = {tank_id for group in result.vessel.voyage_policy.groups for tank_id in group.tank_ids}
    for group in result.vessel.voyage_policy.groups:
        assert sum(arrival.tank_masses_kg.get(tank_id, 0.) for tank_id in group.tank_ids) == pytest.approx(
            0.1 * sum(result.onboard.tank_masses_kg.get(tank_id, 0.) for tank_id in group.tank_ids))
    for tank_id, mass in result.onboard.tank_masses_kg.items():
        if tank_id not in consumables:
            assert arrival.tank_masses_kg[tank_id] == mass
    for vessel, masses in captured:
        assert set(masses) == {tank.id for tank in vessel.tanks}
        assert any(not tank.adjustable for tank in vessel.tanks)
        assert any(tank.adjustable for tank in vessel.tanks)
        from stowage.vessel.fleet_conditions import departure_tank_masses
        assert masses == departure_tank_masses(vessel)
        assert any(masses[tank.id] > 0 for tank in vessel.tanks if tank.adjustable)


def test_full_dense_export_uses_booking_tanks_and_audits_design_bands(diagnostic, geometry, monkeypatch):
    """Actual tank tables; geometry-only diagnostics never certify hull physics."""
    tanks = [Tank.model_validate({key: value for key, value in item.items()
                                 if key in Tank.model_fields})
             for item in load_param_file("fleet", "feeder_500")["tanks"]]
    policy = build_voyage_policy(tanks, parameter_source="params/fleet/feeder_500.toml",
                                 vessel_id=geometry.id)
    vessel = geometry.model_copy(update={"tanks": tanks, "voyage_policy": policy})
    monkeypatch.setattr(random_yard, "_full_vessel", lambda name: vessel)
    actual_booking = adapter.booking_tank_masses
    actual_screen = adapter.screen_load_list
    bookings = []
    screened = []

    def booking(vessel, boxes, params):
        assert [item["id"] for item in params["equipment"]] == ["dry_20"]
        masses = actual_booking(vessel, boxes, params)
        bookings.append((vessel, [box.id for box in boxes], masses))
        return masses

    def screen(vessel, boxes, **kwargs):
        booked_vessel, ids, masses = bookings[-1]
        assert vessel is booked_vessel
        assert [box.id for box in boxes] == ids
        assert kwargs["tank_masses_kg"] == masses
        screened.append(masses)
        return actual_screen(vessel, boxes, **kwargs)

    monkeypatch.setattr(adapter, "booking_tank_masses", booking)
    monkeypatch.setattr(adapter, "screen_load_list", screen)
    report = GenerationReport()
    result = adapter.generate_random_v2("full", seed=1, noise=.2,
        vessel_name="feeder_500", port_name="small_feeder", dense_20ft=True,
        report=report)
    assert report.accepted and screened and len(result.load_list) > 40
    audit = report.full_export
    assert 0.90 <= audit["design_displacement_fraction"] <= 1.0
    assert 0.75 <= audit["cargo_mass_fraction"] <= 0.90
    assert audit["teu_fraction"] <= 0.90
    assert audit["mass_limited"] or audit["teu_fraction"] >= 0.75
    assert audit["cargo_mass_kg"] == pytest.approx(sum(
        box.gross_mass_kg for box in result.containers if box.id in result.load_list))
    assert any(result.onboard.tank_masses_kg[tank.id] > 0
               for tank in result.vessel.tanks if tank.adjustable)
    assert screened[-1] == result.onboard.tank_masses_kg


@pytest.mark.parametrize("enabled", [False, True])
def test_dense_small_seed_three_uses_actual_conditioned_profile(diagnostic, geometry, monkeypatch, enabled):
    """Reviewer probe: cargo and extra screen must share the same dry20 recipe."""
    from stowage.generate import cargo
    from stowage.rules.catalogue import rule_freeze_v1

    monkeypatch.setattr(random_yard, "accept_background_candidate", lambda instance, audit: instance)
    draws = []
    actual = cargo.generate_load_list if enabled else random_yard.generate_load_list

    def draw(*args, **kwargs):
        draws.append(kwargs["params"])
        return actual(*args, **kwargs)

    monkeypatch.setattr(cargo if enabled else random_yard, "generate_load_list", draw)
    actual_booking = adapter.booking_tank_masses
    screened = []

    def booking(vessel, boxes, params):
        assert draws and params is draws[-1]
        screened.append(params)
        return actual_booking(vessel, boxes, params)

    monkeypatch.setattr(adapter, "booking_tank_masses", booking)
    with rule_freeze_v1(enabled=enabled):
        result = adapter.generate_random_v2("small", seed=3, noise=.3,
            vessel_name="feeder_500", port_name="laem_chabang_like", dense_20ft=True)
        if enabled:
            import math
            from stowage.generate.random_export import full_export_policy
            reference = full_export_policy(geometry, draws[0])
            assert reference.mass_limited
            expected = math.floor(reference.target_count * len(result.vessel.cells) / reference.capacity_teu)
        else:
            expected = 45
    targets = [box for box in result.containers if box.id in result.load_list]
    # ON adopts the full-ship policy's current D64/D77 ballast/mass budget.
    # Its regional quantity is an outcome of that policy; OFF keeps its 45 pin.
    assert len(targets) == expected
    assert all(box.length_ft == 20 and not box.reefer for box in targets)
    assert screened and all([e["id"] for e in p["equipment"]] == ["dry_20"] for p in screened)


def test_adapter_preserves_every_producer_report_field(diagnostic, monkeypatch):
    from copy import deepcopy
    from stowage.rules.catalogue import rule_freeze_v1

    captured = {}

    def producer(size, *, report, **kwargs):
        report.__dict__.update(random_yard.RandomGenerationReport().__dict__)
        report.attempts = 3
        report.cargo_draws = {"calls": [{"new_field": ["retain", 1]}]}
        report.d62_crossing = {"selected": True, "ship_probe": {"observed": True}}
        report.c2_requested, report.c2_checks = True, 2
        report.c2_direction = "necessary"
        report.c2_errors = {"reason": 1}
        captured.update(deepcopy(asdict(report)))
        raise ValueError("producer diagnostic stop")

    monkeypatch.setattr(random_yard, "generate_random_instance", producer)
    report = GenerationReport()
    with rule_freeze_v1(enabled=True), pytest.raises(ValueError, match="diagnostic stop"):
        adapter.generate_random_v2("small", seed=3, noise=.3,
            vessel_name="feeder_500", port_name="laem_chabang_like", report=report)
    assert asdict(report)["producer_report"] == captured
    for name, value in captured.items():
        if name not in {"conditioning", "accepted"}:
            assert getattr(report, name) == value, name


@pytest.mark.parametrize("size,producer_checks,hook_calls", [("small", 0, 1), ("medium", 2, 0)])
def test_campaign_c2_execution_survives_adapter_and_final_audit(
        monkeypatch, size, producer_checks, hook_calls):
    """Campaign and producer C2 checks have separate owners, one final audit."""
    from time import perf_counter
    from types import SimpleNamespace
    from stowage.generate import instances_v3 as campaign
    from stowage.rules.catalogue import rule_freeze_v1

    calls = []

    def producer(size, *, report, **kwargs):
        result = SimpleNamespace(load_list=["a", "b"], family={},
                                 provenance=SimpleNamespace(versions={}))
        report.attempts = 1
        assert random_yard.completion_failures(result) == ()
        report.accepted, report.requested_targets = True, 2
        if kwargs["c2_screen"]:
            report.c2_requested = report.c2_screened = True
            report.c2_checks = producer_checks
            report.c2_direction = "necessary"
        return result

    monkeypatch.setattr(random_yard, "completion_failures", lambda instance: ())
    monkeypatch.setattr(random_yard, "generate_random_instance", producer)
    monkeypatch.setattr(campaign, "screen_c2", lambda instance: calls.append(instance) or ())
    monkeypatch.setattr(adapter, "attach_default_voyage", lambda instance: instance)
    physics = {"sha256": "c2-bookkeeping-diagnostic"}
    monkeypatch.setattr(campaign, "physics_metadata", lambda **kwargs: physics)
    report = GenerationReport()
    with rule_freeze_v1(enabled=True):
        spec = campaign.scenario_specs(pilot=True, generators=["random_yard"],
            profiles=[size], c2_screened=True, max_attempts=1)[0]
        with campaign._completion_screen(spec, report):
            adapter.generate_random_v2(size, seed=3, noise=.2,
                vessel_name="feeder_500", port_name="laem_chabang_like", report=report)
        audit = campaign._audit_metadata(report, spec, physics, {}, perf_counter())

    assert len(calls) == hook_calls
    assert report.accepted
    assert report.c2_requested and report.c2_screened
    assert report.c2_checks == producer_checks + hook_calls
    assert report.c2_direction == "necessary"
    assert audit["c2_status"] == "C2 screened"
    assert audit["c2_screened"] and audit["c2_requested"]
    assert audit["c2_checks"] == producer_checks + hook_calls
    assert audit["producer_report"]["c2_checks"] == producer_checks


def test_active_profile_pair_share_and_medium_c2_are_forwarded(diagnostic, monkeypatch):
    from stowage.generate import random_cargo
    from stowage.rules.catalogue import rule_freeze_v1

    original = random_cargo.prepare_random_cargo
    captured = {}

    def producer(size, **kwargs):
        captured.update(kwargs)
        params, _ = random_cargo.prepare_random_cargo(load_port("laem_chabang_like"),
                                                     cargo_profile=kwargs["cargo_profile"])
        assert params["dg"]["selected_pair"]["id"] == "water_reactive_acid"
        assert [e["id"] for e in params["equipment"]] == ["dry_20"]
        raise ValueError("producer diagnostic stop")

    monkeypatch.setattr(random_yard, "generate_random_instance", producer)
    with rule_freeze_v1(enabled=True), pytest.raises(ValueError, match="diagnostic stop"):
        adapter.generate_random_v2("medium", seed=3, noise=.3,
            vessel_name="feeder_500", port_name="laem_chabang_like", cargo_profile="dg_stress",
            dg_stress_pair="water_reactive_acid", d62_crossing_share=.75)
    assert captured["cargo_profile"] == "dg_stress"
    assert captured["d62_crossing_share"] == .75
    assert captured["c2_screen"] is True
    assert random_cargo.prepare_random_cargo is original


def test_active_bucket_tags_versions_and_bound_shipments(diagnostic, monkeypatch):
    from stowage.generate import cargo
    from stowage.rules.catalogue import rule_freeze_v1
    from stowage.yard import effective_yard_simulator_version

    monkeypatch.setattr(random_yard, "accept_background_candidate", lambda instance, audit: instance)
    original_bind = cargo.bind_shipment_scenarios
    bound = set()

    def bind(instance, report):
        result = original_bind(instance, report)
        bound.update(box.id for box in result.containers if box.dg is not None)
        return result

    monkeypatch.setattr(cargo, "bind_shipment_scenarios", bind)
    original_screen = adapter.screen_load_list

    def screen(vessel, boxes, **kwargs):
        assert {box.id for box in boxes if box.dg is not None} <= bound
        return original_screen(vessel, boxes, **kwargs)

    monkeypatch.setattr(adapter, "screen_load_list", screen)
    report = GenerationReport()
    with rule_freeze_v1(enabled=True):
        result = adapter.generate_random_v2("small", seed=3, noise=.3,
            vessel_name="feeder_500", port_name="laem_chabang_like", dense_20ft=True,
            d62_crossing_share=1, report=report)
        assert result.provenance.versions["cargo_generator"] == cargo.effective_cargo_generator_version()
        assert result.provenance.versions["yard_simulator"] == effective_yard_simulator_version()
        assert result.provenance.versions["random_yard"] == random_yard.effective_random_yard_version()
    assert result.provenance.versions["random_yard_v2"] == "8.0.0"
    assert result.family["d62_crossing_bucket"] == "yard_limit_v1"
    assert result.family["d62_ship_crossing"] == "probed"
    assert result.family["generator_family"] == "random-yard"
    assert report.cargo_draws["admitted"]["n_boxes"] == len(result.load_list)
    assert report.d62_crossing["yard_bucket"]["applied"]
    assert bound


@pytest.mark.parametrize("pair", [None, "water_reactive_acid"])
def test_direct_stress_adapter_emits_honest_identity_tags(diagnostic, monkeypatch, pair):
    from stowage.rules.catalogue import rule_freeze_v1

    # Metadata boundary diagnostic; the actual profile and pair recipe are
    # exercised separately above, without claiming this fixture's feasibility.
    with rule_freeze_v1(enabled=False):
        source = adapter.generate_random_v2("tiny", seed=3, noise=.3,
            vessel_name="feeder_500", port_name="small_feeder")
    monkeypatch.setattr(random_yard, "generate_random_instance", lambda *args, **kwargs: source.model_copy(deep=True))
    with rule_freeze_v1(enabled=True):
        result = adapter.generate_random_v2("small", seed=3, noise=.3,
            vessel_name="feeder_500", port_name="small_feeder", cargo_profile="dg_stress",
            dg_stress_pair=pair)
    assert result.family["cargo_profile"] == "dg_stress"
    assert result.family["equipment_profile"] == "dry20_dg_stress"
    assert result.family["identity_version"] == "v3"
    assert result.family["dg_stress_pair"] == (pair or "mixed")
    assert result.provenance.versions["equipment_profile"] == "dry20_dg_stress"


def test_active_final_instance_identity_is_bound_before_every_screen(diagnostic, monkeypatch):
    from copy import deepcopy
    from stowage.generate import cargo, random_cargo
    from stowage.rules.catalogue import rule_freeze_v1

    original_params = cargo._params
    original_bind = random_cargo.RandomCargoDraws.bind

    def params(name):
        profile = deepcopy(original_params(name))
        profile["equipment"] = [next(e for e in profile["equipment"] if e["id"] == "dry_20")]
        profile["equipment"][0]["empty_share"]["value"] = 0
        profile["dg"]["share_of_eligible_loaded_boxes"]["value"] = 1
        profile["dg"].pop("pending_rule_freeze_v1", None)
        profile["dg"]["classes"] = [row for row in profile["dg"]["classes"] if row["id"] == "9"]
        profile["dg"]["classes"][0]["un_numbers"] = [
            row for row in profile["dg"]["classes"][0]["un_numbers"] if int(row["un_number"]) == 3480]
        profile["special_stowage"]["share_of_loaded_boxes"]["value"] = 0
        return profile

    ids = []
    original_cargo_bind = cargo.bind_shipment_scenarios

    def bind(instance, report):
        ids.append(instance.id)
        assert instance.id == "final.campaign.identity"
        return original_cargo_bind(instance, report)

    monkeypatch.setattr(cargo, "_params", params)
    monkeypatch.setattr(cargo, "bind_shipment_scenarios", bind)
    monkeypatch.setattr(random_yard, "accept_background_candidate", lambda instance, audit: instance)
    with rule_freeze_v1(enabled=True):
        result = adapter.generate_random_v2("tiny", seed=3, noise=.3,
            vessel_name="feeder_500", port_name="small_feeder", n_boxes=4,
            instance_id="final.campaign.identity")
    assert len(ids) >= 2
    assert result.id == "final.campaign.identity"
    from stowage.rules.imdg.scenarios import context_for, provenance_matches

    registry = result.rule_overrides["imdg.stowage.3_2"]["assessment_scenarios"]
    assert registry
    boxes = {box.id: box for box in result.containers}
    bound_boxes = set()
    for scenario_id, registered in registry.items():
        # Authored design IDs are shared across boxes; each exact shipment
        # context has its own complete record under that ID.
        records = registered if isinstance(registered, (list, tuple)) else [registered]
        assert records
        for record in records:
            assert record["instance_id"] == result.id
            box = boxes[record["container_id"]]
            assert box.dg is not None
            bound_boxes.add(box.id)
            assert record["allowed_slots"]
            source = {"scenario_id": scenario_id, "document": record["document"],
                      "clause": record["clause"]}
            for slot in record["allowed_slots"]:
                context = context_for(result, box, record["entry_path"], slot)
                # Check this complete row independently: a sibling cannot hide
                # stale vessel, voyage, call, entry or placement scope.
                assert provenance_matches(source, context, {scenario_id: record})
    assert {box.id for box in result.containers if box.dg is not None} <= bound_boxes
    assert random_cargo.RandomCargoDraws.bind is original_bind
