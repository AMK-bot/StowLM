"""Small read/write fixture checks; never invoke corpus producers."""
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
import build
import regenerate


def test_d84_rebuild_cannot_downgrade_frozen_v21(tmp_path, monkeypatch):
    import pytest
    import sys
    build.atomic_json(tmp_path/'releases.json', {'current_release':'v2.1'})
    monkeypatch.setattr(sys, 'argv', ['regenerate.py', '--root', str(tmp_path),
                                    '--booking-slack', '.25', '--execute'])
    with pytest.raises(SystemExit) as error:
        regenerate.main()
    assert error.value.code == 2
    assert sorted(p.name for p in tmp_path.iterdir()) == ['releases.json']


def fixture_root(tmp_path):
    root = tmp_path / "corpus"
    for name in ("test", "showcase"):
        (root / name).mkdir(parents=True)
        (root / name / "old.json").write_text(name)
    specs = build.specs_for("test", dict.fromkeys(build.RUNGS, 15))
    build.atomic_json(root / "test.requested.json", {"specs": specs, "counts": dict.fromkeys(build.RUNGS, 15)})
    build.atomic_json(root / "manifest.json", {"version": 1})
    build.atomic_json(root / "releases.json", {"preserved_v1": [{"path": "pinned"}]})
    return root


def test_plan_preserves_tiny_and_balance_without_mutating(tmp_path):
    root = fixture_root(tmp_path)
    original = (root / "test.requested.json").read_bytes()
    plan = regenerate.plan(root, 0.25, 10)
    before = regenerate.read(root / "test.requested.json")["specs"]
    assert plan["specs"][:15] == before[:15]
    assert sum(s.get("booking_slack") == 0.25 for s in plan["specs"]) == 10
    assert (root / "test.requested.json").read_bytes() == original
    assert min(s["seed"] for s in plan["specs"] if s["rung"] != "tiny") >= 28_000_000


def test_seed_collision_is_rejected(tmp_path):
    root = fixture_root(tmp_path)
    build.atomic_json(root / "train.requested.json", {"specs": [{"seed": 28_100_000}]})
    import pytest
    with pytest.raises(AssertionError, match="overlap"):
        regenerate.plan(root, 0.25, 10)


def test_archive_and_rerun_keep_v1(tmp_path):
    root = fixture_root(tmp_path)
    archive = regenerate.archive_v1(root)
    (root / "test/old.json").write_text("v2")
    assert regenerate.archive_v1(root) == archive
    assert (archive / "test/old.json").read_text() == "test"
    # Already-published v2 showcase selections must not block resumption.
    build.atomic_json(root / "showcase/new.audit.json", {"config": {"seed": 29_000_000}})
    regenerate.plan(root, 0.25, 10)


def test_publish_failure_restores_all_live_bytes(tmp_path, monkeypatch):
    root = fixture_root(tmp_path)
    inventory = regenerate.plan(root, 0.25, 10)
    stage = tmp_path / "stage"
    for name in ("test", "showcase"):
        (stage / name).mkdir(parents=True)
        (stage / name / "new.json").write_text("new")
    original = {str(p.relative_to(root)): p.read_bytes() for p in root.rglob("*") if p.is_file()}
    def fail(*args, **kwargs):
        raise ValueError("checker evidence rejected")
    monkeypatch.setattr(regenerate.freeze, "write_freeze", fail)
    import pytest
    with pytest.raises(ValueError, match="checker evidence"):
        regenerate.publish(root, stage, 0.25, inventory)
    restored = {str(p.relative_to(root)): p.read_bytes() for p in root.rglob("*") if p.is_file()}
    assert restored == original


def test_incomplete_backup_recovery_does_not_replace_live_files(tmp_path):
    root = fixture_root(tmp_path)
    rollback = root / ".v2-rollback/test"
    rollback.mkdir(parents=True)
    (rollback / "broken.json").write_text("partial backup")
    build.atomic_json(root / ".v2-publish.json", {"backup_complete": False, "targets": ["test"],
                                               "original_exists": {"test": True}})
    regenerate.recover_publish(root)
    assert (root / "test/old.json").read_text() == "test"
    assert not (root / "test/broken.json").exists()


def test_failed_evaluation_stage_keeps_fitting_inventory(tmp_path, monkeypatch):
    import campaign
    import pytest
    root = fixture_root(tmp_path)
    inventory = root / "train.requested.json"
    build.atomic_json(inventory, {"specs": [{"seed": 10_000_000}]})
    before = inventory.read_bytes()
    calls = []
    def prepare_spy(allocation, corpus_root, *, ship_settings=None, small_train=2700, small_val=70, migrate=False):
        calls.append(True)
    monkeypatch.setattr(campaign, "prepare", prepare_spy)
    monkeypatch.setattr(regenerate, "api_status", lambda setting: {"producer": {"booking_slack": setting}})
    def rejected(*args, **kwargs):
        raise RuntimeError("staged showcase rejected")
    monkeypatch.setattr(regenerate, "stage_evaluation", rejected)
    monkeypatch.setattr(sys, "argv", ["regenerate.py", "--root", str(root),
                                    "--booking-slack", "0.25", "--execute"])
    with pytest.raises(RuntimeError, match="staged showcase rejected"):
        regenerate.main()
    assert calls == []
    assert inventory.read_bytes() == before


def test_d84_settings_pending_and_no_medium(tmp_path):
    from collections import Counter
    root = fixture_root(tmp_path)
    inventory = regenerate.plan(root, ship_settings=regenerate.QUALIFIED)
    small = [row for row in inventory["specs"] if row["rung"] == "small"]
    assert len(inventory["specs"]) == 25
    assert Counter(row["vessel"] for row in small) == {"feeder_500": 5, "feeder_800": 5}
    assert Counter(row["generator"] for row in small) == dict.fromkeys(build.FAMILIES, 5)
    assert all(row["booking_mass_fraction"] == regenerate.QUALIFIED[row["vessel"]]["booking_mass_fraction"] for row in small)
    assert sum(row["count"] for row in inventory["pending"] if row["split"] == "test") == 5
    assert not any(row["rung"] == "medium" for row in inventory["specs"])


def test_unqualified_settings_fail_before_generation(tmp_path):
    import pytest
    root = fixture_root(tmp_path)
    with pytest.raises(AssertionError, match="D84"):
        regenerate.plan(root, ship_settings={"feeder_1700": {"booking_slack": .25, "booking_mass_fraction": .5}})
