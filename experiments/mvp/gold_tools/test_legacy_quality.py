"""Pure D97 decision tests: no solver, checker, or expensive fixture setup."""
import importlib.util
import json
from pathlib import Path

import pytest

_spec = importlib.util.spec_from_file_location("gold_legacy_quality", Path(__file__).with_name("legacy_quality.py"))
module = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(module)


def record(legacy=10, baseline=10, **changes):
    kwargs = dict(legacy_checker_passed=True, legacy_mask_passed=True,
                  baseline_checker_passed=True, baseline_mask_passed=True,
                  input_sha256="a"*64, legacy_input_sha256="a"*64,
                  baseline_version="4.4.0", legacy_plan_sha256="b"*64, baseline_plan_sha256="c"*64)
    kwargs.update(changes)
    return module.comparison_record(legacy, baseline, **kwargs)


@pytest.mark.parametrize("legacy,baseline,retain", [(9, 10, True), (10, 10, True), (11, 10, False), (10.00000001, 10, False)])
def test_measured_no_worse_rule(legacy, baseline, retain):
    row = record(legacy, baseline)
    assert row["retain_legacy"] is retain
    assert row["objective_delta"] == legacy-baseline
    if not retain:
        assert row["drop_reason"] == "legacy_quality_worse_than_twostep"


@pytest.mark.parametrize("gate,reason", [
    ("legacy_checker_passed", "legacy_quality_checker"),
    ("legacy_mask_passed", "legacy_quality_mask"),
    ("baseline_checker_passed", "legacy_quality_baseline_unverified"),
    ("baseline_mask_passed", "legacy_quality_baseline_unverified"),
])
def test_fresh_gates_required(gate, reason):
    row = record(**{gate: False})
    assert not row["retain_legacy"] and row["drop_reason"] == reason


@pytest.mark.parametrize("value", [None, float("nan"), float("inf"), -1, True, "10"])
def test_unmeasured_objectives_are_never_retained(value):
    assert record(legacy=value)["drop_reason"] == "legacy_quality_objective_unavailable"
    assert record(baseline=value)["drop_reason"] == "legacy_quality_baseline_unverified"
    json.dumps(record(legacy=value, baseline=value), allow_nan=False)


def test_same_instance_bytes_mandatory():
    assert record(legacy_input_sha256="d"*64)["drop_reason"] == "legacy_quality_input_mismatch"
    assert record(input_sha256=None, legacy_input_sha256=None)["drop_reason"] == "legacy_quality_input_mismatch"


def test_record_preserves_artifact_and_version_lineage():
    row = record()
    assert row["baseline_version"] == "4.4.0"
    assert row["legacy_plan_sha256"] == "b"*64
    assert row["baseline_plan_sha256"] == "c"*64
    assert row["objective_definition"] == "weighted_nonproductive"
