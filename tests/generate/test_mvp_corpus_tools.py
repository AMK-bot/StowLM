"""Fast release-contract tests; no hull construction, solver or model weights."""
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

import pytest


TOOLS = Path(__file__).resolve().parents[2] / "experiments/mvp/corpus_tools"


def load_tool(name):
    spec = importlib.util.spec_from_file_location(f"test_mvp_tool_{name}", TOOLS / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def build():
    return load_tool("build")


@pytest.fixture
def freeze():
    return load_tool("freeze")


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, sort_keys=True))


def test_test_planning_balances_all_ships_rungs_and_families(build):
    from collections import Counter
    specs = build.specs_for("test", dict.fromkeys(build.RUNGS, 15))
    assert len(specs) == 45
    assert Counter(s["rung"] for s in specs) == dict.fromkeys(build.RUNGS, 15)
    assert Counter((s["vessel"], s["rung"]) for s in specs) == {
        (vessel, rung): 5 for vessel in build.VESSELS for rung in build.RUNGS}
    for vessel in build.VESSELS:
        for rung in build.RUNGS:
            family_counts = Counter(s["generator"] for s in specs if s["vessel"] == vessel and s["rung"] == rung)
            assert sorted(family_counts.values()) == [2, 3]


def test_root_seeds_are_unique_across_settings_and_partitions(build):
    specs = [s for split in ("test", "train", "val")
             for s in build.specs_for(split, {"tiny": 101, "small": 102, "medium": 103})]
    seeds = [s["seed"] for s in specs]
    assert len(seeds) == len(set(seeds))
    assert not any(9_000_100 <= seed < 9_000_410 for seed in seeds)
    assert build.specs_for("test", {"tiny": 0, "small": 0, "medium": 0}) == []


@pytest.fixture
def synthetic_artifact(tmp_path, freeze, monkeypatch):
    instances = {}
    monkeypatch.setattr(freeze, "_load_instance", lambda path: instances[Path(path)])
    monkeypatch.setattr(freeze, "_require_clean", lambda instance: None)
    monkeypatch.setattr(freeze, "_requires_gate", lambda instance: instance.gate_required)
    monkeypatch.setattr(freeze, "_canonical_full_cell_ids", lambda vessel: {"fixture-cell-a", "fixture-cell-b"})

    def create(path, config, *, gate=True, showcase=False):
        write_json(path, {"synthetic_release_contract_fixture": True, "seed": config["seed"]})
        rung = config.get("rung", config.get("size"))
        instance = SimpleNamespace(seed=config["seed"], size_class=rung,
            family={"vessel": config["vessel"], "port": config["port"]},
            load_list=["fixture-target"], voyage=object(), schema_version="1.9",
            containers=[SimpleNamespace(id="fixture-target", dg=object(), reefer=True)],
            yard=SimpleNamespace(stacks=[SimpleNamespace(boxes=["fixture-target", "non-target-blocker"])]),
            provenance=SimpleNamespace(versions={"schema": "1.9", "fixture_generator": "1.0"}),
            vessel=SimpleNamespace(nominal_teu=1604 if rung == "full" else 6,
                cells=[SimpleNamespace(id="fixture-cell-a"), SimpleNamespace(id="fixture-cell-b")],
                fixed_loads=[]), gate_required=gate)
        instances[path] = instance
        audit = dict(config=config, accepted=True, rule_freeze_v1_enabled=False,
            instance_sha256=freeze.sha256(path), instance_versions=dict(instance.provenance.versions),
            requested_targets=1, elapsed_generation_s=0.25, background_seconds=0.1,
            background_checks=int(gate), background_last={"accepted": True, "physics_sha256": "fixture-physics"},
            source_files={"fixture.py": "same"})
        if showcase:
            audit.update(showcase_features={"dg_targets": 1, "reefer_targets": 1, "buried_targets": 1},
                         hero_view_only=rung == "full")
        write_json(path.with_suffix(".audit.json"), audit)
        return instance, audit

    return create


def test_artifact_checks_reject_tampering_and_missing_gate(tmp_path, freeze, synthetic_artifact):
    config = dict(seed=1, vessel="feeder_500", rung="tiny", generator="random_yard", port="laem_chabang_like")
    path = tmp_path / "fixture.json"
    _, audit = synthetic_artifact(path, config)
    freeze.validate_artifact(path, current_sources={"fixture.py": "same"})
    path.write_text("tampered")
    with pytest.raises(ValueError, match="checksum"):
        freeze.validate_artifact(path, current_sources={})
    _, audit = synthetic_artifact(path, config)
    audit["background_last"]["accepted"] = False
    write_json(path.with_suffix(".audit.json"), audit)
    with pytest.raises(ValueError, match="D58"):
        freeze.validate_artifact(path, current_sources={})
    audit["background_last"]["accepted"] = True
    audit["accepted"] = False
    write_json(path.with_suffix(".audit.json"), audit)
    with pytest.raises(ValueError, match="producer did not accept"):
        freeze.validate_artifact(path, current_sources={})


def test_source_drift_is_explicit_and_optionally_rejected(tmp_path, freeze, synthetic_artifact):
    path = tmp_path / "fixture.json"
    synthetic_artifact(path, dict(seed=1, vessel="feeder_500", rung="tiny", generator="random_yard", port="laem_chabang_like"))
    _, _, record = freeze.validate_artifact(path, current_sources={"fixture.py": "changed"})
    assert record["source_binding_status"] == "drift"
    assert record["changed_source_files"] == ["fixture.py"]
    with pytest.raises(ValueError, match="source binding drift"):
        freeze.validate_artifact(path, current_sources={"fixture.py": "changed"}, strict_sources=True)


@pytest.fixture
def synthetic_release(tmp_path, build, freeze, synthetic_artifact):
    def inventory(split, counts, *, emitted=None):
        specs = build.specs_for(split, counts)
        write_json(tmp_path / f"{split}.requested.json", dict(split_policy=build.POLICY, counts=counts, specs=specs))
        for config in specs[:emitted]:
            synthetic_artifact(build.shard_path(config, tmp_path), config)
        return specs

    inventory("test", dict.fromkeys(build.RUNGS, 15))
    records = []
    for i, (ship, rung) in enumerate((("feeder_500", "small"), ("feeder_800", "medium"),
                                      ("feeder_1700", "medium"), ("feeder_1700", "full"))):
        config = dict(vessel=ship, size=rung, generator="random_yard", seed=9_000_100 + i * 100,
                      port="laem_chabang_like")
        path = tmp_path / "showcase" / f"{ship}-{rung}.json"
        _, audit = synthetic_artifact(path, config, showcase=True)
        records.append({k: audit[k] for k in ("config", "instance_sha256", "instance_versions")})
    write_json(tmp_path / "showcase/showcase-manifest.json",
               dict(rule_freeze_v1_enabled=False, instances=4, records=records, hashes={}))
    return tmp_path, inventory


def test_partial_freeze_requires_test_and_showcase_and_reports_zero_rows(freeze, synthetic_release):
    root, _ = synthetic_release
    manifest = freeze.assemble_manifest(root, partial=True, current_sources={"fixture.py": "same"})
    assert manifest["counts"] == {"test": 45, "showcase": 4, "train": 0, "val": 0}
    assert manifest["complete"] is False
    assert manifest["missing_requested_inventories"] == ["train", "val"]
    assert len(manifest["requested_vs_emitted"]) == 78
    assert all(row["requested"] == row["emitted"] == 0
               for row in manifest["requested_vs_emitted"] if row["split"] in ("train", "val"))
    with pytest.raises(ValueError, match="missing requested inventory"):
        freeze.assemble_manifest(root, current_sources={})


def test_incomplete_train_is_counted_without_claiming_complete(freeze, synthetic_release):
    root, inventory = synthetic_release
    inventory("train", {"tiny": 0, "small": 3, "medium": 6}, emitted=2)
    inventory("val", {"tiny": 0, "small": 0, "medium": 0})
    manifest = freeze.assemble_manifest(root, current_sources={"fixture.py": "same"})
    assert manifest["counts"]["train"] == 2
    assert manifest["requested_counts"]["train"] == 9
    assert manifest["complete"] is False
    train_rows = [r for r in manifest["requested_vs_emitted"] if r["split"] == "train"]
    assert sum(r["pending"] for r in train_rows) == 7
    assert sum(r["generation_seconds"] for r in train_rows) == 0.5
    assert sum(r["background_gate_seconds"] for r in train_rows) == 0.2


def test_incomplete_freeze_ignores_atomic_temporary_files(freeze, synthetic_release):
    root, _ = synthetic_release
    temporary_paths = [root / "test/tiny/.seed-999999999.123.partial.json",
                       root / "test/seed-999999999.partial.json",
                       root / "showcase/.feeder_1700-full.partial.json",
                       root / "train/.work/seed-999999999.json",
                       root / "train/.work/seed-999999999.audit.json"]
    for path in temporary_paths:
        write_json(path, {"unfinished": True})
    manifest = freeze.assemble_manifest(root, partial=True, current_sources={"fixture.py": "same"})
    assert manifest["counts"]["test"] == 45
    assert manifest["counts"]["showcase"] == 4
    assert manifest["counts"]["train"] == 0
    assert not manifest["uncommitted_instance_files"]


def test_release_records_tool_and_token_allocation_hashes(freeze, synthetic_release):
    root, _ = synthetic_release
    allocation = root / "token_allocation.json"
    write_json(allocation, {"fixture_allocation": True})
    manifest = freeze.assemble_manifest(root, partial=True, current_sources={"fixture.py": "same"})
    assert manifest["hashes"]["token_allocation.json"] == freeze.sha256(allocation)
    assert manifest["tool_files_sha256"]["experiments/mvp/corpus_tools/freeze.py"] == freeze.sha256(TOOLS / "freeze.py")
    assert "experiments/mvp/corpus_tools/README.md" in manifest["tool_files_sha256"]


def test_hero_checks_full_geometry_instead_of_nominal_family_label(freeze, synthetic_release, monkeypatch):
    root, _ = synthetic_release
    # The full fixture intentionally reports nominal_teu=1604, as the real
    # reconditioned producer does. Complete cell coverage is the authority.
    manifest = freeze.assemble_manifest(root, partial=True, current_sources={"fixture.py": "same"})
    assert any(r["rung"] == "full" and r["hero_view_only"] for r in manifest["files"])
    monkeypatch.setattr(freeze, "_canonical_full_cell_ids", lambda vessel: {"fixture-cell-a"})
    with pytest.raises(ValueError, match="full feeder_1700 geometry"):
        freeze.assemble_manifest(root, partial=True, current_sources={"fixture.py": "same"})


def test_duplicate_root_seed_fails_before_freeze(freeze, synthetic_release):
    root, inventory = synthetic_release
    specs = inventory("train", {"tiny": 1}, emitted=0)
    specs[0]["seed"] = 8_000_000
    write_json(root / "train.requested.json", dict(split_policy=load_tool("build").POLICY, counts={"tiny": 1}, specs=specs))
    with pytest.raises(ValueError, match="root seed .* reused"):
        freeze.assemble_manifest(root, partial=True, current_sources={})


def test_verification_detects_frozen_metadata_changes(freeze, synthetic_release, monkeypatch):
    root, _ = synthetic_release
    build_module = load_tool("build")
    monkeypatch.setattr(build_module, "source_snapshot", lambda: {"fixture.py": "same"})
    monkeypatch.setattr(freeze, "_build_module", lambda: build_module)
    manifest = freeze.write_freeze(root, partial=True)
    assert freeze.verify_freeze(root)["counts"] == manifest["counts"]
    assert (root / "test/SHA256SUMS").exists()
    (root / "test.requested.json").write_text("{}")
    with pytest.raises(ValueError, match="frozen checksum mismatch"):
        freeze.verify_freeze(root)


def test_partial_verification_allows_planned_training_shards_to_finish(freeze, synthetic_release, synthetic_artifact, monkeypatch):
    root, inventory = synthetic_release
    specs = inventory("train", {"tiny": 1}, emitted=0)
    inventory("val", {"tiny": 0})
    build_module = load_tool("build")
    monkeypatch.setattr(build_module, "source_snapshot", lambda: {"fixture.py": "same"})
    monkeypatch.setattr(freeze, "_build_module", lambda: build_module)
    frozen = freeze.write_freeze(root, partial=True)
    # Finishing a predeclared train shard must not invalidate frozen test hashes.
    synthetic_artifact(build_module.shard_path(specs[0], root), specs[0])
    assert freeze.verify_freeze(root)["counts"] == frozen["counts"]
