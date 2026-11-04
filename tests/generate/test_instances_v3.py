"""V3 campaign identity and audit tests avoid sampling or hull assembly."""
from copy import deepcopy
from itertools import product
from pathlib import Path
import json
from types import SimpleNamespace

import pytest

from stowage.generate import instances_v3 as campaign


def test_simulated_mixing_share_is_explicit_in_campaign_identity(mocked_campaign, monkeypatch):
    from stowage.generate import instances, instances_v2
    from stowage.rules.catalogue import rule_freeze_v1

    mocked_campaign[1]["generator"] = "simulated_yard"
    original = instances.generate_simulated_instance
    seen = []
    def producer(*args, **kwargs):
        seen.append(kwargs)
        return original(*args, **kwargs)
    monkeypatch.setattr(instances, "generate_simulated_instance", producer)
    with rule_freeze_v1(enabled=True):
        for owner in (instances_v2, campaign):
            spec = owner.scenario_specs(pilot=True, generators=["simulated_yard"])[0]
            assert spec["congestion_mix_share"] == .25
        spec = campaign.scenario_specs(pilot=True, generators=["simulated_yard"],
                                       congestion_mix_share=.75)[0]
        other = {**spec, "congestion_mix_share": .25}
        assert campaign.split_metadata(spec)["generation_key"] != campaign.split_metadata(other)["generation_key"]
        assert campaign.split_metadata(spec)["split_spec"] == campaign.split_metadata(other)["split_spec"]
        campaign.generate(spec)
        assert seen[-1]["congestion_mix_share"] == .75
    with rule_freeze_v1(enabled=False):
        assert "congestion_mix_share" not in campaign.scenario_specs(pilot=True)[0]


def test_specs_cover_every_family_and_keep_pilot_prefixes():
    specs = campaign.scenario_specs()
    assert len(specs) == 2240
    assert len({spec["seed"] for spec in specs}) == len(specs)
    assert len(campaign.scenario_specs(pilot=True)) == 60
    assert all(spec in specs for spec in campaign.scenario_specs(pilot=True))
    for generator in campaign.GENERATORS:
        for profile, count in campaign.COUNTS.items():
            group = [spec for spec in specs if spec["generator"] == generator and spec["profile"] == profile]
            assert len(group) == count
            vessels = ("feeder_500",) if profile == "realistic_500" else ("feeder_800",) if profile == "realistic_800" else campaign.VESSELS
            expected = set(product(vessels, campaign.PORTS, campaign.NOISE))
            if profile == "full":
                expected = {combination for combination in expected
                    if combination[:2] != ("feeder_1700", "small_feeder")}
            assert {(spec["vessel"], spec["port"], spec["noise"]) for spec in group} == expected
            assert {spec["vessel"] for spec in group} == set(vessels)
            assert {spec["port"] for spec in group} == set(campaign.PORTS)
            assert {spec["noise"] for spec in group} == set(campaign.NOISE)
            assert all(spec["n_boxes"] is None and spec["tiny_dg_enabled"] for spec in group)
            assert all(spec["c2_screened"] is False for spec in group)
            if profile.startswith("realistic"):
                assert all(spec["dense_20ft"] and spec["full_export"] and spec["size"] == "full" for spec in group)


def test_every_spec_has_frozen_split_before_sampling():
    for spec in campaign.scenario_specs():
        result = campaign.split_metadata(spec)
        assert result["split"] in {"train", "val", "test"}
        assert len(result["spec_key"]) == 64
        assert result == campaign.split_metadata({**spec, "physics_sha256": "new-physics"})


def test_empty_tiny_conditioning_is_not_relabelled_as_ordinary_campaign(mocked_campaign):
    from eval.splits import canonical_spec, spec_from_instance

    _, current = mocked_campaign
    current["generator"] = "simulated_yard"
    spec = campaign.scenario_specs(pilot=True, generators=["simulated_yard"])[0]
    spec["tiny_dg_enabled"] = False
    assert campaign.family_tags(spec, 2)["equipment_profile"] == "empty_dry20"
    assert campaign.family_tags(spec, 2)["cargo_profile"] == "empty_dry20"
    instance, audit = campaign.generate(spec)
    assert instance.family["equipment_profile"] == "empty_dry20"
    assert instance.family["cargo_profile"] == "empty_dry20"
    assert canonical_spec(spec_from_instance(instance)) == campaign.split_metadata(spec)["split_spec"]
    assert audit["split"] == campaign.split_metadata(spec)["split"]


@pytest.fixture
def mocked_campaign(monkeypatch, empty_ship_instance):
    from stowage.generate import instances, instances_v2_random

    physics = {"sha256": "physics-a", "components": {"linear_model": "current"}}
    monkeypatch.setattr(campaign, "physics_metadata", lambda **kwargs: deepcopy(physics))

    def producer(size, *, report, seed, vessel_name, port_name, **kwargs):
        instance = deepcopy(empty_ship_instance)
        instance.seed = seed
        instance.size_class = size
        instance.provenance.created_by = "generate.random_yard" if current["generator"] == "random_yard" else "generate.instances"
        # Model the complete requested controls emitted by real producers.
        # Realised quantity remains an outcome, never the automatic request.
        requested = kwargs["n_boxes"]
        dense = kwargs["dense_20ft"]
        tiny_dg = kwargs["tiny_dg_enabled"]
        empty = current["generator"] == "simulated_yard" and size == "tiny" and not tiny_dg
        instance.family.update(requested_n_boxes="auto" if requested is None else str(requested),
            dense_20ft=str(dense).lower(), tiny_dg_enabled=str(tiny_dg).lower(), without_dg="false",
            cargo_profile="empty_dry20" if empty else "dense_dry20_dg" if dense and
                          current["generator"] == "random_yard" else "feeder_export",
            equipment_profile="empty_dry20" if empty else "dry20_with_dg" if size == "tiny" else
                              "dense_20ft" if dense else "full_mix")
        from stowage.generate.instances_voyage import attach_default_voyage
        from stowage.vessel.fleet_voyage import build_voyage_policy
        instance.port.sources.append("TEST: explicit synthetic fixture departure environment")
        instance.vessel.voyage_policy = build_voyage_policy(instance.vessel.tanks,
            parameter_source="TEST: actual fixture tank catalog", vessel_id=instance.vessel.id)
        instance = attach_default_voyage(instance)
        # This producer is a unit-test double; bind its explicit witness to
        # the same actual/mocked identity used by the campaign under test.
        identity = campaign.physics_metadata()
        report.background_last = {"accepted": True,
            "physics_sha256": identity.get("input_sha256", identity["sha256"])}
        report.accepted = True
        report.attempts = 3
        report.requested_targets = len(instance.load_list)
        report.completion_checks = 3
        report.completion_rejections = {"c1a": 2}
        report.shipment_redraws = 4
        report.capacity_teu = 44
        report.teu_budget = 22
        report.length_slot_budgets = {20: 22, 40: 11}
        if size == "full":
            report.full_export = {"capacity_teu": 44, "teu_fraction": .8}
        return instance

    current = {"generator": "random_yard"}
    monkeypatch.setattr(instances, "generate_simulated_instance", producer)
    monkeypatch.setattr(instances_v2_random, "generate_random_v2", producer)
    return physics, current


@pytest.mark.parametrize("generator", campaign.GENERATORS)
def test_generate_propagates_audit_and_realised_quantity(generator, mocked_campaign):
    physics, current = mocked_campaign
    current["generator"] = generator
    spec = campaign.scenario_specs(pilot=True, generators=[generator])[0]
    instance, audit = campaign.generate(spec)
    assert instance.family["n_boxes"] == str(len(instance.load_list))
    assert audit["physics"] == physics
    assert audit["requested_targets"] == len(instance.load_list)
    assert audit["completion_rejections"] == {"c1a": 2}
    assert audit["shipment_redraws"] == 4
    assert audit["c2_screened"] is False
    assert audit["c2_status"] == "C2 unscreened"
    assert audit["split"] == campaign.split_metadata(spec)["split"]


def test_campaign_rejects_producer_missing_voyage(mocked_campaign, monkeypatch):
    from stowage.generate import instances_v2_random
    original = instances_v2_random.generate_random_v2

    def missing_voyage(*args, **kwargs):
        return original(*args, **kwargs).model_copy(update={"voyage": None})

    monkeypatch.setattr(instances_v2_random, "generate_random_v2", missing_voyage)
    spec = campaign.scenario_specs(pilot=True, generators=["random_yard"])[0]
    with pytest.raises(ValueError, match="voyage"):
        campaign.generate(spec)


def test_resume_rejects_missing_voyage_even_with_rebound_checksum(tmp_path, mocked_campaign):
    spec = campaign.scenario_specs(pilot=True)[0]
    campaign.shard((tmp_path, spec))
    path, sidecar = campaign._paths(tmp_path, spec)
    payload = json.loads(path.read_text())
    payload["voyage"] = None
    path.write_text(json.dumps(payload))
    audit = json.loads(sidecar.read_text())
    audit["instance_sha256"] = campaign._sha256(path)
    sidecar.write_text(json.dumps(audit))
    with pytest.raises(ValueError, match="voyage"):
        campaign.shard((tmp_path, spec))


@pytest.mark.parametrize("generator", campaign.GENERATORS)
def test_campaign_preserves_producer_booking_and_conditioning_tags(generator, mocked_campaign, monkeypatch):
    from stowage.generate import instances, instances_v2_random
    _, current = mocked_campaign
    current["generator"] = generator
    module, name = ((instances, "generate_simulated_instance") if generator == "simulated_yard"
                    else (instances_v2_random, "generate_random_v2"))
    original = getattr(module, name)

    def producer(*args, **kwargs):
        instance = original(*args, **kwargs)
        instance.family.update(booking_policy="region_full_export", requested_n_boxes="auto",
                               generation_identity="test-pinned-draw")
        return instance

    monkeypatch.setattr(module, name, producer)
    spec = campaign.scenario_specs(pilot=True, generators=[generator])[0]
    instance, _ = campaign.generate(spec)
    assert instance.family["booking_policy"] == "region_full_export"
    assert instance.family["requested_n_boxes"] == "auto"
    assert instance.family["generation_identity"] == "test-pinned-draw"


def test_resume_checks_current_physics_without_rewriting(tmp_path, mocked_campaign, monkeypatch):
    physics, _ = mocked_campaign
    spec = campaign.scenario_specs(pilot=True)[0]
    first = campaign.shard((tmp_path, spec))
    path, sidecar = campaign._paths(tmp_path, spec)
    original = path.read_bytes(), sidecar.read_bytes()
    monkeypatch.setattr(campaign, "generate", lambda spec: pytest.fail("regenerated committed shard"))
    assert campaign.shard((tmp_path, spec))["status"] == "resumed"
    assert (path.read_bytes(), sidecar.read_bytes()) == original
    physics["sha256"] = "physics-b"
    with pytest.raises(ValueError, match="physics mismatch"):
        campaign.shard((tmp_path, spec))
    physics["sha256"] = "physics-a"
    path.write_text(path.read_text() + " ")
    with pytest.raises(ValueError, match="checksum"):
        campaign.shard((tmp_path, spec))
    assert first["status"] == "generated"


def test_resume_rejects_changed_requested_control_with_rebound_checksum(tmp_path, mocked_campaign):
    spec = campaign.scenario_specs(pilot=True)[0]
    campaign.shard((tmp_path, spec))
    path, sidecar = campaign._paths(tmp_path, spec)
    payload = json.loads(path.read_text())
    payload["family"]["requested_n_boxes"] = "1"
    path.write_text(json.dumps(payload))
    audit = json.loads(sidecar.read_text())
    audit["instance_sha256"] = campaign._sha256(path)
    sidecar.write_text(json.dumps(audit))
    with pytest.raises(ValueError, match="family/audit/provenance mismatch"):
        campaign.shard((tmp_path, spec))


def test_generate_rejects_stale_admission_fingerprint(mocked_campaign):
    spec = {**campaign.scenario_specs(pilot=True)[0], "physics_sha256": "older"}
    with pytest.raises(ValueError, match="campaign admission"):
        campaign.generate(spec)


def test_physics_metadata_uses_shared_complete_inputs_and_distinguishes_c2():
    from stowage.generate.physics_identity import current_physics_identity
    shared = current_physics_identity()
    c1 = campaign.physics_metadata(c2_screened=False)
    c2 = campaign.physics_metadata(c2_screened=True)
    assert c1["input_sha256"] == c2["input_sha256"] == shared["sha256"]
    assert c1["sources"] == c2["sources"] == shared["sources"]
    assert "src/stowage/solver/ballast.py" in c1["sources"]
    assert c1["c2_screened"] is False and c2["c2_screened"] is True
    assert c1["sha256"] != c2["sha256"]


def test_manifest_has_c1_rates_split_totals_and_physics(tmp_path, mocked_campaign):
    spec = campaign.scenario_specs(pilot=True)[0]
    record = campaign.shard((tmp_path, spec))
    manifest = campaign.write_manifests(tmp_path, [record], "pilot", .5)
    summary = manifest["groups"]["random_yard/tiny"]
    assert summary["completion_rejections"] == {"c1a": 2}
    assert summary["completion_screening_rates_by_reason"] == {"c1a": 2 / 3}
    assert summary["shipment_redraws"] == 4
    assert summary["count"] == 1
    assert summary["mean_instance_s"] > 0
    assert summary["shards"][0]["family"]["n_boxes"] == str(record["audit"]["requested_targets"])
    assert manifest["physics"]["sha256"] == "physics-a"
    assert manifest["split_counts"] == {record["audit"]["split"]: 1}
    assert manifest["c2_status"] == "C2 unscreened"
    assert manifest["structural_exclusion_count"] == 1
    assert manifest["structurally_excluded_family_combinations"] == 6
    exclusion = manifest["structural_exclusions"][0]
    assert exclusion["rule_id"] == "expert.physical.slot_occupancy"
    assert exclusion["reachable_capacity_teu"] == 1260
    assert exclusion["expected_cargo_teu"] == 1363.09
    record["audit"]["physics"]["sha256"] = "physics-b"
    with pytest.raises(ValueError, match="mix physics"):
        campaign.write_manifests(tmp_path, [record], "pilot", .5)


@pytest.mark.parametrize("generator", campaign.GENERATORS)
def test_c2_hook_runs_after_c1_and_always_restores(generator, monkeypatch):
    from stowage.generate import instances, random_yard

    module = random_yard if generator == "random_yard" else instances
    failures = {"value": ("c1a",)}
    original = lambda instance: failures["value"]
    monkeypatch.setattr(module, "completion_failures", original)
    calls = []
    monkeypatch.setattr(campaign, "screen_c2", lambda instance: calls.append(instance) or ("c2",))
    spec = campaign.scenario_specs(pilot=True, generators=[generator], c2_screened=True)[0]
    candidate = SimpleNamespace(load_list=list(range(6)))
    with pytest.raises(RuntimeError, match="candidate exhaustion"):
        with campaign._completion_screen(spec):
            assert module.completion_failures(candidate) == ("c1a",)
            assert calls == []
            failures["value"] = ()
            assert module.completion_failures(candidate) == ("c2",)
            assert calls == [candidate]
            raise RuntimeError("candidate exhaustion")
    assert module.completion_failures is original


def test_c2_unavailable_fails_closed(monkeypatch):
    from stowage.generate import instances
    monkeypatch.setattr(instances, "completion_failures", lambda instance: ())
    spec = campaign.scenario_specs(pilot=True, generators=["simulated_yard"], c2_screened=True)[0]
    with campaign._completion_screen(spec):
        assert instances.completion_failures(SimpleNamespace(load_list=list(range(6)))) == ("checker.missing_vessel_data",)


def test_single_shipment_probe_skips_c2_but_retains_c1(monkeypatch):
    from stowage.generate import instances
    failures = {"value": ()}
    monkeypatch.setattr(instances, "completion_failures", lambda instance: failures["value"])
    monkeypatch.setattr(campaign, "screen_c2", lambda instance: pytest.fail("single shipment invoked LP"))
    spec = campaign.scenario_specs(pilot=True, generators=["simulated_yard"], c2_screened=True)[0]
    with campaign._completion_screen(spec):
        assert instances.completion_failures(SimpleNamespace(load_list=["single"])) == ()
        failures["value"] = ("c1a",)
        assert instances.completion_failures(SimpleNamespace(load_list=["single"])) == ("c1a",)


@pytest.mark.parametrize("diagnostic,c2,expected", [
    ("unavailable", True, ("checker.missing_vessel_data",)),
    ("off", True, ("checker.missing_vessel_data",)),
    (None, True, ("checker.missing_vessel_data",)),
    ("on", True, ()),
    ("on", False, ("c2",)),
    ("on", None, ("c2",)),
])
def test_screen_c2_requires_available_global_screen_and_explicit_pass(monkeypatch, diagnostic, c2, expected):
    from stowage import actions, feasibility

    calls = []
    monkeypatch.setattr(actions, "get_action_space", lambda name: calls.append(name) or "boxslot-space")

    def make_engine(instance, action_space, *, config):
        assert instance == "synthetic-instance"
        assert action_space == "boxslot-space"
        assert config.lookahead == 0
        assert config.extra["global_screen"] == 1
        return SimpleNamespace(diagnostics={"global_screen": diagnostic},
            completion_check=lambda: {"c2": c2})

    monkeypatch.setattr(feasibility, "make_feasibility_engine", make_engine)
    assert campaign.screen_c2("synthetic-instance") == expected
    assert calls == ["boxslot"]


@pytest.mark.parametrize("failure", [ValueError("unqualified necessary model"),
                                    KeyError("missing qualification"),
                                    TypeError("required direction unavailable")])
@pytest.mark.parametrize("stage", ["construction", "completion"])
def test_screen_c2_model_failure_is_closed(monkeypatch, failure, stage):
    from stowage import actions, feasibility

    monkeypatch.setattr(actions, "get_action_space", lambda name: "boxslot-space")

    def fail():
        raise failure

    def make_engine(instance, action_space, *, config):
        if stage == "construction":
            fail()
        return SimpleNamespace(diagnostics={"global_screen": "on"}, completion_check=fail)

    monkeypatch.setattr(feasibility, "make_feasibility_engine", make_engine)
    assert campaign.screen_c2("synthetic-instance") == ("checker.missing_vessel_data",)


def test_c1_only_campaign_does_not_construct_c2(monkeypatch):
    from stowage.generate import instances

    original = lambda instance: ("c1a",)
    monkeypatch.setattr(instances, "completion_failures", original)
    monkeypatch.setattr(campaign, "screen_c2", lambda instance: pytest.fail("C1-only invoked C2"))
    spec = campaign.scenario_specs(pilot=True, generators=["simulated_yard"], c2_screened=False)[0]
    with campaign._completion_screen(spec):
        assert instances.completion_failures is original
        assert instances.completion_failures(SimpleNamespace(load_list=["one", "two"])) == ("c1a",)


def test_missing_linear_direction_does_not_pass_c2(monkeypatch):
    from stowage import actions, feasibility

    def directional_rows(model, *, direction, margin_scale=1.0):
        pytest.fail("incompatible caller unexpectedly supplied its direction")

    def incompatible_adapter(instance, action_space, *, config):
        # Emulate a lower adapter that has not migrated to D49's mandatory API.
        # This remains a failure-injection test after that owner's fix lands.
        directional_rows(object(), margin_scale=config.stability_margin_scale)

    monkeypatch.setattr(actions, "get_action_space", lambda name: "boxslot-space")
    monkeypatch.setattr(feasibility, "make_feasibility_engine", incompatible_adapter)
    assert campaign.screen_c2("synthetic-instance") == ("checker.missing_vessel_data",)


def test_simulated_c2_requests_necessary_rows_at_actual_linear_boundary(monkeypatch):
    from stowage import actions, feasibility, linear
    from stowage.feasibility.global_bounds import GlobalScreen

    directions = []
    model = object()
    candidate = SimpleNamespace(vessel=SimpleNamespace(hull=SimpleNamespace(stations=["station"])))

    class RowsRequested(Exception):
        pass

    def constraints(actual_model, *, direction, margin_scale=1.0):
        assert actual_model is model
        directions.append(direction)
        assert direction == "necessary"
        assert margin_scale == 1.0
        # Exercise the real adapter boundary, stopping before matrix assembly.
        raise RowsRequested

    def make_engine(instance, action_space, *, config):
        assert config.extra["global_screen"] == 1
        with pytest.raises(RowsRequested):
            GlobalScreen.from_instance(instance, None, margin_scale=config.stability_margin_scale)
        return SimpleNamespace(diagnostics={"global_screen": "on"}, completion_check=lambda: {"c2": True})

    monkeypatch.setattr(actions, "get_action_space", lambda name: "boxslot-space")
    monkeypatch.setattr(linear, "band_for_instance", lambda instance: "qualified-band")
    monkeypatch.setattr(linear, "build_linear_model", lambda instance, *, band: model)
    monkeypatch.setattr(linear, "linear_constraints", constraints)
    monkeypatch.setattr(feasibility, "make_feasibility_engine", make_engine)
    assert campaign.screen_c2(candidate) == ()
    assert directions == ["necessary"]


def test_realistic_generation_requires_and_retains_export_audit(mocked_campaign):
    spec = campaign.scenario_specs(pilot=True, generators=["random_yard"], profiles=["realistic_500"])[0]
    _, audit = campaign.generate(spec)
    assert audit["full_export"]["teu_fraction"] == .8


@pytest.mark.parametrize("generator", campaign.GENERATORS)
@pytest.mark.parametrize("kind", ["balance", "exhausted"])
def test_expected_failures_commit_rejected_audit_and_resume(generator, kind, mocked_campaign, monkeypatch, tmp_path):
    from stowage.generate import instances, instances_v2_random
    spec = campaign.scenario_specs(pilot=True, generators=[generator], max_attempts=20, c2_screened=True)[0]
    module = instances_v2_random if generator == "random_yard" else instances
    name = "generate_random_v2" if generator == "random_yard" else "generate_simulated_instance"
    calls = []

    def reject(*args, report, **kwargs):
        calls.append(1)
        if kind == "balance":
            raise ValueError("cannot balance slice background within physical cargo capacities: infeasible synthetic moments")
        report.attempts = 20
        report.rejections = {"expert.physical.slot_occupancy": 20}
        report.completion_rejections = {"c2": 20}
        raise ValueError("screening exhausted 20 attempts: capacity; C1: c2")

    monkeypatch.setattr(module, name, reject)
    record = campaign.shard((tmp_path, spec))
    assert record["status"] == "rejected"
    path, sidecar = campaign._paths(tmp_path, spec)
    assert not path.exists() and not sidecar.exists()
    assert path.with_suffix(".rejected.json").exists()
    audit = record["audit"]
    assert audit["accepted"] is False and audit["c2_screened"] is True
    assert audit["screening_rate"] == 1
    assert audit["attempts"] == (1 if kind == "balance" else 20)
    assert audit["initialization_attempts"] == (1 if kind == "balance" else 0)
    assert audit["balancing_rejections"] == (1 if kind == "balance" else 0)
    assert audit["rejections"]["expert.physical.slot_occupancy"] == audit["attempts"]
    assert audit["split"] == campaign.split_metadata(spec)["split"]
    assert campaign.shard((tmp_path, spec))["status"] == "resumed_rejected"
    assert len(calls) == 1
    payload = json.loads(Path(record["path"]).read_text())
    payload["failure_message"] += "tampered"
    Path(record["path"]).write_text(json.dumps(payload))
    with pytest.raises(ValueError, match="checksum"):
        campaign.shard((tmp_path, spec))


def test_exhausted_rejection_retries_when_attempt_cap_increases(mocked_campaign, monkeypatch, tmp_path):
    from stowage.generate import instances_v2_random
    original = instances_v2_random.generate_random_v2
    spec = campaign.scenario_specs(pilot=True, max_attempts=20)[0]

    def reject(*args, report, **kwargs):
        report.attempts = 20
        report.rejections = {"expert.physical.slot_occupancy": 20}
        raise ValueError("screening exhausted 20 attempts: capacity")

    monkeypatch.setattr(instances_v2_random, "generate_random_v2", reject)
    assert campaign.shard((tmp_path, spec))["status"] == "rejected"
    monkeypatch.setattr(instances_v2_random, "generate_random_v2", original)
    assert campaign.shard((tmp_path, {**spec, "max_attempts": 50}))["status"] == "generated"
    path, sidecar = campaign._paths(tmp_path, spec)
    assert path.exists() and sidecar.exists()
    assert not path.with_suffix(".rejected.json").exists()


def test_unexpected_errors_and_changed_physics_abort(mocked_campaign, monkeypatch):
    from stowage.generate import instances_v2_random
    physics, _ = mocked_campaign
    spec = campaign.scenario_specs(pilot=True)[0]

    def unexpected(*args, **kwargs):
        raise ValueError("missing source parameter")

    monkeypatch.setattr(instances_v2_random, "generate_random_v2", unexpected)
    with pytest.raises(ValueError, match="missing source parameter"):
        campaign.generate(spec)

    def balance_with_epoch_change(*args, **kwargs):
        physics["sha256"] = "new physics"
        raise ValueError("cannot balance slice background within physical cargo capacities: infeasible")

    monkeypatch.setattr(instances_v2_random, "generate_random_v2", balance_with_epoch_change)
    with pytest.raises(ValueError, match="physics implementation changed") as error:
        campaign.generate(spec)
    assert not isinstance(error.value, campaign.CampaignRejection)


def test_mixed_manifest_counts_rejected_requests_without_accepted_shards(mocked_campaign, monkeypatch, tmp_path):
    from stowage.generate import instances_v2_random
    specs = campaign.scenario_specs(pilot=True)[:2]
    accepted = campaign.shard((tmp_path, specs[0]))

    def balance(*args, **kwargs):
        raise ValueError("cannot balance slice background within physical cargo capacities: infeasible")

    monkeypatch.setattr(instances_v2_random, "generate_random_v2", balance)
    rejected = campaign.shard((tmp_path, specs[1]))
    manifest = campaign.write_manifests(tmp_path, [accepted, rejected], "pilot", 1.0, planned_requested=2)
    summary = manifest["groups"]["random_yard/tiny"]
    assert summary["count"] == summary["accepted"] == 1
    assert summary["requested"] == 2 and summary["rejected"] == 1
    assert summary["attempts"] == 4
    assert summary["screened_out"] == 3
    assert summary["screening_rate"] == .75
    assert summary["request_rejection_rate"] == .5
    assert summary["completion_screening_rates_by_reason"] == {"c1a": .5}
    assert summary["rejection_diagnostics"] == {"background_balance_infeasible": 1}
    assert summary["balancing_rejections"] == 1
    assert summary["balancing_screening_rate"] == .25
    assert manifest["balancing_rejections"] == 1
    assert manifest["balancing_screening_rate"] == .25
    assert len(summary["shards"]) == len(summary["rejected_shards"]) == 1
    assert summary["rejected_shards"][0]["instance"] is None
    assert manifest["complete"] is True
    assert manifest["accepted"] == 1 and manifest["rejected"] == 1 and manifest["requested"] == 2
    assert sum(manifest["accepted_split_counts"].values()) == 1
    assert sum(manifest["rejected_split_counts"].values()) == 1
    assert sum(manifest["rung_split_counts"]["tiny"]["rejected"].values()) == 1
    rejected_only = campaign.summarize([rejected])
    assert rejected_only["count"] == 0 and rejected_only["requested"] == 1
    assert rejected_only["screening_rate"] == 1 and rejected_only["shards"] == []


def test_exhaustion_retains_counted_balance_diagnostics(mocked_campaign, monkeypatch):
    from stowage.generate import instances_v2_random
    spec = campaign.scenario_specs(pilot=True, max_attempts=5)[0]

    def exhausted(*args, report, **kwargs):
        report.attempts = report.balancing_rejections = 5
        report.rejections = {"expert.physical.slot_occupancy": 5}
        raise ValueError("screening exhausted 5 attempts: background balances failed")

    monkeypatch.setattr(instances_v2_random, "generate_random_v2", exhausted)
    with pytest.raises(campaign.CampaignRejection) as result:
        campaign.generate(spec)
    assert result.value.audit["rejection_diagnostics"] == {
        "background_balance_infeasible": 5, "screening_exhausted": 1}
    assert result.value.audit["balancing_rejections"] == 5


@pytest.mark.parametrize("args", [["--workers", "7"], ["--workers", "0"], ["--seed", "-1"]])
def test_cli_admission_arguments(args):
    with pytest.raises(SystemExit):
        campaign.main(args)


_REAL_PHYSICS_METADATA = campaign.physics_metadata


def test_resume_rejects_actual_free_surface_environment_change(mocked_campaign, monkeypatch, tmp_path):
    """An intact old shard cannot bypass the public checker's invalid method."""
    monkeypatch.setattr(campaign, "physics_metadata", _REAL_PHYSICS_METADATA)
    monkeypatch.setenv("STOWAGE_STABILITY_FREE_SURFACE_METHOD", "1")
    spec = campaign.scenario_specs(pilot=True)[0]
    assert campaign.shard((tmp_path, spec))["status"] == "generated"
    assert campaign.shard((tmp_path, spec))["status"] == "resumed"
    monkeypatch.setenv("STOWAGE_STABILITY_FREE_SURFACE_METHOD", "3")
    with pytest.raises(ValueError, match="physics|free.surface"):
        campaign.shard((tmp_path, spec))


def test_physics_metadata_tracks_actual_free_surface_environment(monkeypatch):
    monkeypatch.setenv("STOWAGE_STABILITY_FREE_SURFACE_METHOD", "1")
    first = campaign.physics_metadata()
    monkeypatch.setenv("STOWAGE_STABILITY_FREE_SURFACE_METHOD", "3")
    try:
        current = campaign.physics_metadata()
    except ValueError:
        return  # Reject invalid checker configuration before consulting storage.
    assert first != current


def test_rejected_resume_rejects_actual_free_surface_environment_change(
        mocked_campaign, monkeypatch, tmp_path):
    from stowage.generate import instances_v2_random
    monkeypatch.setattr(campaign, "physics_metadata", _REAL_PHYSICS_METADATA)
    monkeypatch.setenv("STOWAGE_STABILITY_FREE_SURFACE_METHOD", "1")
    spec = campaign.scenario_specs(pilot=True, max_attempts=5)[0]

    def exhausted(*args, report, **kwargs):
        report.attempts = 5
        raise ValueError("screening exhausted 5 attempts: completion")

    monkeypatch.setattr(instances_v2_random, "generate_random_v2", exhausted)
    assert campaign.shard((tmp_path, spec))["status"] == "rejected"
    assert campaign.shard((tmp_path, spec))["status"] == "resumed_rejected"
    monkeypatch.setenv("STOWAGE_STABILITY_FREE_SURFACE_METHOD", "3")
    with pytest.raises(ValueError, match="physics|free.surface"):
        campaign.shard((tmp_path, spec))


def test_active_stress_controls_reach_producer_and_v3_identity(mocked_campaign, monkeypatch):
    from stowage.generate import instances_v2_random
    from stowage.rules.catalogue import rule_freeze_v1
    from eval.splits import spec_from_instance
    received = []
    original = instances_v2_random.generate_random_v2

    def producer(*args, **kwargs):
        received.append(kwargs.copy())
        kwargs["report"].cargo_draws = {"raw": 3}
        kwargs["report"].d62_crossing = {"selected": True, "yard_bucket": {"applied": True}}
        kwargs["report"].future_producer_field = {"evidence": "retained"}
        result = original(*args, **kwargs)
        result.family.update(cargo_profile="dg_stress", equipment_profile="dry20_dg_stress",
                             d62_crossing_bucket="yard_built")
        return result

    monkeypatch.setattr(instances_v2_random, "generate_random_v2", producer)
    with rule_freeze_v1(enabled=True):
        spec = campaign.scenario_specs(pilot=True, generators=["random_yard"], profiles=["small"],
            cargo_profile="dg_stress", d62_crossing_share=.75,
            dg_stress_pair="water_reactive_acid")[0]
        instance, audit = campaign.generate(spec)
        assert received[0]["instance_id"] == f"v3.random_yard.small.{spec['seed']}"
        assert received[0]["cargo_profile"] == "dg_stress"
        assert received[0]["d62_crossing_share"] == .75
        assert received[0]["dg_stress_pair"] == "water_reactive_acid"
        identity = spec_from_instance(instance)
        assert identity.identity_version == "v3" and identity.dg_stress_pair == "water_reactive_acid"
        assert identity.equipment_profile == "dry20_dg_stress"
        assert audit["future_producer_field"] == {"evidence": "retained"}
        assert audit["cargo_draws"] == {"raw": 3}
        record = {"status": "generated", "path": "seed.json", "audit": {**audit, "instance_sha256": "abc"}}
        row = campaign.summarize([record])["shards"][0]
        assert row["producer_report"]["future_producer_field"] == {"evidence": "retained"}
        assert row["cargo_draws"] == audit["cargo_draws"]
        assert row["d62_crossing"] == audit["d62_crossing"]
        assert row["family"]["d62_crossing_bucket"] == "yard_built"


def test_active_medium_specs_require_c2_and_off_specs_remain_unchanged():
    from stowage.rules.catalogue import rule_freeze_v1
    with rule_freeze_v1(enabled=False):
        legacy = campaign.scenario_specs(pilot=True, profiles=["medium"])
        assert all(not spec["c2_screened"] for spec in legacy)
        assert all("d62_crossing_share" not in spec for spec in legacy)
    with rule_freeze_v1(enabled=True):
        activated = campaign.scenario_specs(pilot=True, profiles=["medium"])
        assert all(spec["c2_screened"] for spec in activated)


def test_stress_profile_is_rejected_before_sampling_with_switch_off():
    from stowage.rules.catalogue import rule_freeze_v1
    with rule_freeze_v1(enabled=False):
        with pytest.raises(ValueError, match="activation"):
            campaign.scenario_specs(pilot=True, generators=["random_yard"], cargo_profile="dg_stress")


def test_active_manifest_accepts_c2_strata_with_same_physics_inputs(mocked_campaign, monkeypatch, tmp_path):
    from stowage.rules.catalogue import rule_freeze_v1
    from stowage.generate import instances_v2_random
    original = instances_v2_random.generate_random_v2

    def screened_producer(size, **kwargs):
        result = original(size, **kwargs)
        kwargs["report"].c2_screened = size == "medium"
        return result

    monkeypatch.setattr(instances_v2_random, "generate_random_v2", screened_producer)

    def physics(*, c2_screened=False):
        return {"sha256": "c2" if c2_screened else "c1", "input_sha256": "shared",
                "c2_screened": c2_screened, "components": {"linear_model": "current"}}

    monkeypatch.setattr(campaign, "physics_metadata", physics)
    with rule_freeze_v1(enabled=True):
        tiny = campaign.scenario_specs(pilot=True, generators=["random_yard"], profiles=["tiny"])[0]
        medium = campaign.scenario_specs(pilot=True, generators=["random_yard"], profiles=["medium"])[0]
        records = [campaign.shard((tmp_path, spec)) for spec in (tiny, medium)]
        manifest = campaign.write_manifests(tmp_path, records, "pilot", 1., planned_requested=2)
        assert manifest["c2_status"] == "mixed"
        assert manifest["physics"]["input_sha256"] == "shared"
        assert manifest["groups"]["random_yard/medium"]["shards"][0]["spec"]["c2_screened"] is True
        assert campaign.shard((tmp_path, medium))["status"] == "resumed"


def test_active_resume_rejects_stale_effective_generator_versions(mocked_campaign, monkeypatch, tmp_path):
    from stowage.generate import cargo
    from stowage.rules.catalogue import rule_freeze_v1
    with rule_freeze_v1(enabled=True):
        spec = campaign.scenario_specs(pilot=True, generators=["random_yard"])[0]
        campaign.shard((tmp_path, spec))
        monkeypatch.setattr(cargo, "effective_cargo_generator_version", lambda: "future-cargo")
        with pytest.raises(ValueError, match="component|provenance"):
            campaign.shard((tmp_path, spec))


def test_active_rejection_does_not_claim_unperformed_c2(mocked_campaign, monkeypatch, tmp_path):
    from stowage.generate import instances_v2_random
    from stowage.rules.catalogue import rule_freeze_v1

    def balance(*args, **kwargs):
        raise ValueError("cannot balance slice background within physical cargo capacities: synthetic")

    monkeypatch.setattr(instances_v2_random, "generate_random_v2", balance)
    with rule_freeze_v1(enabled=True):
        spec = campaign.scenario_specs(pilot=True, generators=["random_yard"], profiles=["medium"])[0]
        with pytest.raises(campaign.CampaignRejection) as failure:
            campaign.generate(spec)
        assert failure.value.audit["c2_requested"] is True
        assert failure.value.audit["c2_screened"] is False
        assert failure.value.audit["c2_status"] == "C2 unscreened"
        record = campaign.shard((tmp_path, spec))
        manifest = campaign.write_manifests(tmp_path, [record], "pilot", 1., planned_requested=1)
        assert manifest["c2_status"] == "C2 unscreened"
        assert manifest["c2_requested"] is True
