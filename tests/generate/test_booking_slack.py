"""Underbooking changes synthetic demand, never the checker's physical rules."""
from copy import deepcopy
from dataclasses import asdict
from types import SimpleNamespace

import pytest

from stowage.generate import instances, instances_v2, instances_v3, random_export, random_yard
from stowage.generate.instances_v2_random import generate_random_v2
from stowage.params import load_param_file
from stowage.rules.catalogue import rule_freeze_v1
from stowage.vessel.fleet_slices import load_fleet_preset


@pytest.mark.parametrize("slack", [-.1, 1., 2., float("nan"), float("inf"), True, "0.2"])
def test_invalid_booking_slack_rejected_at_public_apis(slack):
    kwargs = dict(seed=1, booking_slack=slack)
    for producer in (random_yard.generate_random_instance, instances.generate_simulated_instance):
        with pytest.raises(ValueError, match="booking_slack"):
            producer(**kwargs)
    with pytest.raises(ValueError, match="booking_slack"):
        generate_random_v2("small", noise=.2, vessel_name="feeder_500", port_name="small_feeder", **kwargs)
    for module in (instances_v2, instances_v3):
        with pytest.raises(ValueError, match="booking_slack"):
            module.scenario_specs(booking_slack=slack)


def test_cap_uses_expected_mix_then_actual_teu_budget_without_changing_cells():
    vessel = SimpleNamespace(cells=list(range(100)))
    params = load_param_file("cargo", "feeder_export")
    sizing = (85, 90, {20: 90, 40: 40, 45: 40})
    assert random_yard.apply_booking_slack(vessel, params, sizing, None) is sizing
    count, budget, limits = random_yard.apply_booking_slack(vessel, params, sizing, .25)
    mean, _ = random_export.expected_cargo_mix(params)
    assert budget == 75 and count == int(75 // mean)
    assert limits == sizing[2] and len(vessel.cells) == 100
    with pytest.raises(ValueError, match="leaves no"):
        random_yard.apply_booking_slack(vessel, params, sizing, .999)


@pytest.fixture(scope="module")
def geometry():
    return load_fleet_preset("feeder_500", "full", with_hull=False)


@pytest.mark.parametrize("family", ["random_yard", "simulated_yard"])
def test_underbooked_family_is_deterministic_audited_and_default_unchanged(monkeypatch, geometry, family):
    # Explicit geometry-only diagnostic; integration experiments must still use
    # the real hull, ballast finisher, full checker and mask replay.
    from stowage.generate import instances_v2_random as adapter
    module = random_yard if family == "random_yard" else instances
    monkeypatch.setattr(module, "_full_vessel", lambda name: geometry)
    screen_owner = adapter if family == "random_yard" else instances
    original_screen = screen_owner.screen_load_list
    monkeypatch.setattr(screen_owner, "screen_load_list", lambda *args, **kwargs:
        [r for r in original_screen(*args, **kwargs) if r != "checker.missing_vessel_data"])
    producer = generate_random_v2 if family == "random_yard" else instances.generate_simulated_instance
    kwargs = dict(seed=3, vessel_name="feeder_500", port_name="laem_chabang_like", noise=.2,
                  tiny_dg_enabled=True)
    with rule_freeze_v1(enabled=False):
        baseline = producer("tiny", **kwargs)
        default_none = producer("tiny", **kwargs, booking_slack=None)
        report = instances.GenerationReport()
        slack = producer("tiny", **kwargs, booking_slack=.5, report=report)
        again = producer("tiny", **kwargs, booking_slack=.5)
    assert baseline.model_dump() == default_none.model_dump()
    assert "booking_slack" not in baseline.family
    assert slack.model_dump() == again.model_dump()
    assert len(slack.load_list) == report.requested_targets == 3
    assert report.realised_teu <= report.teu_budget == 3
    assert report.capacity_teu == 6 and len(slack.vessel.cells) == len(baseline.vessel.cells)
    assert report.booking_slack == asdict(report)["booking_slack"] == .5
    assert slack.family["booking_slack"] == "0.5"
    assert slack.family["booking_capacity_basis"] == "decision_cells_geometric_teu"
    assert slack.family["generation_identity"] != baseline.family["generation_identity"]
    assert any(line.startswith("ASSUMPTION:") for line in report.conditioning)
    with pytest.raises(ValueError, match="explicit|n_boxes"):
        producer("tiny", **kwargs, booking_slack=.5, n_boxes=4)


def test_slack_omits_only_synthetic_lower_distribution_bands(monkeypatch):
    audit = dict(failure_components=["reference_teu_fraction", "reference_cargo_mass_fraction",
        "reference_design_displacement_fraction"], teu_fraction=.5, cargo_mass_fraction=.5,
        design_displacement_fraction=.8)
    monkeypatch.setattr(random_export, "export_reference_audit", lambda *a, **k: deepcopy(audit))
    assert random_export.export_rejections(None, [], None)
    assert random_export.export_rejections(None, [], None, minimum_utilisation=False) == []
    audit.update(teu_fraction=.95, cargo_mass_fraction=.95, design_displacement_fraction=1.1)
    assert random_export.export_rejections(None, [], None, minimum_utilisation=False) == [
        "expert.physical.slot_occupancy", "iscode.loading.displacement"]


@pytest.mark.parametrize("module", [instances_v2, instances_v3])
def test_campaign_specs_pin_slack_in_separate_generation_identity(module):
    kwargs = dict(pilot=True, profiles={"small"}, generators={"random_yard"})
    original = module.scenario_specs(**kwargs)[0]
    slack = module.scenario_specs(**kwargs, booking_slack=.25)[0]
    assert "booking_slack" not in original and slack["booking_slack"] == .25
    original_split = instances_v2.split_metadata(original)
    slack_split = instances_v2.split_metadata(slack)
    assert original_split["split_spec"] == slack_split["split_spec"]
    assert original_split["generation_key"] != slack_split["generation_key"]
    assert slack_split["generation_spec"]["booking_slack"] == .25


def test_random_cli_booking_slack_resume_and_legacy_audit_shape(monkeypatch, tmp_path, repo_root, capsys):
    from stowage.io import load_instance

    fixture = load_instance(repo_root / "data/fixtures/empty_ship.json")
    calls = []

    def producer(size, *, seed, blocking_ratio, vessel_name, port_name, report, booking_slack):
        calls.append(booking_slack)
        report.attempts, report.accepted = 1, True
        report.targets = len(fixture.load_list)
        report.booking_slack = booking_slack
        return fixture.model_copy(update={"family": {
            "vessel": vessel_name, "port": port_name, "size": size,
            "seed": str(seed), "blocking_ratio": repr(float(blocking_ratio)),
            "generator": random_yard.effective_random_yard_version(),
            "booking_slack": repr(float(booking_slack))}})

    monkeypatch.setattr(random_yard, "generate_random_instance", producer)
    monkeypatch.setattr(random_yard, "_resume_inputs", lambda *a, **k: {"test": "stable-inputs"})
    argv = ["--size", "tiny", "--output", str(tmp_path), "--booking-slack", ".25"]
    with rule_freeze_v1(enabled=False):
        assert "booking_slack" not in random_yard._report_payload(random_yard.RandomGenerationReport())
        assert random_yard.main(argv) == 0
        assert random_yard.main(argv) == 0
        assert '"resumed": 1' in capsys.readouterr().out
        assert calls == [.25]
        with pytest.raises(ValueError, match="configuration mismatch"):
            random_yard.main([*argv[:-1], ".2"])
        with pytest.raises(ValueError, match="configuration mismatch"):
            random_yard.main(argv[:-2])
