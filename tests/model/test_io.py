"""Persistence boundaries: canonical bytes, version gates and stub provenance."""

import gzip
import json

import pytest

from stowage.frozen import StubTaintError
from stowage.io import (
    SchemaVersionError,
    load_instance,
    load_plan,
    load_result,
    save_instance,
    save_plan,
    save_result,
)
from stowage.model import MovePlan, PlannerResult, Provenance
from stowage.versions import SCHEMA_VERSION


def _plan(**kwargs):
    return MovePlan(id="test-plan", instance_id="test-instance", call_index=0, **kwargs)


def _result(**kwargs):
    return PlannerResult(status="ok", planner="test", planner_version="1", **kwargs)


@pytest.mark.parametrize("suffix", [".json", ".json.gz"])
@pytest.mark.parametrize("kind", ["plan", "result"])
def test_plan_result_round_trip(tmp_path, suffix, kind):
    value = _plan() if kind == "plan" else _result(plan=_plan())
    save, load = (save_plan, load_plan) if kind == "plan" else (save_result, load_result)
    first = save(value, tmp_path / "nested" / f"first{suffix}")
    second = save(load(first), tmp_path / f"second{suffix}")
    assert load(first) == value
    assert first.read_bytes() == second.read_bytes()
    data = first.read_bytes()
    if suffix.endswith(".gz"):
        data = gzip.decompress(data)
    assert json.loads(data)["schema_version"] == SCHEMA_VERSION


def test_equal_objects_have_equal_bytes(tmp_path):
    first = _result(metrics={"b": 2, "a": {"y": 1, "x": 0}})
    second = _result(metrics={"a": {"x": 0, "y": 1}, "b": 2})
    assert first == second
    assert save_result(first, tmp_path / "a.json").read_bytes() == save_result(second, tmp_path / "b.json").read_bytes()


@pytest.mark.parametrize("load", [load_instance, load_plan, load_result])
@pytest.mark.parametrize("version", ["2.0", "0.9", None, "bad"])
def test_schema_major_checked_before_model_validation(tmp_path, load, version):
    path = tmp_path / "bad.json"
    path.write_text(json.dumps({"schema_version": version}))
    with pytest.raises(SchemaVersionError, match="schema_version"):
        load(path)


def test_same_major_new_minor_loads(tmp_path):
    plan = _plan(schema_version=f"{SCHEMA_VERSION.split('.')[0]}.99")
    assert load_plan(save_plan(plan, tmp_path / "plan.json")) == plan


@pytest.mark.parametrize("location", ["result", "plan", "later_plan"])
def test_result_refuses_nested_stub_taint(tmp_path, location):
    taint = Provenance(stubs={"checker.check": "tiny-run only"})
    result = _result(
        provenance=taint if location == "result" else Provenance(),
        plan=_plan(provenance=taint if location == "plan" else Provenance()),
        later_plans=[_plan(provenance=taint)] if location == "later_plan" else [],
    )
    path = tmp_path / "result.json"
    with pytest.raises(StubTaintError, match="checker.check"):
        save_result(result, path)
    assert not path.exists()
    assert load_result(save_result(result, path, allow_stubs=True)) == result


def test_plan_refuses_stub_taint(tmp_path):
    plan = _plan(provenance=Provenance(stubs={"planner.plan": "tiny-run only"}))
    with pytest.raises(StubTaintError, match="save_plan"):
        save_plan(plan, tmp_path / "plan.json")
    assert load_plan(save_plan(plan, tmp_path / "plan.json", allow_stubs=True)) == plan


def test_result_rejects_nested_plan_schema_major(tmp_path):
    payload = _result(plan=_plan()).model_dump(mode="json")
    payload["schema_version"] = SCHEMA_VERSION
    payload["plan"]["schema_version"] = "2.0"
    path = tmp_path / "bad-result.json"
    path.write_text(json.dumps(payload))
    with pytest.raises(SchemaVersionError, match="incompatible"):
        load_result(path)


@pytest.mark.parametrize("layover", [False, True])
@pytest.mark.parametrize("suffix", [".json", ".json.gz"])
def test_instance_round_trip(tmp_path, layover, suffix):
    from stowage.model.fixtures import build_instance

    instance = build_instance(layover=layover)
    first = save_instance(instance, tmp_path / f"first{suffix}")
    loaded = load_instance(first)
    assert loaded == instance
    second = save_instance(loaded, tmp_path / f"second{suffix}")
    assert first.read_bytes() == second.read_bytes()


def test_instance_refuses_stub_taint(tmp_path):
    from stowage.model.fixtures import build_instance

    instance = build_instance().model_copy(update={"provenance": Provenance(stubs={"generator.generate": "tiny-run"})})
    with pytest.raises(StubTaintError, match="save_instance"):
        save_instance(instance, tmp_path / "instance.json")
    assert load_instance(save_instance(instance, tmp_path / "instance.json", allow_stubs=True)) == instance
