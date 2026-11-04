"""Fast migration contracts; no producers, solver, model or cloud calls."""
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
import build
import campaign
import tiny_batch


def test_tiny_restart_has_disjoint_complete_partitions(tmp_path):
    for split in ("train", "val"):
        specs = build.specs_for(split, {"tiny": 12, "small": 6, "medium": 6})
        build.atomic_json(tmp_path / f"{split}.requested.json", {"specs": specs})
    shards = [tiny_batch.selection(tmp_path, i, 3) for i in range(3)]
    seeds = [[s["seed"] for s in shard] for shard in shards]
    assert all(s["rung"] == "tiny" for shard in shards for s in shard)
    assert len([seed for shard in seeds for seed in shard]) == 24
    assert len(set(seed for shard in seeds for seed in shard)) == 24


def test_slack_migration_archives_only_superseded_rungs(tmp_path):
    root = tmp_path / "corpus"
    allocation = tmp_path / "allocation.json"
    counts = {"tiny": 6, "small": 6, "medium": 6}
    build.atomic_json(allocation, {"allocation": {"train_counts_by_rung": counts, "val_counts_by_rung": counts}})
    original = campaign.prepare(allocation, root)
    tiny = root / "train/tiny/sentinel"
    tiny.parent.mkdir(parents=True)
    tiny.write_bytes(b"immutable tiny bytes")
    old = root / "train/small/old-instance.json"
    old.parent.mkdir(parents=True)
    old.write_bytes(b"old small bytes")
    updated = campaign.prepare(allocation, root, booking_slack=0.25, migrate=True)
    assert tiny.read_bytes() == b"immutable tiny bytes"
    assert not old.exists()
    assert (root / "archive/v1/train/small/old-instance.json").read_bytes() == b"old small bytes"
    for split in ("train", "val"):
        assert [s for s in updated[split] if s["rung"] == "tiny"] == [s for s in original[split] if s["rung"] == "tiny"]
        other = [s for s in updated[split] if s["rung"] != "tiny"]
        assert all(s["booking_slack"] == 0.25 for s in other)
        assert all(s["seed"] >= {"train": 30_000_000, "val": 40_000_000}[split] for s in other)
    assert campaign.prepare(allocation, root, booking_slack=0.25, migrate=True) == updated


def test_slack_helper_never_silently_drops_requested_control():
    import pytest
    def producer_without_slack(size):
        return size
    def producer_with_slack(size, *, booking_slack=None):
        return booking_slack
    assert build.booking_slack_kwargs(producer_without_slack, None) == {}
    with pytest.raises(ValueError, match="API not available"):
        build.booking_slack_kwargs(producer_without_slack, 0.25)
    assert build.booking_slack_kwargs(producer_with_slack, 0.25) == {"booking_slack": 0.25}
