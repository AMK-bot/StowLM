"""Cheap shard integrity tests use the public empty-ship fixture, not hull builds."""
from dataclasses import dataclass, field
import json
import sys
from types import ModuleType

import pytest

from stowage.generate import instances_cli as cli
from stowage.io import load_instance

_REAL_PHYSICS_IDENTITY = cli.current_physics_identity


def test_background_unknowns_are_serialized_separately_without_changing_empty_off_audits():
    from stowage.generate.instances import GenerationReport
    from stowage.rules.catalogue import rule_freeze_v1

    report = GenerationReport()
    with rule_freeze_v1(enabled=False):
        assert 'background_unknowns' not in cli._report_payload(report)
    report.background_unknowns = {'background.compute.deadline': 1}
    for enabled in (False, True):
        with rule_freeze_v1(enabled=enabled):
            payload = cli._report_payload(report)
            assert payload['background_unknowns'] == {'background.compute.deadline': 1}
            assert payload['background_rejections'] == {}
            assert cli._producer_shard_fields(payload).get('producer_report', payload)['background_unknowns'] == report.background_unknowns


def test_mixing_evidence_survives_shard_summary_and_stays_absent_off():
    from stowage.generate.instances import GenerationReport
    from stowage.rules.catalogue import rule_freeze_v1

    report = GenerationReport()
    report.congestion_mixing = {"policy": "synthetic-independent-v2", "share": .25,
                               "achieved_share": .2, "mixed_boxes": 1, "eligible_boxes": 5}
    with rule_freeze_v1(enabled=True):
        payload = cli._report_payload(report)
        shard = cli._producer_shard_fields(payload)
        assert shard["congestion_mixing"] == report.congestion_mixing
        assert shard["producer_report"]["congestion_mixing"] == report.congestion_mixing
    with rule_freeze_v1(enabled=False):
        assert "congestion_mixing" not in cli._report_payload(report)


@pytest.fixture
def generator(monkeypatch, repo_root):
    # Storage-only tests replace the physical backend explicitly; dedicated
    # environment regressions below restore the real input fingerprint.
    monkeypatch.setattr(cli, "current_physics_identity",
        lambda: {"sha256": "unit-test-current-physics"})
    fixture = load_instance(repo_root / "data/fixtures/empty_ship.json")
    from stowage.generate.instances_voyage import attach_default_voyage
    from stowage.vessel.fleet_voyage import build_voyage_policy
    fixture.port.sources.append("TEST: explicit synthetic departure profile")
    fixture.vessel.voyage_policy = build_voyage_policy(fixture.vessel.tanks,
        parameter_source="TEST: actual fixture tank catalog", vessel_id=fixture.vessel.id)
    fixture = attach_default_voyage(fixture)
    calls = []

    @dataclass
    class Report:
        attempts: int = 2
        provision_rejections: int = 1
        rejections: dict = field(default_factory=lambda: {"expert.physical.reefer_plug": 1})
        conditioning: list = field(default_factory=list)
        simulation_statistics: dict = field(default_factory=dict)
        timings_s: dict = field(default_factory=dict)
        capacity_teu: int = 44
        requested_targets: int = 13
        teu_budget: int = 22
        length_slot_budgets: dict = field(default_factory=lambda: {20: 22, 40: 11})
        completion_checks: int = 1
        completion_rejections: dict = field(default_factory=lambda: {"c1a": 1, "c1d": 1})
        shipment_redraws: int = 3
        c2_screened: bool = False
        background_last: dict = field(default_factory=dict)

        @property
        def screening_rate(self):
            return (self.attempts - 1) / self.attempts

    def generate(size="tiny", *, seed, noise, vessel_name, port_name, report, max_attempts, without_dg=False):
        calls.append((size, seed))
        # The fixture producer replaces the physics backend in these shard
        # unit tests; an explicit current witness remains part of its contract.
        report.background_last = {"accepted": True,
            "physics_sha256": cli.current_physics_identity()["sha256"]}
        return fixture.model_copy(update={"size_class": size, "seed": seed, "family": {
            "vessel": vessel_name, "port": port_name, "size": size, "noise": repr(float(noise)),
            "seed": str(seed), "generator": "test-version",
            "cargo_profile": "no_dg" if without_dg else "feeder_export",
            "equipment_profile": "dry20_with_dg" if size == "tiny" else "full_mix"}})

    module = ModuleType("stowage.generate.instances")
    module.GenerationReport = Report
    module.INSTANCES_VERSION = "test-version"
    module.generate_simulated_instance = generate
    monkeypatch.setitem(sys.modules, "stowage.generate.instances", module)
    return calls


def args(tmp_path):
    return ["--size", "tiny", "--count", "2", "--output", str(tmp_path)]


def test_active_cli_binds_mixing_config_and_evidence_on_resume(generator, monkeypatch, tmp_path, capsys):
    from stowage.rules.catalogue import rule_freeze_v1

    module = sys.modules["stowage.generate.instances"]
    original = module.generate_simulated_instance
    evidence = {"policy": "synthetic-independent-v2", "congestion_mix_share": .75,
                "achieved_share": .5, "mixed_boxes": 1, "eligible_boxes": 2}
    def producer(*args, congestion_mix_share, **kwargs):
        assert congestion_mix_share == .75
        kwargs["report"].congestion_mixing = evidence.copy()
        result = original(*args, **kwargs)
        result.family["congestion_mix_share"] = repr(congestion_mix_share)
        return result
    monkeypatch.setattr(module, "generate_simulated_instance", producer)
    argv = ["--size", "tiny", "--count", "1", "--output", str(tmp_path),
            "--congestion-mix-share", ".75"]
    with rule_freeze_v1(enabled=True):
        assert cli.main(argv) == 0
        first = json.loads(capsys.readouterr().out)
        row = first["rungs"]["tiny"]["shards"][0]
        assert row["congestion_mixing"] == evidence
        assert row["producer_report"]["congestion_mixing"] == evidence
        sidecar = tmp_path / "tiny/seed-00000000.audit.json"
        assert json.loads(sidecar.read_text())["config"]["congestion_mix_share"] == .75
        assert cli.main(argv) == 0
        capsys.readouterr()
        with pytest.raises(ValueError, match="configuration mismatch"):
            cli.main([*argv[:-1], ".25"])
    assert generator == [("tiny", 0)]


def test_resume_preserves_audits_and_manifest_totals(generator, tmp_path, capsys):
    assert cli.main(args(tmp_path)) == 0
    first = json.loads(capsys.readouterr().out)
    audit = tmp_path / "tiny/seed-00000000.audit.json"
    original = audit.read_bytes()
    assert first["generated"] == 2
    manifest = first["rungs"]["tiny"]
    assert manifest["attempts"] == 4 and manifest["screened_out"] == 2
    assert manifest["provision_rejections"] == 2
    assert manifest["infeasible_by_provision_rate"] == 0.5
    assert manifest["screening_rates_by_reason"] == {"expert.physical.reefer_plug": 0.5}
    assert manifest["completion_rejections"] == {"c1a": 2, "c1d": 2}
    assert manifest["completion_screening_rates_by_reason"] == {"c1a": .5, "c1d": .5}
    assert manifest["completion_checks"] == 2
    assert manifest["shipment_redraws"] == 6
    assert manifest["c2_screened"] is False
    assert manifest["shards"][0]["requested_targets"] == 13
    assert manifest["shards"][0]["length_slot_budgets"] == {"20": 22, "40": 11}
    assert manifest["shards"][0]["teu_budget"] == 22
    assert manifest["shards"][0]["capacity_teu"] == 44
    assert cli.main(args(tmp_path)) == 0
    second = json.loads(capsys.readouterr().out)
    assert second["resumed"] == 2 and len(generator) == 2
    assert second["rungs"] == first["rungs"]
    assert original == audit.read_bytes()


def test_missing_audit_is_recovered_by_deterministic_regeneration(generator, tmp_path, capsys):
    cli.main(args(tmp_path))
    capsys.readouterr()
    audit = tmp_path / "tiny/seed-00000000.audit.json"
    audit.unlink()
    cli.main(args(tmp_path))
    summary = json.loads(capsys.readouterr().out)
    assert summary["recovered"] == 1 and summary["resumed"] == 1
    assert audit.exists() and len(generator) == 3


def test_resume_rejects_configuration_and_tampering(generator, tmp_path):
    cli.main(args(tmp_path))
    with pytest.raises(ValueError, match="configuration mismatch"):
        cli.main([*args(tmp_path), "--noise", "0.7"])
    path = tmp_path / "tiny/seed-00000000.json"
    path.write_text(path.read_text() + "\n")
    with pytest.raises(ValueError, match="checksum mismatch"):
        cli.main(args(tmp_path))


def test_orphan_audit_rejected(generator, tmp_path):
    cli.main(args(tmp_path))
    (tmp_path / "tiny/seed-00000000.json").unlink()
    with pytest.raises(ValueError, match="audit without instance"):
        cli.main(args(tmp_path))


def test_without_dg_is_explicit_and_pinned_on_resume(generator, tmp_path, capsys):
    conditioned = [*args(tmp_path), "--without-dg"]
    cli.main(conditioned)
    capsys.readouterr()
    instance = load_instance(tmp_path / "tiny/seed-00000000.json")
    assert instance.family["cargo_profile"] == "no_dg"
    audit = json.loads((tmp_path / "tiny/seed-00000000.audit.json").read_text())
    assert audit["config"]["without_dg"] is True
    cli.main(conditioned)
    assert json.loads(capsys.readouterr().out)["resumed"] == 2
    with pytest.raises(ValueError, match="configuration mismatch"):
        cli.main(args(tmp_path))


@pytest.mark.parametrize("flag,count", [("--benchmark", 5), ("--starter", 50)])
def test_modes(generator, tmp_path, capsys, flag, count):
    cli.main(["--size", "tiny", flag, "--output", str(tmp_path)])
    summary = json.loads(capsys.readouterr().out)
    assert summary["generated"] == count and len(generator) == count


@pytest.mark.parametrize("extra", [["--count", "0"], ["--max-attempts", "0"], ["--noise", "nan"]])
def test_invalid_arguments(generator, tmp_path, extra):
    with pytest.raises(SystemExit):
        cli.main([*args(tmp_path), *extra])


def test_resume_rejects_changed_equipment_conditioning(generator, tmp_path):
    cli.main(args(tmp_path))
    path = tmp_path / "tiny/seed-00000000.json"
    payload = json.loads(path.read_text())
    payload["family"]["equipment_profile"] = "empty_dry20"
    path.write_text(json.dumps(payload))
    config = json.loads(path.with_suffix(".audit.json").read_text())["config"]
    with pytest.raises(ValueError, match="configuration mismatch"):
        cli._validate_instance(path, config)


def test_capacity_audit_preserves_booking_utilisation():
    audit = {"booking_reference": {"capacity_teu": 6},
             "booking_utilisation": {"requested_teu_fraction": .85, "teu_fraction": 2 / 3}}
    assert cli._capacity_audit(audit) == audit


def test_resume_rejects_missing_voyage(generator, tmp_path):
    cli.main(args(tmp_path))
    path = tmp_path / "tiny/seed-00000000.json"
    payload = json.loads(path.read_text())
    payload["voyage"] = None
    path.write_text(json.dumps(payload))
    config = json.loads(path.with_suffix(".audit.json").read_text())["config"]
    with pytest.raises(ValueError, match="voyage"):
        cli._validate_instance(path, config)


def test_resume_rejects_actual_free_surface_environment_change(generator, monkeypatch, tmp_path):
    """Unchanged bytes/configuration are insufficient when checker inputs change."""
    monkeypatch.setattr(cli, "current_physics_identity", _REAL_PHYSICS_IDENTITY)
    monkeypatch.setenv("STOWAGE_STABILITY_FREE_SURFACE_METHOD", "1")
    assert cli.main(args(tmp_path)) == 0
    monkeypatch.setenv("STOWAGE_STABILITY_FREE_SURFACE_METHOD", "3")
    with pytest.raises(ValueError, match="physics|free.surface"):
        cli.main(args(tmp_path))


def test_resume_rejects_missing_physics_binding(generator, tmp_path):
    cli.main(args(tmp_path))
    sidecar = tmp_path / "tiny/seed-00000000.audit.json"
    audit = json.loads(sidecar.read_text())
    audit.pop("physics", None)
    sidecar.write_text(json.dumps(audit))
    with pytest.raises(ValueError, match="physics mismatch"):
        cli.main(args(tmp_path))


def test_generation_rejects_midflight_physics_change(generator, monkeypatch, tmp_path):
    # The fixture replaces sys.modules; another test may retain the package
    # attribute referring to the real producer, so patch the actual backend.
    instances = sys.modules["stowage.generate.instances"]
    original = instances.generate_simulated_instance

    def changed_environment(*args, **kwargs):
        result = original(*args, **kwargs)
        monkeypatch.setenv("STOWAGE_STABILITY_FREE_SURFACE_METHOD", "2")
        return result

    monkeypatch.setattr(cli, "current_physics_identity", _REAL_PHYSICS_IDENTITY)
    monkeypatch.setenv("STOWAGE_STABILITY_FREE_SURFACE_METHOD", "1")
    monkeypatch.setattr(instances, "generate_simulated_instance", changed_environment)
    with pytest.raises(ValueError, match="physics implementation changed"):
        cli.main(args(tmp_path))
    assert not (tmp_path / "tiny/seed-00000000.audit.json").exists()


@pytest.mark.parametrize("binding", [None, {}, {"accepted": False, "physics_sha256": "current"},
    {"accepted": True}, {"accepted": True, "physics_sha256": "older"}])
def test_background_binding_requires_current_successful_witness(empty_ship_instance, binding):
    instance = empty_ship_instance.model_copy(deep=True)
    instance.vessel.slice_of = "parent-for-resume-test"
    with pytest.raises(ValueError, match="D58 background physics binding"):
        cli._validate_background_binding(instance, {"background_last": binding}, "current")
    cli._validate_background_binding(instance,
        {"background_last": {"accepted": True, "physics_sha256": "current"}}, "current")


def test_active_report_payload_preserves_every_producer_field_and_legacy_shape():
    from dataclasses import dataclass, field
    from stowage.rules.catalogue import rule_freeze_v1

    @dataclass
    class ProducerReport:
        attempts: int = 1
        c2_requested: bool = False
        c2_checks: int = 0
        c2_direction: str | None = None
        c2_errors: dict = field(default_factory=dict)
        requested_blocking_ratio: float = .2
        realised_blocking_ratio: float = .25
        blocked_targets: int = 1
        targets: int = 4
        producer_report: dict = field(default_factory=dict)
        cargo_draws: dict = field(default_factory=lambda: {"requested": 4})
        d62_crossing: dict = field(default_factory=lambda: {"selected": True})

    report = ProducerReport()
    report.future_producer_field = {"fact": "retained"}
    with rule_freeze_v1(enabled=True):
        assert cli._report_payload(report) == vars(report)
        assert cli._producer_audit({**vars(report), "config": {}, "instance_sha256": "abc"}) == vars(report)
    with rule_freeze_v1(enabled=False):
        assert cli._report_payload(report) == {"attempts": 1}


def test_booking_slack_is_threaded_audited_and_pinned_on_resume(generator, monkeypatch, tmp_path, capsys):
    module = sys.modules["stowage.generate.instances"]
    original = module.generate_simulated_instance

    def producer(*args, booking_slack, **kwargs):
        assert booking_slack == .25
        instance = original(*args, **kwargs)
        instance.family.update(booking_slack=repr(booking_slack),
                               booking_capacity_basis="decision_cells_geometric_teu")
        return instance

    monkeypatch.setattr(module, "generate_simulated_instance", producer)
    argv = [*args(tmp_path), "--booking-slack", ".25"]
    assert cli.main(argv) == 0
    summary = json.loads(capsys.readouterr().out)
    assert summary["rungs"]["tiny"]["booking_slack"] == .25
    assert summary["rungs"]["tiny"]["booking_capacity_basis"] == "decision_cells_geometric_teu"
    audit = json.loads((tmp_path / "tiny/seed-00000000.audit.json").read_text())
    assert audit["config"]["booking_slack"] == .25
    assert cli.main(argv) == 0
    assert json.loads(capsys.readouterr().out)["resumed"] == 2
    with pytest.raises(ValueError, match="configuration mismatch"):
        cli.main([*args(tmp_path), "--booking-slack", ".2"])
    with pytest.raises(ValueError, match="configuration mismatch"):
        cli.main(args(tmp_path))


@pytest.mark.parametrize("slack", ["nan", "-0.1", "1"])
def test_cli_rejects_invalid_booking_slack(generator, tmp_path, slack):
    with pytest.raises(SystemExit):
        cli.main([*args(tmp_path), "--booking-slack", slack])


def test_booking_mass_fraction_cli_pins_config_manifest_and_resume(generator, monkeypatch, tmp_path, capsys):
    module = sys.modules["stowage.generate.instances"]
    original = module.generate_simulated_instance
    def producer(*args, booking_mass_fraction, **kwargs):
        assert booking_mass_fraction == .55
        instance = original(*args, **kwargs)
        instance.family.update(booking_mass_fraction=repr(booking_mass_fraction),
            booking_mass_basis="scaled_full_export_cargo_budget_at_realised_mass")
        return instance
    monkeypatch.setattr(module, "generate_simulated_instance", producer)
    argv = [*args(tmp_path), "--booking-mass-fraction", ".55"]
    assert cli.main(argv) == 0
    summary = json.loads(capsys.readouterr().out)
    assert summary["rungs"]["tiny"]["booking_mass_fraction"] == .55
    audit = json.loads((tmp_path / "tiny/seed-00000000.audit.json").read_text())
    assert audit["config"]["booking_mass_fraction"] == .55
    assert cli.main(argv) == 0
    assert json.loads(capsys.readouterr().out)["resumed"] == 2
    with pytest.raises(ValueError, match="configuration mismatch"):
        cli.main([*argv[:-1], ".7"])
    with pytest.raises(ValueError, match="configuration mismatch"):
        cli.main(args(tmp_path))


@pytest.mark.parametrize("fraction", ["nan", "-0.1", "0", "1.1"])
def test_cli_rejects_invalid_booking_mass_fraction(generator, tmp_path, fraction):
    with pytest.raises(SystemExit):
        cli.main([*args(tmp_path), "--booking-mass-fraction", fraction])
