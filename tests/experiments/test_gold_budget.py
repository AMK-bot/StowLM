"""Budget sizing: denominators, failure costs, coverage bounds and token epochs."""
from __future__ import annotations

import importlib.util
from functools import partial
from pathlib import Path
import sys

import pytest


@pytest.fixture(scope="module")
def budget():
    path = Path(__file__).resolve().parents[2] / "experiments/gold-plans/campaign/plan_budget.py"
    spec = importlib.util.spec_from_file_location("gold_budget_test", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    # Legacy arithmetic tests explicitly exercise non-production sizing.
    module.plan_budget = partial(module.plan_budget, pipeline_test=True)
    return module


def payload(*rows):
    return {"label": "synthetic test inputs, not evidence", "measurements": list(rows)}


def row(rung="tiny", *, attempted=10, published=5, seconds=100, tokens=100, available=100):
    return dict(rung=rung, attempted=attempted, published=published,
                total_attempt_seconds=seconds, prompt_tokens=tokens * .6,
                output_tokens=tokens * .4, available_instances=available)


def test_failed_attempts_are_charged_and_parallel_time_reported(budget):
    result = budget.plan_budget(payload(row()), target_tokens=1000, cores=4)
    chosen = result["rows"][0]
    assert chosen["gold_count"] == 10
    assert chosen["expected_attempts"] == 20
    assert result["serial_core_seconds"] == 200
    assert result["ideal_parallel_wall_seconds"] == 50
    assert result["calibration_core_seconds"] == 100
    assert result["additional_core_seconds"] == 100
    assert result["ideal_additional_wall_seconds"] == 25


def test_zero_success_never_invents_gold_or_divides_by_zero(budget):
    result = budget.plan_budget(payload(row(published=0, tokens=0)), target_tokens=1000)
    assert result["status"] == "insufficient_capacity"
    assert result["processed_tokens"] == 0
    assert result["shortfall_tokens"] == 1000
    assert result["serial_core_seconds"] == 100
    assert result["additional_core_seconds"] == 0
    assert result["rows"][0]["measured_attempted"] == 10


def test_mix_uses_efficiency_after_required_coverage(budget):
    result = budget.plan_budget(
        payload(row(), row("small", tokens=200, seconds=1000)),
        target_tokens=1000, min_counts={"small": 2})
    assert {r["rung"]: r["gold_count"] for r in result["rows"]} == {"tiny": 6, "small": 2}


def test_capacity_uses_unique_instance_denominator_and_full_cap(budget):
    result = budget.plan_budget(payload(row("full", available=3500)), target_tokens=1_000_000)
    assert result["rows"][0]["gold_count"] == 300
    assert result["status"] == "insufficient_capacity"
    assert result["rows"][0]["expected_attempts"] == 600
    capped = budget.plan_budget(payload(row(available=11, published=3)), target_tokens=10_000)
    assert capped["rows"][0]["gold_capacity"] == 3
    assert capped["rows"][0]["expected_attempts"] == 10


def test_epochs_repeat_tokens_without_repeating_solver_cost(budget):
    result = budget.plan_budget(payload(row()), target_tokens=1000, epochs=2)
    assert result["rows"][0]["gold_count"] == 5
    assert result["serial_core_seconds"] == 100
    assert result["processed_tokens"] == 1000


def test_mean_attempt_cannot_be_split_over_more_cores(budget):
    result = budget.plan_budget(payload(row(published=10)), target_tokens=100, cores=100)
    assert result["ideal_parallel_wall_seconds"] == 10


def test_existing_gold_needs_no_additional_solving(budget):
    result = budget.plan_budget(payload(row()), target_tokens=200)
    assert result["rows"][0]["gold_count"] == 2
    assert result["rows"][0]["additional_attempts"] == 0
    assert result["serial_core_seconds"] == 100
    assert result["ideal_additional_wall_seconds"] == 0


@pytest.mark.parametrize("change", [
    {"attempted": 0}, {"published": 11}, {"total_attempt_seconds": 0},
    {"total_attempt_seconds": float("nan")}, {"published": -1},
    {"attempted": 10.5}, {"available_instances": 9}, {"prompt_tokens": -1},
])
def test_bad_measurements_refused(budget, change):
    sample = row()
    sample.update(change)
    with pytest.raises(ValueError):
        budget.plan_budget(payload(sample))


def test_impossible_or_unmeasured_coverage_refused(budget):
    with pytest.raises(ValueError, match="capacity"):
        budget.plan_budget(payload(row(published=0)), min_counts={"tiny": 1})
    with pytest.raises(ValueError, match="unmeasured"):
        budget.plan_budget(payload(row()), min_counts={"small": 1})
    with pytest.raises(ValueError, match="distinct"):
        budget.plan_budget(payload(row(), row()))


def test_example_labels_all_inputs_and_cli_reports_shortfall(budget, capsys):
    assert "SYNTHETIC" in budget.example_inputs()["label"]
    assert budget.main(["--example"]) == 2
    output = capsys.readouterr().out
    assert "NOT EVIDENCE" in output
    assert "insufficient_capacity" in output
    assert "core-h" in output
    assert "SFT" in output


def test_target_can_be_reached_with_bounded_overshoot(budget):
    result = budget.plan_budget(payload(row()), target_tokens=101)
    assert result["status"] == "ready"
    assert 101 <= result["processed_tokens"] < 201


def test_production_mix_is_not_selected_by_solver_throughput(budget):
    inputs = payload(*(row(rung, published=10, seconds=1 if rung == 'tiny' else 10000,
        tokens=100, available=10000) for rung in budget.RUNGS))
    result = budget.plan_budget(inputs, target_tokens=1_000_000, pipeline_test=False)
    selected = {item['rung']: item for item in result['rows']}
    assert selected['tiny']['processed_tokens'] <= .10 * result['processed_tokens']
    assert selected['small']['processed_tokens'] >= .30 * 1_000_000
    assert selected['medium']['processed_tokens'] >= .35 * 1_000_000
    assert selected['full']['gold_count'] >= 300


def test_production_cannot_replace_unknown_full_cost_with_tiny(budget):
    result = budget.plan_budget(payload(row(available=100000)), target_tokens=10000, pipeline_test=False)
    assert result['status'] == 'insufficient_capacity'
    assert result['data_mode'] == 'production'
    assert 'full' in result['unmeasured_rungs']
