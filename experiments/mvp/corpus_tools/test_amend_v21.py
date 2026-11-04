"""D90 planning/resumption checks; no production generation."""
from collections import Counter
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import amend_v21
import build
import regenerate


def fixture_root(tmp_path):
    root = tmp_path / "corpus"
    root.mkdir()
    original = build.specs_for("test", {"tiny": 15, "small": 15, "medium": 15})
    build.atomic_json(root / "test.requested.json", {"specs": original})
    build.atomic_json(root / "test.requested.json", regenerate.plan(root, .25))
    return root


def test_amendment_preserves_original_specs_and_balances_ship(tmp_path):
    root = fixture_root(tmp_path)
    before_bytes = (root / "test.requested.json").read_bytes()
    original = amend_v21.read(root / "test.requested.json")
    inventory, additions = amend_v21.plan(root)
    assert inventory["specs"][:25] == original["specs"]
    assert len(inventory["specs"]) == 30
    assert Counter(s["generator"] for s in additions) == {"random_yard": 3, "simulated_yard": 2}
    assert {s["seed"] for s in additions} == set(range(28_100_010, 28_100_015))
    assert all(s["booking_slack"] == .35 and s["booking_mass_fraction"] == .45 for s in additions)
    assert inventory["pending"] == [] and inventory["revision"] == "v2.1"
    assert (root / "test.requested.json").read_bytes() == before_bytes


def test_planning_is_idempotent_after_publication(tmp_path):
    root = fixture_root(tmp_path)
    inventory, additions = amend_v21.plan(root)
    build.atomic_json(root / "test.requested.json", inventory)
    assert amend_v21.plan(root) == (inventory, additions)


@pytest.mark.parametrize("seed", [28_100_012, 29_000_203])
def test_fitting_seed_collision_is_rejected(tmp_path, seed):
    root = fixture_root(tmp_path)
    build.atomic_json(root / "train.requested.json", {"specs": [{"seed": seed}]})
    with pytest.raises(AssertionError, match="overlap"):
        amend_v21.plan(root)


def test_additive_copy_never_overwrites_different_bytes(tmp_path):
    source, target = tmp_path / "new", tmp_path / "published"
    source.write_text("original")
    amend_v21.copy_addition(source, target)
    amend_v21.copy_addition(source, target)
    source.write_text("changed")
    with pytest.raises(AssertionError, match="differs"):
        amend_v21.copy_addition(source, target)
    assert target.read_text() == "original"
