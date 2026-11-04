"""Pilot evidence bookkeeping must not hide failed generation or ballast."""
from __future__ import annotations

import importlib.util
from pathlib import Path


def pilot():
    path = Path(__file__).resolve().parents[2] / "experiments/scalable-solver/fix_epoch_pilot.py"
    spec = importlib.util.spec_from_file_location("scalable_epoch_test", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_generation_only_failures_remain_in_per_rung_evidence():
    result = pilot().summarize([dict(spec=dict(profile="medium"),
        generation=dict(status="rejected"), variants={})])
    assert result["medium/default"]["generation_failed"] == 1
    assert result["medium/default"]["attempted"] == 0
    assert result["medium/default"]["approved_seconds_per_plan"] is None


def test_not_found_and_incomplete_mask_do_not_count_as_approved():
    result = pilot().summarize([dict(spec=dict(profile="small"), generation=dict(status="accepted"),
        variants=dict(default=dict(status="checker_failed", completed=True, checker_passed=False,
            approved=False, ballast_status="not_found", timings_s=dict(pipeline=12),
            replay=dict(status="failed", reason="incomplete_departure"))))])
    row = result["small/default"]
    assert row["ballast_status_counts"] == {"not_found": 1}
    assert row["mask_pass"] == row["approved"] == row["full_checker_pass"] == 0
    assert row["complete"] == 1 and row["mean_seconds_per_attempt"] == 12
    assert row["approved_objectives"] == row["approved_gaps_to_lower_bound"] == []
