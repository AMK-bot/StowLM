"""Audit the open development cohort and the sealed cohort's bytes only.

Sealed map files are never decoded, loaded as instances, or inspected here.
Their only allowed read is the streaming SHA-256 operation in ``_sha``.
"""
from __future__ import annotations

from collections import Counter
import hashlib
import importlib.util
import json
from pathlib import Path

import pytest


HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[3]
CELLS = {(ship, fill, family)
         for ship in ("feeder_500", "feeder_800")
         for fill in (60, 70)
         for family in ("random_yard", "simulated_yard")}


def _sha(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _metadata(name: str):
    """Read cohort-level metadata, never a map file."""
    path = HERE / name
    assert path.parent == HERE and path.name != "sealed"
    return json.loads(path.read_text())


def _map_path(row) -> Path:
    path = (ROOT / row["path"]).resolve()
    assert path.is_relative_to((HERE / row["split"]).resolve())
    assert path.name.endswith(".json")
    return path


def _cell(row):
    return row["ship"], row["fill_target"], row["generator"]


@pytest.fixture(scope="module")
def manifest():
    return _metadata("MANIFEST.json")


def test_map_counts_cells_and_files(manifest):
    rows = manifest["maps"]
    assert len(rows) == 56
    assert len({row["path"] for row in rows}) == 56
    assert len({row["seed"] for row in rows}) == 56
    assert all(row["schema_valid"] is True for row in rows)
    assert {row["split"] for row in rows} == {"dev", "sealed"}
    for split, repeats in (("dev", 4), ("sealed", 3)):
        selected = [row for row in rows if row["split"] == split]
        assert Counter(_cell(row) for row in selected) == Counter({cell: repeats for cell in CELLS})
        expected = {_map_path(row) for row in selected}
        assert set((HERE / split).glob("*.json")) == expected


def test_all_map_hashes_without_opening_sealed_content(manifest):
    for row in manifest["maps"]:
        assert _sha(_map_path(row)) == row["sha256"], row["path"]


def test_source_and_parameter_hashes(manifest):
    files = manifest["files"]
    assert "experiments/mvp/pipeline/maps/generate.py" in files
    for ship in ("feeder_500", "feeder_800"):
        assert any(name.endswith(f"params/fleet/{ship}.toml") for name in files)
    for name, digest in files.items():
        path = (ROOT / name).resolve()
        assert path.is_relative_to(ROOT)
        assert not path.is_relative_to(HERE / "sealed")
        assert _sha(path) == digest, name


def test_registered_seeds_disjoint_from_prior_sets(manifest):
    seeds = {row["seed"] for row in manifest["maps"]}
    cells = [(ship, fill, family)
             for ship in ("feeder_500", "feeder_800")
             for fill in (60, 70)
             for family in ("random_yard", "simulated_yard")]
    for split, offset, repeats in (("dev", 0, 4), ("sealed", 10_000, 3)):
        expected = {306_100_000 + offset + 100 * index + rep
                    for index in range(len(cells)) for rep in range(repeats)}
        assert {row["seed"] for row in manifest["maps"] if row["split"] == split} == expected
        for row in manifest["maps"]:
            if row["split"] == split:
                assert 0 <= row["rep"] < repeats
                assert row["seed"] == 306_100_000 + offset + 100 * cells.index(_cell(row)) + row["rep"]
    for name in ("SEED-AUDIT-PRE.json", "SEED-AUDIT-POST.json"):
        audit = _metadata(name)
        assert set(audit["seeds"]) == seeds
        assert audit["disjoint"] is True
        assert audit["collisions"] == []
        assert audit["errors"] == []
        assert audit["metadata_files_checked"] > 0
        assert audit["paths_checked"] > audit["metadata_files_checked"]
        assert audit["files"]
        assert audit["audit_script_sha256"] == _sha(HERE / "seed_audit.py")
    pre = _metadata('SEED-AUDIT-PRE.json')
    assert pre['raw_audit_sha256'] == _sha(HERE/'SEED-AUDIT-PRE-RAW.json')
    assert pre['raw_audit_script_sha256'] == _sha(HERE/'seed_audit_lexical_v1.py')
    assert pre['resolution_script_sha256'] == _sha(HERE/'resolve_seed_audit.py')
    assert pre['amendment_sha256'] == _sha(HERE/'AMENDMENT-1.md')
    assert len(pre['resolved_decimal_candidates']) == 4
    post = _metadata('SEED-AUDIT-POST.json')
    raw_post = _metadata('SEED-AUDIT-POST-RAW.json')
    assert post['raw_audit_sha256'] == _sha(HERE/'SEED-AUDIT-POST-RAW.json')
    assert post['post_resolution_script_sha256'] == _sha(HERE/'finalize.py')
    assert post['pre_audit_sha256'] == _sha(HERE/'SEED-AUDIT-PRE.json')
    assert raw_post['errors'] == []
    assert raw_post['audit_script_sha256'] == _sha(HERE/'seed_audit.py')
    assert set(raw_post['seeds']) == seeds
    assert post['sealed_seed_collisions'] == []
    allowed = post['allowed_development_uses']
    signature = lambda row: (row['path'], row['source'], tuple(row['seeds']))
    assert sorted(map(signature, allowed)) == sorted(map(signature, raw_post['collisions']))
    dev_ids = {row['seed']: f"pipeline.dev.{Path(row['path']).stem}.s{row['seed']}"
               for row in manifest['maps'] if row['split'] == 'dev'}
    sealed_seeds = {row['seed'] for row in manifest['maps'] if row['split'] == 'sealed'}
    prefix = ('experiments', 'mvp', 'frontier', 'engine', 'peel_ladder', 'assignment-ballast')
    for row in allowed:
        parts = Path(row['path']).parts
        assert row['source'] == 'path' and len(row['seeds']) == 1
        seed = row['seeds'][0]
        assert seed not in sealed_seeds and seed in dev_ids
        assert parts[:6] == prefix and parts[6].isdigit()
        assert parts[7] == dev_ids[seed]
    # Generation performs the fresh post-audit immediately before publishing.
    # Verify both pinned audit attestations here rather than repeating a third
    # multi-gigabyte repository scan with no intervening cohort mutation.


def test_dev_schema_and_envelope(manifest):
    from stowage.frozen import require_clean
    from stowage.io import load_instance

    for row in manifest["maps"]:
        if row["split"] != "dev":
            continue
        instance = load_instance(_map_path(row))
        require_clean(instance, what="pipeline development cohort test")
        assert instance.seed == row["seed"]
        assert instance.stage == "A"
        assert 0 < len(instance.load_list) <= 195
        assert not instance.onboard.placements
        assert not instance.discharge_list
        assert not instance.forecasts
        assert not instance.plan_calls
        cargo = {box.id: box for box in instance.containers}
        selected = [cargo[cid] for cid in instance.load_list]
        assert row['boxes'] == len(selected)
        assert row['teu'] == sum(box.length_ft / 20 for box in selected)
        assert row['ports'] == len(instance.rotation.calls)
        assert row['load_port_calls'] == 1 and row['onboard_boxes'] == 0
        assert {box.pol for box in selected} == {instance.port.locode}
        assert instance.rotation.calls[instance.call_index].locode == instance.port.locode
        physical_cells = sum(1 if box.length_ft == 20 else 2 for box in selected)
        fill = physical_cells / len(instance.vessel.cells)
        assert fill <= row["fill_target"] / 100 + 1e-12
        assert fill <= .70 + 1e-12
        if not row.get("cap_trimmed", False):
            assert fill >= row["fill_target"] / 100 - .025 - 1e-12


def test_dev_set_loads_with_lab_loader(manifest):
    adapter = HERE / "lab_set.py"
    spec = importlib.util.spec_from_file_location("pipeline_map_catalog_test", adapter)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    expected = [row["path"] for row in manifest["maps"] if row["split"] == "dev"]
    with module.approved_dev():
        from catalog import load_set
        assert load_set(HERE / "dev.txt") == expected
        from catalog import ensure_safe
        sealed = next(row for row in manifest["maps"] if row["split"] == "sealed")
        with pytest.raises(ValueError, match="outside the manifest-pinned dev cohort"):
            ensure_safe(sealed["path"])
