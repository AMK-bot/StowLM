"""Generator contracts; geometry diagnostics explicitly omit expensive hull tables.

No diagnostic artifact here is a feasibility certificate. Production generation
calls the full physics builder; checker acceptance belongs to solver/checker tests.
"""
from __future__ import annotations

import json
import math

import pytest

from stowage.generate import random_yard as random
from stowage.io import load_instance, save_instance
from stowage.vessel.fleet_slices import load_fleet_preset


@pytest.fixture(scope="module")
def geometry():
    return load_fleet_preset("feeder_500", "full", with_hull=False)


@pytest.fixture(autouse=True)
def explicit_geometry_diagnostic(monkeypatch, geometry):
    monkeypatch.setattr(random, "_full_vessel", lambda name: geometry)


@pytest.mark.parametrize("size", random.SIZE_LADDER)
def test_rungs_roundtrip(size, tmp_path):
    audit = random.RandomGenerationReport()
    instance = random.generate_random_instance(size, seed=1, report=audit)
    assert instance.stage == "A" and not instance.onboard.placements
    assert len(instance.load_list) == audit.requested_targets <= random.SIZE_LADDER[size].boxes
    assert audit.realised_teu <= audit.teu_budget
    assert all(sum(b.length_ft == length for b in instance.containers if b.id in instance.load_list) <= limit
               for length, limit in audit.length_slot_budgets.items())
    assert not random.completion_failures(instance)
    assert set(instance.family) >= {"vessel", "port", "size", "blocking_ratio", "seed"}
    assert not random.screen_load_list(instance.vessel, [b for b in instance.containers if b.id in instance.load_list])
    assert abs(random.blocking_ratio(instance) - 0.3) <= 0.5 / len(instance.load_list) + 1e-15
    assert not instance.provenance.tainted
    assert instance.provenance.versions["random_yard"] == random.RANDOM_YARD_VERSION
    path = save_instance(instance, tmp_path / f"{size}.json")
    assert load_instance(path) == instance
    assert 0 <= audit.screening_rate < 1


@pytest.mark.parametrize("ratio", [0.0, 0.1, 0.3, 0.5, 0.9, 1.0])
def test_ratio_and_determinism(ratio):
    a = random.generate_random_instance(seed=13, blocking_ratio=ratio)
    b = random.generate_random_instance(seed=13, blocking_ratio=ratio)
    assert a.model_dump_json() == b.model_dump_json()
    assert abs(random.blocking_ratio(a) - ratio) <= 0.5 / len(a.load_list) + 1e-15
    assert len(a.vessel.cells) == 6
    assert len(a.vessel.hatch_covers) == 1
    assert 0 < len(a.load_list) <= len(a.vessel.cells)


def test_target_identity_order_does_not_create_blocking():
    instance = random.generate_random_instance(seed=2, blocking_ratio=0)
    a, b = instance.yard.stacks[:2]
    # All targets are retrieval cohort0, regardless of POD and category.
    a.boxes.extend(b.boxes)
    instance.yard.stacks.remove(b)
    assert random.blocking_ratio(instance) == 0


def test_screen_necessary_conditions(geometry):
    tiny = random.generate_random_instance(seed=4)
    targets = [b for b in tiny.containers if b.id in tiny.load_list]
    assert "expert.physical.slot_occupancy" in random.screen_load_list(tiny.vessel, targets * 2)
    heavy = [b.model_copy(update={"gross_mass_kg": 1e9}) for b in targets]
    assert "expert.physical.stack_weight" in random.screen_load_list(tiny.vessel, heavy)
    reefers = [b.model_copy(update={"reefer": True}) for b in targets * 4]
    assert "expert.physical.reefer_plug" in random.screen_load_list(tiny.vessel, reefers)


def test_screening_rejections_audited(monkeypatch):
    actual = random.screen_load_list
    calls = 0
    def reject_first(vessel, boxes):
        nonlocal calls
        calls += 1
        return ["expert.physical.slot_occupancy"] if calls == 1 else actual(vessel, boxes)
    monkeypatch.setattr(random, "screen_load_list", reject_first)
    audit = random.RandomGenerationReport()
    random.generate_random_instance(seed=2, report=audit)
    assert audit.attempts == 2 and audit.screening_rate == 0.5
    # D36 also records this candidate's independent reference mass-band failure.
    assert audit.rejections == {"expert.physical.slot_occupancy": 1,
                                "iscode.loading.displacement": 1}


def test_cli_resume_and_configuration_validation(tmp_path, capsys):
    args = ["--size", "tiny", "--count", "2", "--output", str(tmp_path)]
    assert random.main(args) == 0
    assert json.loads(capsys.readouterr().out)["generated"] == 2
    assert random.main(args) == 0
    assert json.loads(capsys.readouterr().out)["resumed"] == 2
    assert len(list((tmp_path / "tiny").glob("*.audit.json"))) == 2
    with pytest.raises(ValueError, match="configuration mismatch"):
        random.main([*args, "--blocking-ratio", "0.9"])


@pytest.mark.parametrize("ratio", [-0.1, 1.1, float("nan")])
def test_invalid_ratio(ratio):
    with pytest.raises(ValueError, match="blocking_ratio"):
        random.generate_random_instance(seed=0, blocking_ratio=ratio)


@pytest.mark.parametrize("name", ["feeder_500", "feeder_800", "feeder_1700"])
@pytest.mark.parametrize("size", random.SIZE_LADDER)
def test_kcs_regions_and_split_identity(name, size, monkeypatch):
    from eval.splits import InstanceSpec, spec_from_instance, spec_key, split_of
    parent = load_fleet_preset(name, "full", with_hull=False)
    monkeypatch.setattr(random, "_full_vessel", lambda _: parent)
    instance = random.generate_random_instance(size, vessel_name=name, seed=1)
    params = random.load_param_file("cargo", "feeder_export")
    if size == "tiny":
        params["equipment"] = [next(e for e in params["equipment"] if e["id"] == "dry_20")]
    policy = random.full_export_policy(parent, params)
    assert len(instance.load_list) == math.floor(
        policy.target_count * len(instance.vessel.cells) / policy.capacity_teu)
    assert sum((b.n_bays // 2) * b.n_rows for b in instance.port.yard.blocks) >= len(instance.load_list) + 2
    assert any(not stack.on_deck for stack in instance.vessel.stacks)
    assert any(stack.on_deck for stack in instance.vessel.stacks)
    assert instance.vessel.dg_doc_areas
    expected = InstanceSpec(generator="random_yard", rung=size, vessel=name,
                            slice=size, port="laem_chabang_like", noise=None,
                            blocking_ratio=0.3, seed=1, cargo_profile="feeder_export")
    actual = spec_from_instance(instance)
    assert spec_key(actual) == spec_key(expected)
    assert split_of(actual) == split_of(expected)
    if size == "tiny":
        assert sum(not s.on_deck for s in instance.vessel.stacks) == 2
        assert len(instance.vessel.cells) == 6
        assert len(instance.vessel.hatch_covers) == 1


def test_tiny_retains_source_dg_and_loaded_mix(monkeypatch):
    params = random.load_param_file("cargo", "feeder_export")
    dry = next(e for e in params["equipment"] if e["id"] == "dry_20")
    dry["empty_share"]["value"] = 0.0
    params["dg"]["share_of_eligible_loaded_boxes"]["value"] = 1.0
    monkeypatch.setattr(random, "load_param_file", lambda *args: params)
    # Isolate shipment sampling from C1: an all-DG forced profile need not admit
    # any completion. The integration tests above run the real C1 screen.
    monkeypatch.setattr(random, "completion_failures", lambda instance: ())
    instance = random.generate_random_instance(seed=1)
    targets = [b for b in instance.containers if b.id in instance.load_list]
    assert all(not b.empty and b.dg is not None for b in targets)


def test_d25_weight_classes_including_background():
    instance = random.generate_random_instance(seed=1, blocking_ratio=1)
    assert all(b.weight_class == ("L" if b.gross_mass_kg < 10000 else
                                  "M" if b.gross_mass_kg < 20000 else "H")
               for b in instance.containers)


def test_depth_zero_rejection_counted_before_redraw(monkeypatch):
    actual = random.completion_failures
    calls = 0
    def first_rejected(instance):
        nonlocal calls
        calls += 1
        return ("c1a", "c1b") if calls == 1 else actual(instance)
    monkeypatch.setattr(random, "completion_failures", first_rejected)
    audit = random.RandomGenerationReport()
    instance = random.generate_random_instance(seed=1, report=audit)
    assert audit.accepted and audit.attempts >= 2
    assert audit.completion_rejections["c1a"] >= 1
    assert audit.completion_rejections["c1b"] >= 1
    assert audit.rejections["completion.c1"] >= 1
    assert "expert.physical.slot_occupancy" not in audit.rejections
    assert not actual(instance)
    assert audit.c2_screened is False


def test_completion_screen_configuration_and_no_action_enumeration(monkeypatch):
    import stowage.feasibility as feasibility
    observed = {}
    class Engine:
        def completion_check(self):
            return {"c1a": True, "c1f": False, "c2": False}
        def dead_end(self):
            pytest.fail("depth-zero screen must not enumerate actions")
    def make(instance, space, *, config):
        observed["config"] = config
        return Engine()
    instance = random.generate_random_instance(seed=1)
    monkeypatch.setattr(feasibility, "make_feasibility_engine", make)
    assert random.completion_failures(instance) == ("c1f",)
    assert observed["config"].extra["global_screen"] == 0
    assert observed["config"].lookahead == 0


def test_completion_exhaustion_is_reported(monkeypatch):
    monkeypatch.setattr(random, "completion_failures", lambda instance: ("c1f",))
    audit = random.RandomGenerationReport()
    with pytest.raises(ValueError, match="c1f"):
        random.generate_random_instance(seed=1, max_attempts=2, report=audit)
    assert not audit.accepted and audit.screening_rate == 1.0
    assert audit.attempts == 2
    assert audit.completion_rejections == {"c1f": 2}
    assert audit.rejections["completion.c1"] == 2
    assert "expert.physical.slot_occupancy" not in audit.rejections


def test_impossible_shipments_redrawn_and_whole_candidate_rechecked():
    audit = random.RandomGenerationReport()
    instance = random.generate_random_instance("full", seed=1, report=audit)
    assert len(instance.load_list) == audit.requested_targets
    assert audit.shipment_redraws > 0
    assert audit.completion_rejections["c1a"] > 0
    assert not random.completion_failures(instance)


@pytest.mark.parametrize("size", random.SIZE_LADDER)
def test_d29_conditions_actual_booking_excluding_yard_blockers(size):
    from stowage.vessel.fleet_conditions import slice_condition
    audit = random.RandomGenerationReport()
    instance = random.generate_random_instance(size, seed=1, blocking_ratio=1, report=audit)
    target_mass = sum(b.gross_mass_kg for b in instance.containers if b.id in instance.load_list)
    info = slice_condition(instance.vessel)
    assert info["expected_load_policy"] == "explicit_booking_mass"
    assert info["expected_load_mass_kg"] == target_mass
    from random import Random
    assert info["seed"] == Random(f"{instance.seed}:{audit.attempts - 1}:slice").getrandbits(64)
    assert target_mass < sum(b.gross_mass_kg for b in instance.containers)
    assert instance.onboard.tank_masses_kg == random.departure_tank_masses(instance.vessel)
    if size != "full":
        # D48 can allocate medium at the unchanged 85% constructor target.
        # Preserve the distinct 90–100% audit band exactly, including false.
        assert info["achieved_design_band"] == (
            0.9 * info["design_displacement_kg"] <= info["expected_displacement_kg"]
            <= info["design_displacement_kg"])
        if size != "medium":
            assert info["achieved_design_band"]
        assert info["booking_reference"]["method"] == "bounded_c3_heavy_low_iso20_v1"
        assert any(load.silhouette is not None for load in instance.vessel.fixed_loads)


def test_d29_reconditions_fresh_template_after_rejection(monkeypatch, geometry):
    from stowage.vessel.fleet_conditions import slice_condition
    # D50 requires a declared policy for the actual tanks. Keep both from the
    # same production fleet build; the hull-free parent is still a diagnostic.
    fleet_vessel = load_fleet_preset("feeder_500", "full")
    parent = geometry.model_copy(update={"tanks": fleet_vessel.tanks,
                                         "voyage_policy": fleet_vessel.voyage_policy})
    monkeypatch.setattr(random, "_full_vessel", lambda name: parent)
    before = parent.model_dump()
    actual = random.recondition_slice
    calls = []
    def condition(vessel, *, expected_load_mass_kg, seed):
        result = actual(vessel, expected_load_mass_kg=expected_load_mass_kg, seed=seed)
        calls.append((vessel, result, expected_load_mass_kg))
        return result
    monkeypatch.setattr(random, "recondition_slice", condition)
    completions = 0
    def reject_first_completion(instance):
        nonlocal completions
        completions += 1
        return ("c1b",) if completions == 1 else ()
    monkeypatch.setattr(random, "completion_failures", reject_first_completion)
    instance = random.generate_random_instance(seed=1)
    assert completions == 2 and len(calls) >= completions
    assert all(call[0] is calls[0][0] for call in calls)
    assert instance.vessel is calls[-1][1]
    assert slice_condition(instance.vessel)["expected_load_mass_kg"] == calls[-1][2]
    assert parent.model_dump() == before
    # D58 reserves actual ballast for KG as well as consumables; the fleet
    # builder owns this recipe, rather than an empty-ballast diagnostic default.
    assert instance.onboard.tank_masses_kg == random.departure_tank_masses(instance.vessel)


def test_d36_region_booking_scales_full_ship_policy(geometry):
    """A half-ship region receives the full class's utilisation, not half fill."""
    from stowage.generate.random_export import full_export_policy
    params = random.load_param_file("cargo", "feeder_export")
    full = full_export_policy(geometry, params)
    audit = random.RandomGenerationReport()
    instance = random.generate_random_instance("medium", seed=1, report=audit)
    capacity = len(instance.vessel.cells)
    expected = math.floor(full.target_count * capacity / full.capacity_teu)
    assert len(instance.load_list) == expected
    assert instance.family["region_capacity_teu"] == str(capacity)
    targets = [box for box in instance.containers if box.id in instance.load_list]
    assert float(instance.family["booking_utilisation_teu"]) == pytest.approx(
        sum(box.length_ft / 20 for box in targets) / capacity)
    region_mass_budget = full.cargo_budget_kg(sum(box.gross_mass_kg for box in targets)
        * full.capacity_teu / capacity) * capacity / full.capacity_teu
    assert float(instance.family["booking_utilisation_mass"]) == pytest.approx(
        sum(box.gross_mass_kg for box in targets) / region_mass_budget)
    assert audit.booking_utilisation == {
        "teu_fraction": float(instance.family["booking_utilisation_teu"]),
        "cargo_mass_fraction": float(instance.family["booking_utilisation_mass"]),
    }


def test_explicit_capacity_hook_keeps_three_argument_contract(monkeypatch):
    from dataclasses import replace
    original = random.capacity_load_size
    def explicit(vessel, rung, params):
        return original(vessel, replace(rung, boxes=2, fill_fraction=1.0), params)
    monkeypatch.setattr(random, "capacity_load_size", explicit)
    instance = random.generate_random_instance("tiny", seed=1)
    assert len(instance.load_list) == 2
    assert instance.family["booking_policy"] == "explicit_capacity_override"


def test_real_slot_screen_and_completion_rejection_keep_distinct_counts(monkeypatch):
    original = random.screen_load_list
    screens = []
    def one_slot_failure(vessel, boxes):
        screens.append(True)
        return ["expert.physical.slot_occupancy"] if len(screens) == 1 else original(vessel, boxes)
    checks = []
    def one_completion_failure(instance):
        checks.append(True)
        return ("c1g", "c1h") if len(checks) == 1 else ()
    monkeypatch.setattr(random, "screen_load_list", one_slot_failure)
    monkeypatch.setattr(random, "completion_failures", one_completion_failure)
    audit = random.RandomGenerationReport()
    instance = random.generate_random_instance("tiny", seed=1, report=audit)
    assert audit.accepted and audit.realised_teu <= audit.teu_budget
    assert audit.rejections["expert.physical.slot_occupancy"] == 1
    assert audit.rejections["completion.c1"] == 1
    assert audit.completion_rejections == {"c1g": 1, "c1h": 1}
