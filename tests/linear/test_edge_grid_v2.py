"""Independent v2 design: frozen original grid, denser training, fresh holdout."""
import hashlib
import importlib.util
import json
from pathlib import Path

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(scope="module")
def sampler():
    path = ROOT/"experiments/linear-model/epoch_requalification/edge_grid_v2.py"
    spec = importlib.util.spec_from_file_location("edge_grid_v2_design_test", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_training_preserves_original_grid_and_adds_512_requests(sampler):
    base = sampler.base_sampler()
    original = base.specifications("train")
    actual = sampler.specifications("train", base)
    without_id = lambda row: {key: value for key, value in row.items() if key != "state_id"}
    assert [without_id(row) for row in actual[:128]] == [without_id(row) for row in original]
    assert len(actual) == 640
    assert all(row["kind"] == "dense-v2-calibration" for row in actual[128:])


def test_dense_training_covers_all_fill_branches_across_mass_range(sampler):
    base = sampler.base_sampler()
    requests = sampler.specifications("train", base)[128:]
    assert {pattern: sum(row["fill_pattern"] == pattern for row in requests)
        for pattern in ("uniform", "staggered", "independent-offgrid")} == {
            "uniform": 192, "staggered": 64, "independent-offgrid": 256}
    for fill in (0., .5, .98-1e-8, .98, .98+1e-8, 1.):
        branch = [row for row in requests if row["fill_pattern"] == "uniform" and row["fill_fraction"] == fill]
        assert len(branch) == 32
        assert min(row["mass_fraction"] for row in branch) < .35
        assert max(row["mass_fraction"] for row in branch) > .9
        assert any(row["trim_fraction"] < -.5 for row in branch)
        assert any(row["trim_fraction"] > .5 for row in branch)
        assert np.array_equal(base.tank_fractions(branch[0], 8), np.full(8, fill))


def test_dense_training_covers_deep_and_interior_attitudes(sampler):
    requests = sampler.specifications("train", sampler.base_sampler())[128:]
    assert sum(row["mass_fraction"] >= .75 for row in requests) >= 256
    for name in ("trim_fraction", "heel_fraction"):
        assert min(row[name] for row in requests) < -.99
        assert max(row[name] for row in requests) > .99
    assert min(row["dry_kg_fraction"] for row in requests) < .01
    assert max(row["dry_kg_fraction"] for row in requests) > .99


def test_new_holdouts_are_reproducible_and_disjoint_from_failed_v1(sampler):
    base = sampler.base_sampler()
    failed_design = base.specifications("holdout")
    actual = sampler.specifications("holdout", base)
    assert sampler.SEEDS == {"train": 54933, "holdout": 54934}
    assert len(actual) == 256
    assert actual == sampler.specifications("holdout", sampler.base_sampler())
    content = lambda row: tuple(row[name] for name in ("mass_fraction", "trim_fraction", "heel_fraction", "dry_kg_fraction", "fill_seed"))
    assert not {content(row) for row in actual}.intersection(content(row) for row in failed_design)
    training = sampler.specifications("train", sampler.base_sampler())
    assert len({row["state_id"] for row in actual+training}) == 896


def test_v2_pilot_pins_both_producers_and_is_not_qualification():
    path = ROOT/"experiments/linear-model/fix-t3-linear/claude-edge-v2-pilot-data/feeder_500/necessary-edges-train.json"
    record = json.loads(path.read_text())
    assert record["pilot"] is True and record["complete"] is False
    assert record["attempts"] == 4
    assert record["sampler_builder_version"] == "2.0"
    for key in ("sampler_source", "sampler_base_source"):
        pin = record[key]
        assert hashlib.sha256((ROOT/pin["path"]).read_bytes()).hexdigest() == pin["sha256"]
    assert record["sampler_base_source"] == record["sampler_config"]["sampler_base_source"]
    assert record["sampler_config"]["independent_failed_holdout_seed_excluded"] == 54932
