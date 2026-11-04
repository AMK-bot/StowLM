"""Campaign addresses, family metadata and resumable audit integrity."""
from copy import deepcopy
from itertools import product
import json

import pytest

from stowage.generate import instances_v2 as campaign


@pytest.mark.parametrize("enabled", [False, True])
@pytest.mark.parametrize("generator", campaign.GENERATORS)
def test_campaign_components_use_effective_epochs(enabled, generator):
    from stowage.generate.cargo import effective_cargo_generator_version
    from stowage.generate.random_yard import effective_random_yard_version
    from stowage.rules.catalogue import rule_freeze_v1
    from stowage.yard.simulator import effective_yard_simulator_version

    with rule_freeze_v1(enabled=enabled):
        components = campaign.generator_components({"generator": generator})
        assert components["cargo_generator"] == effective_cargo_generator_version()
        if generator == "random_yard":
            assert components["random_yard"] == effective_random_yard_version()
        else:
            assert components["yard_simulator"] == effective_yard_simulator_version()


def test_stress_campaign_split_metadata_preserves_v3_identity():
    spec = {**campaign.scenario_specs(pilot=True)[0], "size": "small", "profile": "small",
            "cargo_profile": "dg_stress", "identity_version": "v3",
            "dg_stress_pair": "water_reactive_acid", "equipment_profile": "dry20_dg_stress",
            "dense_20ft": False, "tiny_dg_enabled": True}
    metadata = campaign.split_metadata(spec)
    assert metadata["split"] == "test", metadata
    assert metadata["split_spec"]["identity_version"] == "v3"
    assert metadata["split_spec"]["equipment_profile"] == "dry20_dg_stress"
    assert metadata["split_spec"]["dg_stress_pair"] == "water_reactive_acid"
    assert metadata["generation_spec"]["equipment_profile"] == "dry20_dg_stress"
    assert campaign.family_tags(spec)["identity_version"] == "v3"


def test_campaign_manifest_keeps_every_producer_field():
    from dataclasses import asdict
    from stowage.generate.instances import GenerationReport
    from stowage.rules.catalogue import rule_freeze_v1

    producer = asdict(GenerationReport())
    producer.update(attempts=1, cargo_draws={"requested": {"boxes": 5}},
                    d62_crossing={"selected": True}, future_audit={"fact": 42})
    audit = {**producer, "config": campaign.scenario_specs(pilot=True)[0],
             "instance_sha256": "fixture", "elapsed_generation_s": .1,
             "instance_family": {"d62_crossing_bucket": "yard_limit_v1", "d62_ship_crossing": "probed"}}
    with rule_freeze_v1(enabled=True):
        stored = campaign.summarize([{"path": "one.json", "audit": audit}])["shards"][0]
    for key, value in producer.items():
        assert stored["producer_report"][key] == value
    assert stored["family"]["d62_crossing_bucket"] == "yard_limit_v1"
    assert stored["family"]["d62_ship_crossing"] == "probed"


@pytest.mark.parametrize("generator", campaign.GENERATORS)
def test_campaign_supplies_final_identity_before_shipment_binding(generator, monkeypatch):
    from stowage.generate import instances, instances_v2_random
    from stowage.rules.catalogue import rule_freeze_v1

    spec = campaign.scenario_specs(pilot=True, generators=[generator])[0]
    final_id = f"v2.{generator}.{spec['profile']}.{spec['seed']}"
    class Captured(Exception):
        pass
    def producer(*args, **kwargs):
        assert kwargs.get("instance_id") == final_id
        raise Captured
    module = instances_v2_random if generator == "random_yard" else instances
    name = "generate_random_v2" if generator == "random_yard" else "generate_simulated_instance"
    monkeypatch.setattr(module, name, producer)
    with rule_freeze_v1(enabled=True), pytest.raises(Captured):
        campaign.generate(spec)


@pytest.fixture(autouse=True)
def storage_physics_identity(monkeypatch, request):
    """Metadata/storage cases isolate I/O; the environment regression stays real.

    Full source, environment and qualification invalidation are exercised by
    the dedicated physics-identity and real background-gate regressions.
    """
    if request.node.name.endswith("[environment]"):
        return
    monkeypatch.setattr(campaign, "physics_metadata", lambda: {"sha256": "storage-only-test-inputs"})


@pytest.fixture
def voyage_instance(empty_ship_instance):
    from stowage.generate.instances_voyage import attach_default_voyage
    from stowage.vessel.fleet_voyage import build_voyage_policy

    instance = deepcopy(empty_ship_instance)
    policy = build_voyage_policy(instance.vessel.tanks,
        parameter_source="synthetic model fixture", vessel_id=instance.vessel.id)
    instance.vessel = instance.vessel.model_copy(update={"voyage_policy": policy})
    return attach_default_voyage(instance)


def bind_storage_fixture(instance, audit, spec):
    """Storage-only fixture, with explicit current producer/gate evidence.

    Real acceptance physics remains covered by the background acceptance suite;
    these tests replace only generation to exercise commit and resume storage.
    """
    instance.provenance.versions.update(campaign.generator_components(spec))
    physics = campaign.physics_metadata()
    audit.update(physics=physics, generation_components=campaign.generator_components(spec),
                 background_last={"accepted": True, "physics_sha256": physics["sha256"]})
    return instance, audit


def test_pilot_is_stable_prefix_and_covers_families():
    full = campaign.scenario_specs()
    pilot = campaign.scenario_specs(pilot=True)
    assert len(full) == 1160
    assert len(pilot) == 60
    assert len({s["seed"] for s in full}) == len(full)
    assert all(spec in full for spec in pilot)
    for generator in campaign.GENERATORS:
        group = [s for s in full if s["generator"] == generator]
        assert {s["vessel"] for s in group} == set(campaign.VESSELS)
        assert {s["port"] for s in group} == set(campaign.PORTS)
        assert {s["noise"] for s in group} == set(campaign.NOISE)
        realistic = [s for s in group if s["profile"].startswith("realistic")]
        assert {(s["vessel"],s["n_boxes"]) for s in realistic} == {("feeder_500",400),("feeder_800",800)}
        assert all(s["dense_20ft"] for s in realistic)
        assert all(s["max_attempts"] == 10000 for s in group)
        assert all(s["size"] == "full" for s in realistic)


def test_noise_and_size_family_fields_are_canonical():
    spec = campaign.scenario_specs(pilot=True)[0]
    tags = campaign.family_tags(spec)
    assert tags["noise"] == "0.0"
    assert tags["blocking_ratio"] == "0.0"
    assert tags["cargo_profile"] == "feeder_export"
    assert tags["equipment_profile"] == "dry20_with_dg"
    assert spec["tiny_dg_enabled"] is True
    assert tags["size"] == spec["size"]
    assert tags["generator"] == "random_yard"
    assert all(isinstance(v,str) for v in tags.values())


def test_summary_denominator_is_candidate_draws():
    records = [{"path":"a.json","audit":{"attempts":3,"provision_rejections":1,
        "completion_checks":2,"completion_rejections":{"c1a":2,"c1d":1},
        "shipment_redraws":7,"c2_screened":False,"capacity_teu":44,"requested_targets":13,
        "teu_budget":22,"length_slot_budgets":{20:22,40:11},
        "elapsed_generation_s":.9,"rejections":{"imdg.stowage.3_2":2,"expert.e8.on_deck":1},
        "instance_sha256":"abc","config":campaign.scenario_specs(pilot=True)[0]}},
        {"path":"b.json","audit":{"attempts":1,"elapsed_generation_s":.1,
        "rejections":{},"instance_sha256":"def","config":campaign.scenario_specs(pilot=True)[1]}}]
    summary = campaign.summarize(records)
    assert summary["count"] == 2
    assert summary["screening_rate"] == .5
    assert summary["screening_rates_by_reason"]["imdg.stowage.3_2"] == .5
    assert summary["infeasible_by_provision_rate"] == .25
    assert summary["mean_instance_s"] == .5
    assert summary["completion_rejections"] == {"c1a":2,"c1d":1}
    assert summary["completion_screening_rates_by_reason"] == {"c1a":.5,"c1d":.25}
    assert summary["completion_checks"] == 2
    assert summary["shipment_redraws"] == 7
    assert summary["c2_screened"] is False
    assert summary["shards"][0]["requested_targets"] == 13
    assert summary["shards"][0]["capacity_teu"] == 44
    assert summary["shards"][0]["teu_budget"] == 22
    assert summary["shards"][0]["length_slot_budgets"] == {20:22,40:11}


@pytest.mark.parametrize("generator", campaign.GENERATORS)
def test_generation_rejects_wrong_volume_before_overwriting_family(generator, empty_ship_instance, monkeypatch):
    from stowage.generate import instances, instances_v2_random

    spec = campaign.scenario_specs(pilot=True, generators=[generator])[0]
    assert spec["n_boxes"] != len(empty_ship_instance.load_list)
    before = dict(empty_ship_instance.family)
    module = instances_v2_random if generator == "random_yard" else instances
    name = "generate_random_v2" if generator == "random_yard" else "generate_simulated_instance"
    monkeypatch.setattr(module, name, lambda *args, **kwargs: empty_ship_instance)
    with pytest.raises(ValueError, match="target count.*explicit v2 specification"):
        campaign.generate(spec)
    assert empty_ship_instance.family == before


def test_shard_resume_and_checksum_refusal(tmp_path, voyage_instance, monkeypatch):
    spec = campaign.scenario_specs(pilot=True)[0]
    spec["n_boxes"] = len(voyage_instance.load_list)
    instance = deepcopy(voyage_instance)
    instance.family = campaign.family_tags(spec)
    instance.family["booking_policy"] = "D36_scaled_full_export_v1"
    audit = {"config":spec,"attempts":1,"rejections":{},"elapsed_generation_s":.01}
    bind_storage_fixture(instance, audit, spec)
    monkeypatch.setattr(campaign,"generate",lambda s:(instance,audit))
    first = campaign.shard((tmp_path,spec))
    assert first["status"] == "generated"
    monkeypatch.setattr(campaign,"generate",lambda s:pytest.fail("resume regenerated committed shard"))
    assert campaign.shard((tmp_path,spec))["status"] == "resumed"
    path = tmp_path/spec["generator"]/spec["profile"]/f"seed-{spec['seed']:08d}.json"
    path.write_text(path.read_text()+" ")
    with pytest.raises(ValueError,match="checksum"):
        campaign.shard((tmp_path,spec))


def test_incomplete_shard_regeneration_must_match(tmp_path, voyage_instance, monkeypatch):
    spec = campaign.scenario_specs(pilot=True)[0]
    instance = deepcopy(voyage_instance)
    instance.family = campaign.family_tags(spec)
    audit = {"config": spec}
    bind_storage_fixture(instance, audit, spec)
    monkeypatch.setattr(campaign,"generate",lambda s:(instance,audit))
    path, _ = campaign._paths(tmp_path,spec)
    path.parent.mkdir(parents=True)
    path.write_text("interrupted bytes")
    with pytest.raises(ValueError,match="differs"):
        campaign.shard((tmp_path,spec))


def test_split_assigned_before_generation_and_heldout_noise():
    specs = campaign.scenario_specs()
    for generator in campaign.GENERATORS:
        spec = next(s for s in specs if s["generator"] == generator and s["noise"] == .8)
        metadata = campaign.split_metadata(spec)
        assert metadata["split"] in {"train", "val", "test"}
        assert len(metadata["spec_key"]) == 64
        if generator == "simulated_yard":
            assert metadata["split"] == "test"
            assert "heldout_noise" in metadata["test_family"]


def test_joint_family_coverage_preserves_original_pilot_schedule():
    specs = campaign.scenario_specs()
    for generator in campaign.GENERATORS:
        for profile in campaign.COUNTS:
            group = [s for s in specs if s["generator"] == generator and s["profile"] == profile]
            if profile == "realistic_500":
                vessels = ("feeder_500",)
            elif profile == "realistic_800":
                vessels = ("feeder_800",)
            elif profile in {"medium", "full"}:
                vessels = campaign.VESSELS[1:]
            else:
                vessels = campaign.VESSELS
            ports = tuple(p for p in campaign.PORTS if p != "small_feeder") if profile.startswith("realistic") else campaign.PORTS
            expected = {combo for combo in product(vessels,ports,campaign.NOISE)
                        if not (profile == "small" and combo[:2] == ("feeder_500","small_feeder"))}
            assert {(s["vessel"],s["port"],s["noise"]) for s in group} == expected
            for i,spec in enumerate(group[:5]):
                old_vessel = campaign.VESSELS[i % 3]
                if profile in {"medium","full"}:
                    old_vessel = campaign.VESSELS[1+i%2]
                elif profile.startswith("realistic"):
                    old_vessel = vessels[0]
                assert spec["vessel"] == old_vessel
                assert spec["port"] == ports[i%len(ports)]
                assert spec["noise"] == campaign.NOISE[(i//4)%3]


def test_resume_cap_change_preserves_committed_bytes(tmp_path, voyage_instance, monkeypatch):
    spec = campaign.scenario_specs(pilot=True)[0]
    spec["max_attempts"] = 1000
    spec["n_boxes"] = len(voyage_instance.load_list)
    instance = deepcopy(voyage_instance)
    instance.family = campaign.family_tags(spec)
    audit = {"config":spec,"attempts":8,"rejections":{},"elapsed_generation_s":.01}
    bind_storage_fixture(instance, audit, spec)
    monkeypatch.setattr(campaign,"generate",lambda s:(instance,audit))
    campaign.shard((tmp_path,spec))
    path,sidecar = campaign._paths(tmp_path,spec)
    original = path.read_bytes(),sidecar.read_bytes()
    monkeypatch.setattr(campaign,"generate",lambda s:pytest.fail("cap change regenerated shard"))
    updated = {**spec,"max_attempts":10000}
    resumed = campaign.shard((tmp_path,updated))
    assert resumed["status"] == "resumed"
    assert resumed["audit"]["config"]["max_attempts"] == 1000
    assert original == (path.read_bytes(),sidecar.read_bytes())
    with pytest.raises(ValueError,match="configuration"):
        campaign.shard((tmp_path,{**spec,"max_attempts":7}))
    with pytest.raises(ValueError,match="configuration"):
        campaign.shard((tmp_path,{**updated,"noise":.8}))


def test_cap_compatibility_rejects_invalid_audit_or_configuration():
    old = {"max_attempts":1000,"seed":0}
    assert campaign.configs_compatible(old,{**old,"max_attempts":10000},8)
    assert campaign.configs_compatible(old,{**old,"max_attempts":8},8)
    for attempts in (None, True, 0, -1, 1001):
        assert not campaign.configs_compatible(old,{**old,"max_attempts":10000},attempts)
    assert not campaign.configs_compatible(None,old,1)
    assert not campaign.configs_compatible(old,{**old,"max_attempts":True},1)
    assert not campaign.configs_compatible(old,{**old,"seed":1},1)


def test_generation_controls_have_distinct_auditable_identity():
    spec = campaign.scenario_specs(pilot=True)[0]
    original = campaign.split_metadata(spec)
    for changed in ({**spec, "n_boxes": spec["n_boxes"] + 1},
                    {**spec, "dense_20ft": not spec["dense_20ft"]},
                    {**spec, "tiny_dg_enabled": not spec["tiny_dg_enabled"]}):
        metadata = campaign.split_metadata(changed)
        assert metadata["generation_key"] != original["generation_key"]
        assert metadata["generation_spec"] != original["generation_spec"]
    assert campaign.split_metadata({**spec, "max_attempts": spec["max_attempts"] + 1})["generation_key"] == original["generation_key"]
    tags = campaign.family_tags(spec)
    assert tags["dense_20ft"] == "false"
    assert tags["tiny_dg_enabled"] == "true"
    assert tags["requested_n_boxes"] == str(spec["n_boxes"])


def test_campaign_preserves_producer_booking_and_provenance(voyage_instance, monkeypatch):
    from stowage.generate import instances_v2_random

    spec = campaign.scenario_specs(pilot=True)[0]
    spec["n_boxes"] = len(voyage_instance.load_list)
    instance = deepcopy(voyage_instance)
    instance.family.update(region_capacity_teu="6", booking_policy="D36_scaled_full_export_v1",
                           booking_utilisation_teu="0.8", producer_conditioning="retained")
    instance.seed = spec["seed"]
    instance.size_class = spec["size"]
    instance.provenance.created_by = "generate.random_yard"
    instance.provenance.versions["equipment_profile"] = "producer-dry20"

    def generate(*args, **kwargs):
        kwargs["report"].attempts = 1
        return instance

    monkeypatch.setattr(instances_v2_random, "generate_random_v2", generate)
    result, _ = campaign.generate(spec)
    assert result.family["booking_policy"] == "D36_scaled_full_export_v1"
    assert result.family["region_capacity_teu"] == "6"
    assert result.family["producer_conditioning"] == "retained"
    assert result.provenance.versions["equipment_profile"] == "producer-dry20"


def test_empty_tiny_campaign_preserves_supported_split_identity(voyage_instance, monkeypatch):
    from eval.splits import canonical_spec, spec_from_instance
    from stowage.generate import instances

    spec = campaign.scenario_specs(pilot=True, generators=["simulated_yard"])[0]
    spec["tiny_dg_enabled"] = False
    spec["n_boxes"] = len(voyage_instance.load_list)
    tags = campaign.family_tags(spec)
    assert tags["cargo_profile"] == tags["equipment_profile"] == "empty_dry20"
    metadata = campaign.split_metadata(spec)
    assert metadata["split"] in {"train", "val", "test"}
    assert metadata["split_version"] == "v2"
    identity = metadata["split_spec"]
    assert identity["identity_version"] == "v2"
    assert identity["cargo_profile"] == identity["equipment_profile"] == "empty_dry20"
    assert identity["n_boxes"] == spec["n_boxes"]
    assert identity["tiny_dg_enabled"] is identity["dense_20ft"] is identity["without_dg"] is False
    ordinary = campaign.split_metadata({**spec, "tiny_dg_enabled": True})
    assert metadata["spec_key"] != ordinary["spec_key"]

    # Metadata-boundary fixture only: production generation still owns all
    # physical booking/screening requirements for this explicit quantity.
    instance = deepcopy(voyage_instance)
    instance.seed, instance.size_class = spec["seed"], spec["size"]
    instance.family = {**tags, "without_dg": "false"}
    instance.provenance.created_by = "generate.instances"
    instance.provenance.versions.update(cargo_profile="empty_dry20", equipment_profile="empty_dry20")
    calls = []

    def generate(size, **kwargs):
        calls.append((size, kwargs["tiny_dg_enabled"], kwargs["n_boxes"]))
        kwargs["report"].attempts = 1
        return instance

    monkeypatch.setattr(instances, "generate_simulated_instance", generate)
    result, audit = campaign.generate(spec)
    assert calls == [("tiny", False, spec["n_boxes"])]
    assert canonical_spec(spec_from_instance(result)) == identity
    assert result.family["cargo_profile"] == result.family["equipment_profile"] == "empty_dry20"
    assert result.provenance.versions["cargo_profile"] == "empty_dry20"
    assert result.provenance.versions["equipment_profile"] == "empty_dry20"
    assert audit["config"] == spec


@pytest.mark.parametrize("omit_field", [False, True])
def test_resume_rejects_missing_voyage_even_with_rebound_checksum(
    tmp_path, voyage_instance, monkeypatch, omit_field,
):
    spec = campaign.scenario_specs(pilot=True)[0]
    spec["n_boxes"] = len(voyage_instance.load_list)
    instance = deepcopy(voyage_instance)
    instance.family = campaign.family_tags(spec)
    audit = {"config": spec, "attempts": 1, "rejections": {}, "elapsed_generation_s": .01}
    bind_storage_fixture(instance, audit, spec)
    monkeypatch.setattr(campaign, "generate", lambda spec: (instance, audit))
    campaign.shard((tmp_path, spec))
    path, sidecar = campaign._paths(tmp_path, spec)
    payload = json.loads(path.read_text())
    if omit_field:
        payload.pop("voyage")
    else:
        payload["voyage"] = None
    path.write_text(json.dumps(payload))
    audit = json.loads(sidecar.read_text())
    audit["instance_sha256"] = campaign._sha256(path)
    sidecar.write_text(json.dumps(audit))
    monkeypatch.setattr(campaign, "generate", lambda spec: pytest.fail("legacy shard regenerated on resume"))
    with pytest.raises(ValueError, match="resume.*voyage"):
        campaign.shard((tmp_path, spec))


@pytest.mark.parametrize("generator", campaign.GENERATORS)
def test_generation_rejects_producer_without_voyage(generator, empty_ship_instance, monkeypatch):
    from stowage.generate import instances, instances_v2_random

    spec = campaign.scenario_specs(pilot=True, generators=[generator])[0]
    spec["n_boxes"] = len(empty_ship_instance.load_list)
    instance = deepcopy(empty_ship_instance)
    instance.seed, instance.size_class = spec["seed"], spec["size"]
    instance.provenance.created_by = "generate.random_yard" if generator == "random_yard" else "generate.instances"
    instance.family = campaign.family_tags(spec)
    assert instance.voyage is None

    def generate(*args, **kwargs):
        kwargs["report"].attempts = 1
        return instance

    module = instances_v2_random if generator == "random_yard" else instances
    name = "generate_random_v2" if generator == "random_yard" else "generate_simulated_instance"
    monkeypatch.setattr(module, name, generate)
    with pytest.raises(ValueError, match="producer.*voyage"):
        campaign.generate(spec)


def test_finalize_rejects_pre_d60_committed_real_shard(tmp_path, monkeypatch):
    """An intact historical archive must never acquire the current campaign label."""
    from pathlib import Path
    from stowage.io import load_instance
    from stowage.params import repo_root

    instance = load_instance(repo_root() / 'experiments/background-1028/review-fixes/retry-random-feeder_800-seed0.instance.json')
    assert instance.voyage is not None
    assert max(box.gross_mass_kg for box in instance.containers
               if box.id in instance.load_list) == 40000
    spec = campaign.scenario_specs(pilot=True, generators=['random_yard'], profiles=['medium'])[0]
    spec.update(campaign_version='6.0.0', n_boxes=len(instance.load_list), seed=instance.seed)
    instance.family.update(campaign.family_tags(spec))
    path, sidecar = campaign._paths(tmp_path, spec)
    audit = dict(config=spec, attempts=1, instance_sha256='intact-old-bytes',
                 rejections={}, elapsed_generation_s=1)
    exists, read_text, glob = Path.exists, Path.read_text, Path.glob
    monkeypatch.setattr(Path, 'exists', lambda p: True if p in (path, sidecar) else exists(p))
    monkeypatch.setattr(Path, 'read_text', lambda p, *a, **kw:
        json.dumps(audit) if p == sidecar else read_text(p, *a, **kw))
    monkeypatch.setattr(Path, 'glob', lambda p, pattern:
        iter([sidecar]) if p == tmp_path and pattern == '*/*/seed-*.audit.json' else glob(p, pattern))
    monkeypatch.setattr(campaign, '_sha256', lambda p: 'intact-old-bytes')
    monkeypatch.setattr(campaign, 'load_instance', lambda p: instance)
    monkeypatch.setattr(campaign, 'write_manifests', lambda *args, **kwargs:
        pytest.fail('historical shard reached current campaign manifest'))
    with pytest.raises(ValueError, match='stale.*campaign|campaign.*stale'):
        campaign.main(['--output', str(tmp_path), '--finalize-splits'])
    assert spec['campaign_version'] == audit['config']['campaign_version'] == '6.0.0'


@pytest.mark.parametrize('changed', ['missing_physics', 'components', 'provenance', 'missing_gate', 'gate_identity', 'environment'])
def test_current_resume_requires_current_physics_components_and_gate(
    tmp_path, voyage_instance, monkeypatch, changed,
):
    spec = campaign.scenario_specs(pilot=True)[0]
    spec['n_boxes'] = len(voyage_instance.load_list)
    instance = deepcopy(voyage_instance)
    instance.family = campaign.family_tags(spec)
    instance.vessel = instance.vessel.model_copy(update={'slice_of': 'feeder_500'})
    audit = dict(config=spec, attempts=1, rejections={}, elapsed_generation_s=.01)
    bind_storage_fixture(instance, audit, spec)
    monkeypatch.setattr(campaign, 'generate', lambda spec: (instance, audit))
    campaign.shard((tmp_path, spec))
    path, sidecar = campaign._paths(tmp_path, spec)
    stored = json.loads(sidecar.read_text())
    if changed == 'missing_physics':
        stored.pop('physics')
    elif changed == 'components':
        stored['generation_components']['cargo_generator'] = 'pre-D60'
    elif changed == 'provenance':
        payload = json.loads(path.read_text())
        payload['provenance']['versions']['cargo_generator'] = 'pre-D60'
        path.write_text(json.dumps(payload))
        stored['instance_sha256'] = campaign._sha256(path)
    elif changed == 'missing_gate':
        stored.pop('background_last')
    elif changed == 'gate_identity':
        stored['background_last']['physics_sha256'] = 'stale-witness-inputs'
    else:
        monkeypatch.setenv('STOWAGE_STABILITY_FREE_SURFACE_METHOD', '3')
    sidecar.write_text(json.dumps(stored))
    before = sidecar.read_bytes()
    monkeypatch.setattr(campaign, 'generate', lambda spec: pytest.fail('stale shard silently regenerated'))
    with pytest.raises(ValueError, match='physics|free.surface|binding|component'):
        campaign.shard((tmp_path, spec))
    assert sidecar.read_bytes() == before


def test_manifest_does_not_relabel_old_archive(tmp_path):
    path = tmp_path / 'manifest.json'
    archive = dict(campaign_version='6.0.0', groups={'random_yard/medium': {'shards': []}})
    path.write_text(json.dumps(archive))
    before = path.read_bytes()
    with pytest.raises(ValueError, match='stale campaign manifest'):
        campaign.write_manifests(tmp_path, [], 'finalized', 0.)
    assert path.read_bytes() == before


def test_manifest_does_not_rebind_current_labels_to_changed_physics(tmp_path):
    path = tmp_path / 'manifest.json'
    archive = dict(campaign_version=campaign.CAMPAIGN_VERSION, physics={'sha256': 'old-inputs'},
                   groups={'random_yard/medium': {'shards': []}})
    path.write_text(json.dumps(archive))
    before = path.read_bytes()
    with pytest.raises(ValueError, match='stale physics manifest'):
        campaign.write_manifests(tmp_path, [], 'finalized', 0.)
    assert path.read_bytes() == before
