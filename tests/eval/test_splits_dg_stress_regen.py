"""Release selection keeps DG-stress regeneration separate from the main set."""
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[2]
HERE = ROOT / "experiments/campaign-g1"


@pytest.fixture
def regen(monkeypatch):
    monkeypatch.syspath_prepend(str(HERE))
    spec = importlib.util.spec_from_file_location("dg_stress_test_regen", HERE / "regen.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_regen_selects_frozen_stress_by_name_without_reallocation(regen, tmp_path):
    from eval.splits import load_manifest
    release = regen.build_release("e", split_release="dg-stress-v3")
    assert release == load_manifest("dg-stress-v3")
    jobs = regen.make_jobs(release, tmp_path, False, 2, 1, "e")
    assert len(jobs) == 45
    assert {job["config"]["cargo_profile"] for job in jobs} == {"dg_stress"}
    assert all(job["allocated"]["split"] == "test" for job in jobs)
    assert regen.build_release("e")["version"] == "v2"


@pytest.mark.parametrize("superseded", ["dg-stress-v1", "dg-stress-v2"])
def test_superseded_stress_cannot_be_selected_or_used_directly(regen, tmp_path, superseded):
    old = json.loads((ROOT / "experiments/splits" / superseded / "manifest.json").read_text())
    with pytest.raises(ValueError, match="superseded|supported frozen"):
        regen.build_release("e", split_release=superseded)
    with pytest.raises(ValueError, match="superseded"):
        regen.make_jobs(old, tmp_path, False, 2, 1, "e")
    with pytest.raises(ValueError, match="superseded"):
        regen._write_indices([], old, tmp_path, "e", True)


def test_random_only_diagnostic_does_not_require_a_simulated_spec(regen):
    from eval.splits import load_manifest
    current = load_manifest()
    release = {**current, "entries": [entry for entry in current["entries"]
                                     if entry["spec"]["generator"] == "random_yard"]}
    selected = regen.selected_entries(release, True, 2)
    assert len(selected) == 2
    assert all(entry["spec"]["generator"] == "random_yard" for entry in selected)


def test_regen_preserves_pair_and_equipment_selection_in_job_config(regen):
    from eval.splits import load_manifest
    entry = load_manifest()["entries"][0]
    spec = {**entry["spec"], "cargo_profile": "dg_stress", "identity_version": "v3",
            "equipment_profile": "dry20_dg_stress", "dg_stress_pair": "flammable_solid_flammable_liquid"}
    config = regen._release_config({**entry, "spec": spec}, 1)
    assert config["dg_stress_pair"] == "flammable_solid_flammable_liquid"
    assert config["equipment_profile"] == "dry20_dg_stress"
    assert config["identity_version"] == "v3"


def test_regen_indices_use_selected_release_pin_and_full_allocation(regen, tmp_path):
    release = regen.build_release("e", split_release="dg-stress-v3")
    index = regen._write_indices([], release, tmp_path, "e", True, tranches={})
    assert index["split_version"] == "dg-stress-v3"
    assert index["split_entries_sha256"] == release["entries_sha256"]
    assert len(index["not_generated_spec_keys"]) == 45
    assert index["count"] == index["rejected"] == 0
    assert index["allocation_complete"] is False
    recipe = index["selection_audit"]["requested_recipe"]
    assert Path(recipe["path"]).name == "dg_stress.toml"
    assert 'profile_id = "synthetic_dg_stress_d70"' in recipe["text"]


def test_regen_release_argument_keeps_output_and_pins_separate(regen, tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(regen, "HERE", tmp_path)
    monkeypatch.setattr(regen, "check_manifest", lambda *a: {
        "epoch_id": "same", "diagnostic": True, "label": "diagnostic"})
    monkeypatch.setattr(regen, "qualification_preflight", lambda *a: {
        "path": str(HERE / "regen.py"), "sha256": regen.sha256_file(HERE / "regen.py")})
    args = ["--diagnostic", "--plan-only"]
    assert regen.main(args) == 0
    main = json.loads(capsys.readouterr().out)
    assert regen.main([*args, "--split-release", "dg-stress-v3"]) == 0
    stress = json.loads(capsys.readouterr().out)
    assert stress["split_version"] == "dg-stress-v3"
    assert stress["allocated_specs"] == 45
    assert stress["instance_root"] != main["instance_root"]
    assert Path(stress["split_manifest"]).parent.name == "dg-stress-v3"
    assert "separate" in stress["publication"]


def test_regen_cannot_bind_another_releases_record(regen, tmp_path):
    release = regen.build_release("e", split_release="dg-stress-v3")
    other = regen.build_release("e")
    job = regen.make_jobs(other, tmp_path, True, 2, 1, "e")[0]
    with pytest.raises(ValueError, match="selected.*allocation"):
        regen._write_indices([dict(job=job, result={})], release, tmp_path, "e", True)


def test_regen_cannot_index_stress_identity_without_producer_adoption(regen, tmp_path, monkeypatch):
    from eval import splits
    from eval.snapshot_io import FileSnapshot
    release = regen.build_release("e", split_release="dg-stress-v3")
    job = regen.make_jobs(release, tmp_path, False, 2, 1, "e")[0]
    path = Path(job["root"]) / job["config"]["generator"] / "tiny/accepted.json"
    path.parent.mkdir(parents=True)
    path.write_text("{}")
    monkeypatch.setattr(FileSnapshot, "model", lambda *a: SimpleNamespace(containers=[], load_list=[]))
    monkeypatch.setattr(splits, "spec_from_instance", lambda _: job["allocated"]["spec"])
    with pytest.raises(ValueError, match="DG-stress.*producer.*adoption"):
        regen._write_indices([dict(job=job, result=dict(path=str(path), audit={"status": "accepted"}))],
                             release, tmp_path, "e", True)
