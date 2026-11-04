"""Compare the same complete target cohorts across placement-noise settings."""
import importlib.util
from pathlib import Path

import pytest


@pytest.fixture(scope="module")
def experiment():
    path = Path(__file__).resolve().parents[2] / "experiments/yard-sim/run.py"
    spec = importlib.util.spec_from_file_location("yard_sensitivity_experiment", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_sensitivity_reports_all_statistics_for_matched_multiseed_population(experiment):
    # A fixed workload isolates placement effects from newly sampled demand.
    calls, params, _ = experiment.build_scenario()
    report = experiment.compare_noise_settings(calls, params, seeds=(2026, 2027, 2028),
        noise_levels=(0.0, 1.0), warmup_s=4 * 86400, until_s=14 * 86400)
    assert report["matched_call_ids"]
    assert len(report["runs"]) == 6
    assert {r["seed"] for r in report["runs"]} == {2026, 2027, 2028}
    assert report["matched_population_rule"] == "all targets present in every run"
    matched = set(report["matched_call_ids"])
    for run in report["runs"]:
        snapshots = {s["call_id"]: s for s in run["snapshot_statistics"]}
        assert matched <= snapshots.keys()
        assert all(snapshots[c]["statistics"]["target_presence_fraction"] == 1
                   for c in matched)
        assert all(snapshots[c]["targets_present"] == snapshots[c]["targets_requested"] > 0
                   for c in matched)
        assert all(s["nonempty_stack_height_histogram"] for s in snapshots.values())
        assert run["matched_snapshots"] == len(matched)
        assert 0 <= run["statistics_mean"]["target_presence_fraction"] <= 1
    # Noise has no justified monotonic direction. Require a paired effect in
    # each statistic across the deterministic multi-seed cohort instead.
    for key in ("occupancy_teu_fraction", "mean_nonempty_stack_height", "blocked_target_share"):
        by_setting = {(r["seed"], r["noise"]): r for r in report["runs"]}
        differences = [by_setting[seed, 1.0]["matched_statistics_mean"][key]
                       - by_setting[seed, 0.0]["matched_statistics_mean"][key]
                       for seed in (2026, 2027, 2028)]
        assert any(abs(delta) > 1e-9 for delta in differences), key


def test_matching_excludes_incomplete_targets_in_every_setting(experiment):
    def snapshot(call_id, presence, blocked):
        statistics = dict.fromkeys(experiment.STATISTICS, 0.0)
        statistics.update(target_presence_fraction=presence, blocked_target_share=blocked)
        return {"call_id": call_id, "statistics": statistics, "targets_requested": 4}

    rows = [{"snapshot_statistics": [snapshot("complete", 1, .25),
                                      snapshot("missing", 1, .9)]},
            {"snapshot_statistics": [snapshot("complete", 1, .5),
                                      snapshot("missing", .5, 0)]}]
    report = experiment.match_complete_populations(rows)
    assert report["matched_call_ids"] == ["complete"]
    assert [r["matched_statistics_mean"]["blocked_target_share"]
            for r in rows] == [.25, .5]
    assert all(r["excluded_call_ids"] == ["missing"] for r in rows)
    assert rows[1]["snapshot_statistics"][1]["statistics"]["target_presence_fraction"] == .5


def test_no_complete_population_is_explicit_not_zero_effect(experiment):
    stats = dict.fromkeys(experiment.STATISTICS, 0.0)
    row = {"snapshot_statistics": [{"call_id": "missing", "statistics": stats,
                                    "targets_requested": 4}]}
    report = experiment.match_complete_populations([row])
    assert report["matched_call_ids"] == []
    assert row["matched_snapshots"] == 0
    assert row["matched_statistics_mean"]["blocked_target_share"] is None
