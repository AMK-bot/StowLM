"""Cheap tests for deterministic balanced allocation prefixes."""
import copy
from collections import Counter, defaultdict
from itertools import product
import random
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
from tranches import parse_train_counts, priority_entries, select_entries


def entry(seed, ship="a", generator="simulated", port="p", noise=0, rung="tiny", split="train", family=None):
    return dict(spec=dict(seed=seed, vessel=ship, generator=generator, port=port,
                         noise=noise, blocking_ratio=noise, rung=rung), split=split, test_family=family)


def allocation():
    rows = []
    for rung in ("tiny", "small", "medium", "full"):
        for split in ("train", "val", "test"):
            for ship, generator, port, noise in product(("a", "b", "c"), ("simulated", "random_yard"), ("p", "q"), (0, 1)):
                for seed in (8, 2, 5):
                    rows.append(entry(seed, ship, generator, port, noise, rung, split))
    return {"version": "v2", "entries": rows}


def test_parse_targets_and_errors():
    assert parse_train_counts("tiny=2,small=2,medium=1,full=0") == dict(tiny=2, small=2, medium=1, full=0)
    assert parse_train_counts(" tiny=3 ") == dict(tiny=3, small=0, medium=0, full=0)
    for value in ("", "tiny=-1", "tiny=1.5", "huge=1", "tiny=2,tiny=3", "tiny", "tiny=", "tiny=１"):
        with pytest.raises(ValueError):
            parse_train_counts(value)


def test_round_robin_cells_seed_order_and_early_axis_diversity():
    rows = [row for row in allocation()["entries"] if row["spec"]["rung"] == "tiny" and row["split"] == "train"]
    ordered = priority_entries(rows)
    cells = defaultdict(list)
    for row in ordered:
        spec = row["spec"]
        cells[spec["vessel"], spec["generator"], spec["port"], spec["noise"]].append(spec["seed"])
    assert all(seeds == [2, 5, 8] for seeds in cells.values())
    for count in range(1, len(ordered) + 1):
        seen = Counter((row["spec"]["vessel"], row["spec"]["generator"], row["spec"]["port"], row["spec"]["noise"])
                       for row in ordered[:count])
        totals = [seen[cell] for cell in cells]
        assert max(totals) - min(totals) <= 1
    for axis in ("vessel", "generator", "port", "noise"):
        assert len({row["spec"][axis] for row in ordered[:2]}) == 2


def test_cumulative_targets_extend_identical_train_prefix_and_preserve_specs():
    release = allocation()
    before = copy.deepcopy(release)
    a = select_entries(release, parse_train_counts("tiny=2,small=2,medium=1,full=0"), 1, True)
    b = select_entries(release, parse_train_counts("tiny=4,small=3,medium=2,full=1"), 1, True)
    assert release == before
    assert all(row in b for row in a)
    for rung, old_count in (("tiny", 2), ("small", 2), ("medium", 1), ("full", 0)):
        first = [row for row in a if row["split"] == "train" and row["spec"]["rung"] == rung]
        later = [row for row in b if row["split"] == "train" and row["spec"]["rung"] == rung]
        assert first == later[:old_count]
    shuffled = copy.deepcopy(release)
    random.Random(41).shuffle(shuffled["entries"])
    assert select_entries(shuffled, parse_train_counts("tiny=4,small=3,medium=2,full=1"), 1, True) == b
    assert len(a) == 5 + 8  # one val plus one test for every rung, even full=0 train


def test_validation_test_complete_and_caps_diagnostic_only():
    release = allocation()
    selected = select_entries(release, {"tiny": 2})
    assert Counter(row["split"] for row in selected) == Counter(train=2, val=288, test=288)
    assert len(select_entries(release, None)) == len(release["entries"])
    with pytest.raises(ValueError, match="diagnostic"):
        select_entries(release, {}, 1)
    for mapping in ({"tiny": -1}, {"tiny": True}, {"other": 0}, {"tiny": 1000}):
        with pytest.raises(ValueError):
            select_entries(release, mapping)


def test_family_round_robin_balances_evaluation_prefix():
    release = dict(entries=[entry(seed, split="test", family=family)
                            for family in ("in_distribution", "heldout_port", "heldout_noise")
                            for seed in range(4)])
    selected = select_entries(release, {}, 3, True)
    assert len({row["test_family"] for row in selected}) == 3
