"""Refuse execution until active-release producer and cohort adoption is verified.

The spy executes real campaign dispatch and stops at its producer entry before
physics or any cargo draw. This does not establish sampler adoption.
"""
import importlib.util
import inspect
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
HERE = ROOT / "experiments/campaign-g1"


@pytest.fixture
def regen(monkeypatch):
    monkeypatch.syspath_prepend(str(HERE))
    spec = importlib.util.spec_from_file_location("dg_stress_adoption_regen", HERE / "regen.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def stress_config(regen):
    release = regen.build_release("probe", split_release="dg-stress-v3")
    entry = next(entry for entry in release["entries"]
                 if entry["spec"]["dg_stress_pair"] == "flammable_solid_flammable_liquid")
    return regen._release_config(entry, 1)


def test_campaign_adapter_passes_actual_profile_and_cohort_to_producer(stress_config, monkeypatch):
    from stowage.generate import instances_v3, instances_v2_random
    from stowage.rules.catalogue import rule_freeze_v1

    class StopBeforePhysics(Exception):
        pass

    original = instances_v2_random.generate_random_v2
    observed = []

    def stop_before_physics(*args, **kwargs):
        bound = inspect.signature(original).bind(*args, **kwargs)
        bound.apply_defaults()
        observed.append(dict(bound.arguments))
        raise StopBeforePhysics

    monkeypatch.setattr(instances_v2_random, "generate_random_v2", stop_before_physics)
    monkeypatch.setattr(instances_v3, "physics_metadata", lambda **_: {"sha256": "probe"})
    with rule_freeze_v1(enabled=True), pytest.raises(StopBeforePhysics):
        instances_v3.generate(stress_config)

    assert len(observed) == 1
    assert stress_config["cargo_profile"] == "dg_stress"
    assert observed[0]["cargo_profile"] == stress_config["cargo_profile"]
    assert observed[0]["dg_stress_pair"] == stress_config["dg_stress_pair"]
    tags = instances_v3.family_tags(stress_config, 1)
    assert tags["cargo_profile"] == "dg_stress"
    assert tags["equipment_profile"] == stress_config["equipment_profile"]


def test_stress_admission_refuses_until_actual_producer_adoption(regen, stress_config):
    with pytest.raises(ValueError, match="DG-stress.*producer.*adoption"):
        regen._require_stress_producer_adoption(stress_config)


def test_main_export_admission_does_not_require_stress_adoption(regen):
    assert regen._require_stress_producer_adoption({"cargo_profile": "feeder_export"}) is None


def test_worker_refuses_stress_before_cache_mutation_or_producer(regen, stress_config, monkeypatch):
    from stowage.generate import instances_v3
    called = []
    monkeypatch.setattr(instances_v3, "shard", lambda *_: called.append(True))
    # Deliberately provide no cache paths: refusal must precede cache setup.
    with pytest.raises(ValueError, match="DG-stress.*producer.*adoption"):
        regen._worker({"config": stress_config})
    assert not called


def test_worker_refuses_profile_mismatch_before_generation(regen, stress_config):
    allocated = {"spec": {"cargo_profile": "dg_stress"}}
    with pytest.raises(ValueError, match="cargo profile mismatch"):
        regen._worker({"config": {**stress_config, "cargo_profile": "feeder_export"},
                       "allocated": allocated})


def test_execution_refuses_before_writing_or_resuming_but_plan_remains_available(
        regen, tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(regen, "HERE", tmp_path)
    monkeypatch.setattr(regen, "check_manifest", lambda *a: {
        "epoch_id": "adoption", "diagnostic": True, "label": "diagnostic"})
    monkeypatch.setattr(regen, "qualification_preflight", lambda *a: {
        "path": str(HERE / "regen.py"), "sha256": regen.sha256_file(HERE / "regen.py")})
    args = ["--diagnostic", "--split-release", "dg-stress-v3"]
    assert regen.main([*args, "--plan-only"]) == 0
    capsys.readouterr()
    assert regen.main(args) == 1
    assert "DG-stress producer adoption" in capsys.readouterr().err
    assert not (tmp_path / "adoption").exists()
