"""Metadata-only contracts: no generators, planners, or real map reads."""
from collections import Counter
import json
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import test_v3_identity as guards


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value))


def original():
    rows = []
    for rung in ("tiny", "small"):
        for i in range(15):
            rows.append(dict(split="test", rung=rung, vessel=guards.SHIPS[i % 3],
                             generator=guards.FAMILIES[((i // 3) + i % 3) % 2],
                             seed=8_000_000 + (100_000 if rung == "small" else 0) + i,
                             port=("laem_chabang_like", "singapore_like")[i % 2],
                             noise=(0.2, 0.5, 0.8)[i % 3], max_attempts=100))
    return rows


def test_fresh_specs_preserve_tiny_and_expand_small_balanced(tmp_path):
    old = original()
    write(tmp_path / "test.requested.json", {"specs": old})
    fresh = guards.make_specs(tmp_path)
    assert len(fresh) == 45
    counts = Counter((s["rung"], s["vessel"], s["generator"]) for s in fresh)
    for ship in guards.SHIPS:
        assert sum(counts["tiny", ship, f] for f in guards.FAMILIES) == 5
        assert [counts["small", ship, f] for f in guards.FAMILIES] == [5, 5]
    for previous, current in zip(old[:15], fresh[:15]):
        assert {k: v for k, v in previous.items() if k not in {"seed", "split"}} == {
            k: v for k, v in current.items() if k not in {"seed", "split"}}
    assert [s["seed"] for s in fresh[:15]] == list(range(187_000_000, 187_000_015))
    assert [s["seed"] for s in fresh[15:]] == list(range(187_100_000, 187_100_030))


def test_guard_includes_unemitted_archived_and_rejected_seeds_without_maps(tmp_path):
    rows = original()
    write(tmp_path / "test.requested.json", {"specs": rows})
    write(tmp_path / "archive/v1/train.requested.json", {"specs": [{**rows[0], "seed": 111}]})
    write(tmp_path / "train/a.audit.json", {"config": {**rows[0], "seed": 222}, "instance_sha256": "a" * 64})
    write(tmp_path / "showcase/a.selection.json", [{"seed": 333, "accepted": False}])
    # Invalid JSON proves the helper never loads raw instances.
    (tmp_path / "train/a.json").write_text("RAW MAP MUST NOT BE READ")
    write(tmp_path / "test-v3/own.audit.json", {"config": {**rows[0], "seed": 444}})
    inventory = guards.exclusion_inventory(tmp_path)
    assert {111, 222, 333} <= inventory["seeds"]
    assert 444 not in inventory["seeds"]
    assert inventory["instance_sha256"] == {"a" * 64}
    assert inventory["proof"]["metadata_file_count"] == 4
    fresh = guards.make_specs(tmp_path)
    assert guards.assert_disjoint(fresh, inventory, ["b" * 64, "c" * 64])
    with pytest.raises(ValueError, match="SHA256 collides"):
        guards.assert_disjoint(fresh, inventory, ["a" * 64])
    with pytest.raises(ValueError, match="seed collides"):
        guards.assert_disjoint([{**fresh[0], "seed": 111}], inventory)
    with pytest.raises(ValueError, match="Duplicate fresh map"):
        guards.assert_disjoint(fresh, inventory, ["b" * 64, "b" * 64])


def test_guard_rejects_wrong_v21_size(tmp_path):
    write(tmp_path / "test.requested.json", {"specs": original()[:-1]})
    with pytest.raises(ValueError, match="15 tiny and 15 small"):
        guards.make_specs(tmp_path)
