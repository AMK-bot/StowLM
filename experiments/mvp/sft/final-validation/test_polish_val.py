"""Focused orchestration tests; no generation, real checker or search work."""
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace as NS

import pytest

SPEC = importlib.util.spec_from_file_location("validation_polish", Path(__file__).with_name("polish_val.py"))
pv = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(pv)


def test_inventory_identifies_missing_saved_plans():
    inv = pv.inventory(["8.2", "twostep"], ["audit30", "reserve25"], 15.)
    assert [(g["n_rows"], g["n_passed"]) for g in inv["groups"]] == [(30, 26), (25, 18), (30, 29), (25, 21)]
    assert len(inv["jobs"]) == 94
    assert all(j["before_moves"] is not None for j in inv["jobs"])
    assert len(inv["missing_plans"]) == 94
    assert not any(j.get("incumbent") for j in inv["jobs"])


@pytest.mark.parametrize("path", ["experiments/mvp/corpus/train/small/x.json", "experiments/mvp/corpus/val/tiny/x.json", "/tmp/instance.json"])
def test_rejects_inputs_outside_small_validation(path):
    with pytest.raises(ValueError):
        pv.validation_path(path)


def test_only_stored_plan_payloads_are_accepted():
    assert pv.plan_payload({"generation_timings": {"tokens": [1, 2]}}) is None
    plan = {"instance_id": "map", "moves": []}
    assert pv.plan_payload(plan) == {"plan": plan}
    assert pv.plan_payload({"result": {"planner": "old", "plan": plan}})["planner"] == "old"


def test_resume_rejects_changed_source_or_incomplete_result(tmp_path):
    p = tmp_path / "row.json"
    job = {"fingerprint": "same"}
    pv.write_json(p, {"fingerprint": "same", "status": "completed"})
    assert pv.reusable(p, job)
    assert not pv.reusable(p, {"fingerprint": "changed"})
    pv.write_json(p, {"fingerprint": "same", "status": "error"})
    assert not pv.reusable(p, job)


def test_later_arm_inventory_preserves_prior_groups(tmp_path):
    assert pv.main(["--inventory-only", "--arms", "8.2", "--sets", "reserve25", "--output", str(tmp_path)]) == 0
    assert pv.main(["--inventory-only", "--arms", "8.3", "--sets", "reserve25", "--output", str(tmp_path)]) == 0
    inv = json.loads((tmp_path / "inventory.json").read_text())
    assert {(g["arm"], g["set"]) for g in inv["groups"]} == {("8.2", "reserve25"), ("8.3", "reserve25")}
    assert len([j for j in inv["jobs"] if j["arm"] == "8.2"]) == 18


@pytest.mark.parametrize("candidate_moves, candidate_passes, same_slots, same_ballast, expected", [
    (3., True, True, True, 3.),
    (7., True, True, True, 5.),
    (3., False, True, True, 5.),
    (3., True, False, True, 5.),
    (3., True, True, False, 5.),
])
def test_final_admission_never_loses_checked_incumbent(monkeypatch, candidate_moves, candidate_passes, same_slots, same_ballast, expected):
    import stowage.checker
    import stowage.io
    import stowage.model
    import stowage.moves
    import stowage.solver.gold_warmstart

    input_path = pv.validation_path((pv.ROOT / "experiments/mvp/valsplit/audit_small30.txt").read_text().split()[0])
    instance = NS(id="map")
    own_plan = NS(instance_id="map", ballast="own", moves_value=5., slot="A", passes=True)
    candidate = NS(instance_id="map", ballast="own" if same_ballast else "changed", moves_value=candidate_moves,
                   slot="A" if same_slots else "B", passes=candidate_passes)

    class Result:
        status = "ok"
        def __init__(self, plan, metrics=None):
            self.plan, self.metrics, self.message = plan, metrics or {}, "mocked"
        @staticmethod
        def model_validate(value):
            return Result(own_plan)
        def model_dump(self, **kwargs):
            return {"chosen_moves": self.plan.moves_value}

    monkeypatch.setattr(stowage.model, "PlannerResult", Result)
    monkeypatch.setattr(stowage.io, "load_instance", lambda path: instance)
    monkeypatch.setattr(stowage.checker, "check", lambda inst, plan:
        NS(passed=plan.passes, count=NS(weighted_nonproductive=plan.moves_value)))
    monkeypatch.setattr(stowage.moves, "apply_plan", lambda inst, plan:
        NS(legal=True, final_state=NS(ship=NS(placements=[NS(container_id="box", slot=plan.slot)]))))
    calls = []
    def improve(inst, own, **kwargs):
        calls.append(kwargs)
        return Result(candidate, {"baseline_check_s": .2, "acceptance_check_s": .3, "lns_elapsed_s": 1.})
    monkeypatch.setattr(stowage.solver.gold_warmstart, "improve_checked", improve)
    row = pv.polish_one(dict(arm="8.2", set="audit30", instance_id="map", input=str(input_path),
        input_sha256=pv.sha(input_path), before_moves=5., requested_polish_s=15., incumbent={"plan": {}}))
    assert row["status"] == "completed", row
    assert row["after_moves"] == expected
    assert row["result"]["chosen_moves"] == expected
    assert row["improved"] == (expected < 5.)
    assert row["assignments_preserved"] and row["ballast_preserved"] and row["final_report_passed"]
    assert row["check_s_total"] >= .5
    assert calls == [{"time_limit_s": 15., "seed": 0}]
