"""Lighter synthetic booking count; existing per-box draws and physics stay intact."""
from dataclasses import asdict
from types import SimpleNamespace

import pytest

from stowage.generate import instances, instances_v2, instances_v3, random_export, random_yard
from stowage.generate.instances_v2_random import generate_random_v2
from stowage.rules.catalogue import rule_freeze_v1
from stowage.vessel.fleet_slices import load_fleet_preset


@pytest.mark.parametrize("fraction", [0., -.1, 1.01, float("nan"), float("inf"), True, "0.55"])
def test_mass_fraction_validates_all_public_entries(fraction):
    kwargs = dict(seed=1, booking_mass_fraction=fraction)
    for producer in (random_yard.generate_random_instance, instances.generate_simulated_instance):
        with pytest.raises(ValueError, match="booking_mass_fraction"):
            producer(**kwargs)
    with pytest.raises(ValueError, match="booking_mass_fraction"):
        generate_random_v2("tiny", noise=.2, vessel_name="feeder_500", port_name="small_feeder", **kwargs)
    for module in (instances_v2, instances_v3):
        with pytest.raises(ValueError, match="booking_mass_fraction"):
            module.scenario_specs(booking_mass_fraction=fraction)


def test_mass_count_uses_dynamic_existing_reference_denominator(monkeypatch):
    # Explicit unit reference: ballast release makes B(m)=1000+m. A fixed
    # nominal denominator would produce a different (incorrect) box count.
    vessel = SimpleNamespace(cells=list(range(100)))
    reference = SimpleNamespace(capacity_teu=100, cargo_budget_kg=lambda mass: 1000 + mass)
    monkeypatch.setattr(random_yard, "full_export_policy", lambda *a: reference)
    monkeypatch.setattr(random_export, "expected_cargo_mix", lambda params: (1., 10.))
    sizing = (200, 200, {20: 200})
    assert random_yard.apply_booking_mass_fraction(vessel, {}, sizing, None) is sizing
    result = random_yard.apply_booking_mass_fraction(vessel, {}, sizing, .55)
    assert result == (122, 200, {20: 200})
    assert 1220 / random_yard.regional_booking_mass_budget(vessel, reference, 1220) <= .55
    assert 1230 / random_yard.regional_booking_mass_budget(vessel, reference, 1230) > .55
    with pytest.raises(ValueError, match="leaves no"):
        random_yard.apply_booking_mass_fraction(vessel, {}, sizing, .001)


@pytest.mark.parametrize("capacity,budget", [(0, 100), (float("inf"), 100),
    (100, 0), (100, -1), (100, float("nan")), (100, float("inf"))])
def test_mass_reference_fails_closed_on_nonfinite_or_nonpositive_budget(capacity, budget):
    reference = SimpleNamespace(capacity_teu=capacity, cargo_budget_kg=lambda mass: budget)
    with pytest.raises(ValueError, match="booking_mass_fraction"):
        random_yard.regional_booking_mass_budget(SimpleNamespace(cells=[1]), reference, 10.)
    with pytest.raises(ValueError, match="booking_mass_fraction"):
        random_yard.regional_booking_mass_budget(SimpleNamespace(cells=[]), reference, 10.)


@pytest.fixture(scope="module")
def geometry():
    return load_fleet_preset("feeder_500", "full", with_hull=False)


@pytest.mark.parametrize("family", ["random_yard", "simulated_yard"])
@pytest.mark.parametrize("slack", [None, .25])
def test_both_families_cap_realised_mass_and_preserve_individual_distributions(monkeypatch, geometry, family, slack):
    # Geometry-only diagnostic: experiments must still run the actual hull,
    # ballast finisher, full checker and mask replay.
    from stowage.generate import instances_v2_random as adapter
    module = random_yard if family == "random_yard" else instances
    monkeypatch.setattr(module, "_full_vessel", lambda name: geometry)
    screen_owner = adapter if family == "random_yard" else instances
    original_screen = screen_owner.screen_load_list
    monkeypatch.setattr(screen_owner, "screen_load_list", lambda *args, **kwargs:
        [r for r in original_screen(*args, **kwargs) if r != "checker.missing_vessel_data"])
    original_export = module.export_rejections
    lower_filters = []
    def export(*args, **kwargs):
        lower_filters.append(kwargs.get("minimum_utilisation", True))
        return original_export(*args, **kwargs)
    monkeypatch.setattr(module, "export_rejections", export)
    producer = generate_random_v2 if family == "random_yard" else instances.generate_simulated_instance
    report = instances.GenerationReport()
    kwargs = dict(seed=3, vessel_name="feeder_500", port_name="laem_chabang_like", noise=.2,
                  tiny_dg_enabled=True, booking_slack=slack, booking_mass_fraction=.55)
    with rule_freeze_v1(enabled=False):
        emitted = producer("tiny", **kwargs, report=report)
        again = producer("tiny", **kwargs)
    assert emitted.model_dump() == again.model_dump()
    assert report.booking_mass_fraction == asdict(report)["booking_mass_fraction"] == .55
    assert lower_filters and not any(lower_filters)  # includes the mass-only opt-in
    assert any(line.startswith("ASSUMPTION:") and "lighter" in line for line in report.conditioning)
    targets = [box for box in emitted.containers if box.id in emitted.load_list]
    mass = sum(box.gross_mass_kg for box in targets)
    utilisation = report.booking_utilisation
    assert utilisation["cargo_mass_kg"] == mass
    assert utilisation["cargo_mass_fraction"] == pytest.approx(mass / utilisation["cargo_mass_budget_kg"])
    assert utilisation["booking_mass_cap_kg"] == pytest.approx(.55 * utilisation["cargo_mass_budget_kg"])
    assert mass <= utilisation["booking_mass_cap_kg"]
    assert emitted.family["booking_mass_fraction"] == "0.55"
    assert emitted.family["booking_mass_basis"] == random_yard.BOOKING_MASS_BASIS
    assert float(emitted.family["booking_mass_budget_kg"]) == utilisation["cargo_mass_budget_kg"]
    assert len(emitted.load_list) == report.requested_targets < 5
    assert all(box.gross_mass_kg >= box.tare_kg for box in targets)
    with pytest.raises(ValueError, match="explicit|n_boxes"):
        producer("tiny", **kwargs, n_boxes=5)


@pytest.mark.parametrize("module", [instances_v2, instances_v3])
def test_campaign_mass_setting_changes_generation_identity_only(module):
    kwargs = dict(pilot=True, profiles={"small"}, generators={"random_yard"})
    original = module.scenario_specs(**kwargs)[0]
    capped = module.scenario_specs(**kwargs, booking_slack=.25, booking_mass_fraction=.55)[0]
    assert "booking_mass_fraction" not in original
    original_meta = instances_v2.split_metadata(original)
    capped_meta = instances_v2.split_metadata(capped)
    assert original_meta["split_spec"] == capped_meta["split_spec"]
    assert original_meta["generation_key"] != capped_meta["generation_key"]
    assert capped_meta["generation_spec"]["booking_mass_fraction"] == .55
    tags = module.family_tags(capped, 12) if module is instances_v3 else module.family_tags(capped)
    assert tags["booking_mass_fraction"] == "0.55"


def test_random_cli_combined_mass_audit_resume(monkeypatch, tmp_path, repo_root, capsys):
    import json
    from stowage.io import load_instance

    fixture = load_instance(repo_root / "data/fixtures/empty_ship.json")
    calls = []
    def producer(size, *, seed, blocking_ratio, vessel_name, port_name, report,
                 booking_slack, booking_mass_fraction):
        calls.append(booking_mass_fraction)
        report.attempts, report.accepted, report.targets = 1, True, len(fixture.load_list)
        report.booking_slack, report.booking_mass_fraction = booking_slack, booking_mass_fraction
        return fixture.model_copy(update={"family": {
            "vessel": vessel_name, "port": port_name, "size": size, "seed": str(seed),
            "blocking_ratio": repr(float(blocking_ratio)), "generator": random_yard.effective_random_yard_version(),
            "booking_slack": repr(float(booking_slack)), "booking_mass_fraction": repr(float(booking_mass_fraction))}})
    monkeypatch.setattr(random_yard, "generate_random_instance", producer)
    monkeypatch.setattr(random_yard, "_resume_inputs", lambda *a, **k: {"test": "stable-inputs"})
    argv = ["--size", "tiny", "--output", str(tmp_path), "--booking-slack", ".25", "--booking-mass-fraction", ".55"]
    with rule_freeze_v1(enabled=False):
        assert "booking_mass_fraction" not in random_yard._report_payload(random_yard.RandomGenerationReport())
        assert random_yard.main(argv) == 0
        assert random_yard.main(argv) == 0
        assert '"resumed": 1' in capsys.readouterr().out
        assert calls == [.55]
        audit = json.loads((tmp_path / "tiny/seed-00000000.audit.json").read_text())
        assert audit["booking_mass_fraction"] == .55
        with pytest.raises(ValueError, match="configuration mismatch"):
            random_yard.main([*argv[:-1], ".7"])
        with pytest.raises(ValueError, match="configuration mismatch"):
            random_yard.main(argv[:-2])
