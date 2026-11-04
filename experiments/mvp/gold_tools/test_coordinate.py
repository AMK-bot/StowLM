import importlib.util
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).parent))
spec = importlib.util.spec_from_file_location("coordinate", Path(__file__).with_name("coordinate.py"))
coordinate = importlib.util.module_from_spec(spec)
spec.loader.exec_module(coordinate)


def test_waiting_release_keeps_capacity_for_tiny():
    assert coordinate.allocate(5, {"tiny": 100, "small": 0}, {"tiny": 50, "small": 200}, False) == (5, 0)


def test_split_retains_two_pilot_workers_and_gives_small_majority():
    assert coordinate.allocate(5, {"tiny": 100, "small": 0}, {"tiny": 50, "small": 200}, True) == (2, 3)


def test_after_pilot_remaining_effort_favors_small():
    tiny, small = coordinate.allocate(8, {"tiny": 300, "small": 100}, {"tiny": 50, "small": 200}, True)
    assert tiny + small == 8
    assert tiny == 2
    assert small == 6


def test_last_core_avoids_two_simultaneous_one_core_jobs():
    assert coordinate.allocate(1, {"tiny": 100, "small": 0}, {"tiny": 50, "small": 200}, True) == (0, 1)
    assert coordinate.allocate(0, {"tiny": 100, "small": 0}, {"tiny": 50, "small": 200}, True) == (0, 0)


def test_exhausted_inventory_releases_cores_and_validation_still_runs():
    assert coordinate.allocate(5, {"tiny": 850, "small": 2000}, {"tiny": 50, "small": 200}, True,
                               {"tiny": False, "small": True}) == (0, 5)


def test_remaining_inventory_tracks_attempts_and_validation(tmp_path):
    import json
    corpus, output = tmp_path / "corpus", tmp_path / "gold"
    corpus.mkdir()
    spec = dict(split="train", rung="tiny", vessel="feeder_500", generator="random_yard", seed=1)
    (corpus / "train.requested.json").write_text(json.dumps({"specs": [spec]}))
    (corpus / "val.requested.json").write_text(json.dumps({"specs": [dict(spec, split="val", seed=2)]}))
    assert coordinate.remaining_inventory_work(corpus, output, "tiny", {}, 50)
    path = output / "train/tiny/feeder_500/random_yard/seed-000000001/result.json"
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps(dict(completed=True, passed=False)))
    assert coordinate.remaining_inventory_work(corpus, output, "tiny", {}, 50)
    path = output / "val/tiny/feeder_500/random_yard/seed-000000002/result.json"
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps(dict(completed=True, passed=True)))
    assert not coordinate.remaining_inventory_work(corpus, output, "tiny", {}, 50)


def test_capacity_excludes_both_own_reservations(tmp_path):
    ledger = tmp_path / "ledger.md"
    ledger.write_text("| executive/job | task | cores | RAM GB | GPU | est. wall | start |\n"
                      "| other | 4.7 | 11 | 22 | no | 1h | now |\n"
                      "| mvp-gold/tiny | 6.1 | 4 | 8 | no | 1h | now |\n"
                      "| mvp-gold/small | 6.1 | 1 | 2 | no | 1h | now |\n")
    assert coordinate.gold_capacity(ledger) == (5, 11, [])


def test_small_critical_chain_ceilings_and_tiny_stop_at_800():
    assert coordinate.allocate(12, {"tiny": 799, "small": 15}, {}, True) == (2, 10)
    assert coordinate.allocate(12, {"tiny": 800, "small": 15}, {}, True) == (0, 10)


def test_policy_live_first_and_small_val_cap(tmp_path):
    import json
    path = tmp_path / "policy.json"
    coordinate.update_policy(path, 10, small=True)
    policy = json.loads(path.read_text())
    assert policy["admission_mode"] == "live_load_first"
    assert policy["max_workers"] == 10
    assert policy["max_load"] == 16
    assert policy["val_gold_caps"] == {"tiny": 50, "small": 56}


def test_deadline_projection_uses_actual_workers_and_measured_cost():
    from datetime import datetime, timezone
    row = coordinate.deadline_projection({"small": 20}, {"small": 200}, {"small": 6},
                                        datetime(2026, 10, 29, 20, tzinfo=timezone.utc))
    assert row["hours_remaining"] == 10
    assert row["measured_small_gold_per_worker_hour"] == 18
    assert row["projected_small_train_at_current_workers"] == 1100
    assert row["projected_small_train_at_ten_workers"] == 1820


def test_d96_pauses_small_and_allows_ten_tiny_regardless_old_ceiling():
    assert coordinate.priority_ceilings(0, {"tiny": 130, "small": 27}, {}, True,
                                       {"tiny": True, "small": True}, True) == (10, 0)
    assert coordinate.priority_ceilings(12, {"tiny": 800, "small": 27}, {}, True,
                                       {"tiny": False, "small": True}, True) == (0, 0)


def test_measured_cpu_policy_mode_is_explicit(tmp_path):
    import json
    path = tmp_path / "policy.json"
    coordinate.update_policy(path, 8, admission_mode="measured_cpu")
    policy = json.loads(path.read_text())
    assert policy["admission_mode"] == "measured_cpu"
    assert policy["max_workers"] == 8


def test_small_gets_ten_alongside_tiny_until_tiny_target():
    assert coordinate.d97_ceilings({"tiny": True, "small": True}, True) == (2, 10)
    assert coordinate.d97_ceilings({"tiny": False, "small": True}, True) == (0, 12)
    assert coordinate.d97_ceilings({"tiny": True, "small": True}, False) == (2, 0)


def test_d100_target_transfers_slots_even_with_tiny_inventory_remaining():
    work = {"tiny": True, "small": True}
    assert coordinate.d97_ceilings(work, True, {"tiny": 799}) == (2, 10)
    assert coordinate.d97_ceilings(work, True, {"tiny": 800}) == (0, 12)
    assert coordinate.d97_ceilings(work, True, {"tiny": 801}) == (0, 12)
    assert coordinate.d97_ceilings(work, False, {"tiny": 800}) == (0, 0)


def test_d100_target_counts_only_completed_passed_training_plans(tmp_path):
    import json
    output = tmp_path / "gold"
    for split, case, completed, passed in (("train", "good", True, True),
                                           ("train", "failed", True, False),
                                           ("train", "partial", False, True),
                                           ("val", "validation", True, True)):
        path = output / split / "tiny" / case / "result.json"
        path.parent.mkdir(parents=True)
        path.write_text(json.dumps(dict(rung="tiny", completed=completed,
                                        passed=passed, wall_s=1)))
    counts, _, _ = coordinate.training_stats(output)
    assert counts["tiny"] == 1


def test_d100_diagnostic_reserves_one_shared_fallback_slot():
    assert coordinate.reserve_probe_slot(2, 10, True) == (2, 5)
    assert coordinate.reserve_probe_slot(0, 12, True) == (0, 7)
    assert coordinate.reserve_probe_slot(2, 0, True) == (2, 0)
    assert coordinate.reserve_probe_slot(2, 10, False) == (2, 10)
    assert coordinate.reserve_probe_slot(0, 12, False) == (0, 12)


def test_coordinator_preserves_live_solver_budgets(tmp_path):
    import json
    path = tmp_path / "small-policy.json"
    path.write_text(json.dumps(dict(small_ballast_s=240,
                                    max_workers=6, train_first=False)))
    coordinate.update_policy(path, 5, small=True, admission_mode="measured_cpu")
    row = json.loads(path.read_text())
    assert row["small_ballast_s"] == 240
    assert row["max_workers"] == 5
    assert row["train_first"] is True
    assert row["val_gold_caps"] == {"tiny": 50, "small": 56}
