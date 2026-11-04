"""Replacement split release is reproducible and separates underlying draws."""
from collections import defaultdict
import importlib.util
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
_spec = importlib.util.spec_from_file_location("split_freeze", ROOT / "experiments/splits/freeze.py")
freeze = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(freeze)


def test_replacement_inventory_never_reuses_candidate_seed_across_partitions():
    """v1 seed-0 cargo candidates crossed train/test at different yard noise."""
    partitions = defaultdict(set)
    for entry in freeze.build_manifest()["entries"]:
        partitions[entry["spec"]["seed"]].add(entry["split"])
    assert all(len(labels) == 1 for labels in partitions.values())


def test_replacement_inventory_has_unique_seeds_across_all_generators_and_families():
    entries = freeze.build_manifest()["entries"]
    assert len({entry["spec"]["seed"] for entry in entries}) == len(entries) == 9400
    assert {entry["spec"]["generator"] for entry in entries} == {"simulated", "random_yard"}
    assert {entry["test_family"] for entry in entries if entry["split"] == "test"} == {
        "in_distribution", "heldout_port", "heldout_noise", "heldout_slice"}


def test_replacement_inventory_explicitly_requests_current_regional_demand():
    for entry in freeze.build_manifest()["entries"]:
        controls = entry["spec"]
        assert controls["identity_version"] == "v2"
        assert controls["n_boxes"] is None
        assert controls["dense_20ft"] is False
        assert controls["tiny_dg_enabled"] is True
        assert controls["without_dg"] is False
        expected = "dry20_with_dg" if controls["rung"] == "tiny" else "full_mix"
        assert controls["equipment_profile"] == expected


def test_replacement_release_is_frozen_and_pinned():
    from eval.splits import load_manifest
    rebuilt = freeze.build_manifest("v2")
    path = ROOT / "experiments/splits/v2/manifest.json"
    assert load_manifest(path) == rebuilt
    assert path.read_text() == json.dumps(rebuilt, indent=2, sort_keys=True) + "\n"


def test_historical_release_reproduces_unchanged_bytes():
    path = ROOT / "experiments/splits/v1/manifest.json"
    rebuilt = freeze.build_manifest("v1")
    assert path.read_text() == json.dumps(rebuilt, indent=2, sort_keys=True) + "\n"
    assert rebuilt["entries_sha256"] == "b776af130fdeee71e622d08c535e5fdfdb30053ae52b843042ca817ebd8f143c"


def test_random_candidate_attempt_ranges_do_not_alias_between_frozen_specs():
    """The current random producer uses seed*1_000_000+attempt for initial cargo."""
    seeds = sorted(entry["spec"]["seed"] for entry in freeze.build_manifest()["entries"]
                   if entry["spec"]["generator"] == "random_yard")
    # Inclusive ranges at the largest supported release runtime cap. Smaller
    # retry budgets (the current normal default is 200) retain these prefixes.
    ends = [seed * 1_000_000 + 999_999 for seed in seeds]
    starts = [seed * 1_000_000 for seed in seeds]
    assert all(end < start for end, start in zip(ends, starts[1:]))
