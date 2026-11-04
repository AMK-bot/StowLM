"""Release-policy regression tests; no generation or mutable live-corpus fixture."""
from copy import deepcopy
import importlib.util
import json
from pathlib import Path

import pytest


spec = importlib.util.spec_from_file_location("corpus_freeze_test_target", Path(__file__).with_name("freeze.py"))
freeze = importlib.util.module_from_spec(spec)
spec.loader.exec_module(freeze)


@pytest.fixture
def registry(tmp_path):
    pins = []
    for i in range(16):
        split, rung = ("test", "tiny") if i < 15 else ("showcase", "full")
        instance = tmp_path / split / f"{i}.json"
        instance.parent.mkdir(exist_ok=True)
        instance.write_text(f'{{"instance":{i}}}')
        audit = instance.with_suffix(".audit.json")
        audit.write_text(f'{{"audit":{i}}}')
        pins.append(dict(path=str(instance.relative_to(tmp_path)), sha256=freeze.sha256(instance),
                         audit_path=str(audit.relative_to(tmp_path)), audit_sha256=freeze.sha256(audit),
                         split=split, rung=rung, seed=i))
    release = dict(format="stowlm-mvp-releases-v1", current_release="v2", status="pending_regeneration",
                   booking_slack={"setting_name": "booking_slack", "value": 0.15}, preserved_v1=pins,
                   replacements={"test": {"seed_base": 28_000_000}, "showcase": {"seed_base": 29_000_000}})
    (tmp_path / "releases.json").write_text(json.dumps(release))
    return tmp_path, release


def test_registry_pins_both_instance_and_audit_bytes(registry):
    root, release = registry
    loaded, pins = freeze.load_release_registry(root)
    assert loaded == release and len(pins) == 16
    (root / release["preserved_v1"][0]["audit_path"]).write_text("changed")
    with pytest.raises(ValueError, match="preserved v1 checksum mismatch"):
        freeze.load_release_registry(root)


def test_registry_rejects_preserved_path_escape(registry):
    root, release = registry
    release["preserved_v1"][0]["path"] = "../outside.json"
    (root / "releases.json").write_text(json.dumps(release))
    with pytest.raises(ValueError, match="preserved v1 checksum mismatch"):
        freeze.load_release_registry(root)


def test_pending_rows_cannot_be_advertised_as_v2(registry):
    _, release = registry
    row = dict(path="test/small/old.json", split="test", rung="small", seed=8_100_000)
    freeze._release_record(row, {}, release, {})
    assert row["release_epoch"] == "v1_pending_replacement"
    complete = deepcopy(release)
    complete["status"] = "complete"
    with pytest.raises(ValueError, match="still contains old"):
        freeze._release_record(row, {}, complete, {})


@pytest.mark.parametrize("split,seed", [("test", 28_100_000), ("showcase", 29_000_100)])
def test_new_seed_requires_adopted_booking_setting(registry, split, seed):
    _, release = registry
    row = dict(path=f"{split}/small/new.json", split=split, rung="small", seed=seed)
    with pytest.raises(ValueError, match="booking slack setting mismatch"):
        freeze._release_record(row, {"booking_slack": 0.10}, release, {})
    freeze._release_record(row, {"booking_slack": 0.15}, release, {})
    assert row["release_epoch"] == "v2" and row["booking_slack"] == 0.15


def test_preserved_v1_source_exception_requires_pinned_seed(registry):
    root, _ = registry
    release, pins = freeze.load_release_registry(root)
    pin = next(iter(pins.values()))
    row = dict(path=pin["path"], split="test", rung="tiny", seed=pin["seed"])
    freeze._release_record(row, {}, release, pins)
    assert row["release_epoch"] == "v1_preserved"
    assert "immutable" in row["source_binding_exemption"]
    row["seed"] += 1
    with pytest.raises(ValueError, match="preserved seed mismatch"):
        freeze._release_record(row, {}, release, pins)


def test_old_fitting_rows_stay_inactive_during_d79_wait(registry):
    _, release = registry
    old = dict(path="train/small/old.json", split="train", rung="small", seed=10_100_000)
    freeze._release_record(old, {}, release, {})
    assert old["active_for_current_release"] is False
    tiny = dict(path="train/tiny/old.json", split="train", rung="tiny", seed=10_000_000)
    freeze._release_record(tiny, {}, release, {})
    assert tiny["active_for_current_release"] is True and tiny["booking_slack"] is None
    release["status"] = "complete"
    new = dict(path="train/small/new.json", split="train", rung="small", seed=30_100_000)
    freeze._release_record(new, {"booking_slack": 0.15}, release, {})
    assert new["active_for_current_release"] is True


def d84_release(release):
    release = deepcopy(release)
    release.update(status="partial_qualified", ship_settings={
        "feeder_500": {"booking_slack": 0.25, "booking_mass_fraction": 0.55},
        "feeder_800": {"booking_slack": 0.25, "booking_mass_fraction": 0.45}}, pending=[
            dict(split="test", ship="feeder_1700", rung="small", family="random_yard", count=3),
            dict(split="test", ship="feeder_1700", rung="small", family="simulated_yard", count=2),
            dict(split="showcase", ship="feeder_1700", rung="small", family="random_yard", count=1)])
    return release


def test_partial_qualified_registry_and_pending_rows(registry):
    root, old = registry
    release = d84_release(old)
    (root / "releases.json").write_text(json.dumps(release))
    assert freeze.load_release_registry(root)[0] == release
    pending = freeze._pending_rows(release)
    assert sum(row[-1] for row in pending if row[0] == "test") == 5
    assert sum(row[-1] for row in pending if row[0] == "showcase") == 1
    release["pending"][0]["count"] = -1
    with pytest.raises(ValueError, match="invalid pending"):
        freeze._pending_rows(release)


def test_d84_partial_test_inventory_balances_families(registry):
    _, old = registry
    release = d84_release(old)
    specs = [dict(vessel=ship, rung=rung, generator=freeze.FAMILIES[(i + si) % 2])
             for si, ship in enumerate(freeze.VESSELS) for rung in ("tiny", "small")
             if rung == "tiny" or ship in release["ship_settings"] for i in range(5)]
    freeze._test_inventory(specs, release)
    assert len(specs) == 25
    specs[-1]["rung"] = "medium"
    with pytest.raises(ValueError, match="five instances"):
        freeze._test_inventory(specs, release)


@pytest.mark.parametrize("split,seed", [("test", 28_100_000), ("showcase", 29_000_100),
                                      ("train", 30_100_000), ("val", 40_100_000)])
def test_d84_records_per_ship_mass_and_activates_streaming_training(registry, split, seed):
    _, old = registry
    release = d84_release(old)
    config = dict(vessel="feeder_800", booking_slack=0.25, booking_mass_fraction=0.45)
    row = dict(path=f"{split}/small/new.json", split=split, rung="small", seed=seed)
    freeze._release_record(row, config, release, {})
    assert row["active_for_current_release"] is True
    assert row["booking_mass_fraction"] == 0.45
    wrong = dict(config, booking_mass_fraction=0.55)
    if split in {"test", "showcase"}:
        with pytest.raises(ValueError, match="per-ship booking"):
            freeze._release_record(row, wrong, release, {})
    else:
        freeze._release_record(row, wrong, release, {})
        assert row["active_for_current_release"] is False


def test_unqualified_and_medium_training_stay_inactive(registry):
    _, old = registry
    release = d84_release(old)
    for ship, rung in (("feeder_1700", "small"), ("feeder_500", "medium")):
        row = dict(path=f"train/{rung}/new.json", split="train", rung=rung, seed=30_100_000)
        config = dict(vessel=ship, booking_slack=0.25, booking_mass_fraction=0.55)
        freeze._release_record(row, config, release, {})
        assert row["active_for_current_release"] is False


def d90_release(release):
    release = d84_release(release)
    release.update(current_release="v2.1", status="complete", pending=[])
    release["ship_settings"]["feeder_1700"] = {
        "booking_slack": 0.35, "booking_mass_fraction": 0.45}
    return release


def test_d90_registry_requires_three_qualified_ships_and_no_pending(registry):
    root, old = registry
    release = d90_release(old)
    (root / "releases.json").write_text(json.dumps(release))
    assert freeze.load_release_registry(root)[0] == release
    release["pending"] = [dict(split="test", ship="feeder_1700", rung="small",
                               family="random_yard", count=1)]
    (root / "releases.json").write_text(json.dumps(release))
    with pytest.raises(ValueError, match="no pending evaluation slots"):
        freeze.load_release_registry(root)


def test_d90_test_inventory_covers_three_ships_and_balanced_families(registry):
    _, old = registry
    release = d90_release(old)
    specs = [dict(vessel=ship, rung=rung, generator=freeze.FAMILIES[(i + si) % 2])
             for si, ship in enumerate(freeze.VESSELS) for rung in ("tiny", "small")
             for i in range(5)]
    freeze._test_inventory(specs, release)
    assert len(specs) == 30
    with pytest.raises(ValueError, match="five instances"):
        freeze._test_inventory(specs[:-1], release)


@pytest.mark.parametrize("split,seed", [("test", 28_100_010), ("showcase", 29_000_300),
                                      ("train", 30_100_010), ("val", 40_100_010)])
def test_d90_records_third_ship_setting_and_current_revision(registry, split, seed):
    _, old = registry
    release = d90_release(old)
    config = dict(vessel="feeder_1700", booking_slack=0.35, booking_mass_fraction=0.45)
    row = dict(path=f"{split}/small/new.json", split=split, rung="small", seed=seed)
    freeze._release_record(row, config, release, {})
    assert row["active_for_current_release"] is True
    assert row["booking_slack"] == 0.35 and row["booking_mass_fraction"] == 0.45
    assert row["release_epoch"] == ("v2.1_training" if split in {"train", "val"} else "v2.1")
