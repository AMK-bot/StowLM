"""CPU checks for the leader's current-epoch base-mask run preparation."""
import importlib.util
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]


def driver():
    spec = importlib.util.spec_from_file_location("basemask_val_driver", ROOT / "experiments/base-mask/make_val_v3.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_val_runner_uses_exact_frozen_v2_allocations():
    from eval.splits import load_inventory, spec_key
    from stowage.generate.instances_v3 import split_metadata

    inventory = load_inventory()
    specs = driver().build_specs(profiles=["small"], c2=True, max_attempts=200)
    expected = {spec_key(row["spec"]) for row in inventory.manifest["entries"]
                if row["split"] == "val" and row["spec"]["rung"] == "small"}
    assert len(specs) == len(expected) == 25
    assert {split_metadata(spec)["spec_key"] for spec in specs} == expected
    for spec in specs:
        metadata = split_metadata(spec)
        inventory.require_membership(metadata["split_spec"], split="val")
        assert metadata["split_version"] == "v2" and spec["c2_screened"] is True
        assert spec["split_entries_sha256"] == inventory.entries_sha256


def test_val_runner_rejects_retry_ranges_that_cross_draw_groups():
    with pytest.raises(ValueError, match="1,000,000"):
        driver().build_specs(profiles=["small"], c2=True, max_attempts=1_000_001)
