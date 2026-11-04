"""Repair must consume the same active public mask as its candidate supplier."""
from importlib import import_module

import pytest

from stowage.actions import get_action_space
from stowage.actions.samples import tiny_instance
from stowage.actions.space import Place
from stowage.model import CheckReport, PlannerResult, ShipState, Placement
from stowage.rules.catalogue import rule_freeze_v1


@pytest.mark.parametrize("active", [False, True])
def test_repair_replays_candidate_through_active_mask(monkeypatch, active):
    instance = tiny_instance(571, n_load=1, n_other=0, p40=0)
    space = get_action_space()
    plan = space.expand(instance, [Place(instance.load_list[0], "0010182")])
    candidate = PlannerResult(status="ok", plan=plan, planner="test", planner_version="test")
    monkeypatch.setattr(import_module("stowage.solver.scalable"), "complete",
                        lambda *args, **kwargs: candidate)
    monkeypatch.setattr(import_module("stowage.planner"), "finalize",
                        lambda instance, result, **kwargs: result)
    checks = []
    monkeypatch.setattr(import_module("stowage.checker"), "check",
                        lambda *args, **kwargs: checks.append(1) or CheckReport(passed=True))

    class RejectingMask:
        def is_legal(self, action):
            return False

        def screen_diagnostics(self):
            return {"dead_end_rule_ids": ["expert.e2.weight_order"]}

        def dead_end(self):
            return None

    monkeypatch.setattr(import_module("stowage.feasibility"), "make_feasibility_engine",
                        lambda *args, **kwargs: RejectingMask())
    with rule_freeze_v1(enabled=active):
        repaired = import_module("stowage.solver.repair").repair(instance, candidate, None, time_limit_s=1)
    assert (repaired.status == "ok") is not active
    assert bool(checks) is not active
    if active:
        assert repaired.metrics["repair_rule_failures"]["expert.e2.weight_order"] > 0
        assert not repaired.metrics["repair_configured_mask_replay_passed"]


def test_shared_level_four_metrics_use_checker_definitions():
    from stowage.solver.securing_geometry import level_four_tiebreak

    instance = tiny_instance(571, n_load=1, n_other=0, p40=0)
    box = instance.containers[0].model_copy(update={"gross_mass_kg": 21000})
    instance = instance.model_copy(update={"containers": [box]})
    ship = ShipState(vessel_id=instance.vessel.id,
                     placements=[Placement(container_id=box.id, slot="0010182")])
    with rule_freeze_v1(enabled=False):
        assert level_four_tiebreak(instance, ship) == (0., 0.)
    with rule_freeze_v1():
        assert level_four_tiebreak(instance, ship) == (1., 0.)


def test_diagnostic_repair_replay_is_not_a_production_mask_claim(monkeypatch):
    from stowage.feasibility import FeasibilityConfig

    instance = tiny_instance(571, n_load=1, n_other=0, p40=0)
    plan = get_action_space().expand(instance, [Place(instance.load_list[0], "0010182")])
    candidate = PlannerResult(status="ok", plan=plan, planner="test", planner_version="test")
    config = FeasibilityConfig(extra={"global_screen": 0})
    seen = []

    def complete(instance, prefix, **kwargs):
        assert kwargs["config"].feasibility_config is config
        return candidate

    class AcceptingMask:
        def is_legal(self, action):
            return True

        def push(self, action):
            pass

        def is_complete(self):
            return True

    def factory(instance, space, **kwargs):
        seen.append(kwargs["config"])
        return AcceptingMask()

    monkeypatch.setattr(import_module("stowage.solver.scalable"), "complete", complete)
    monkeypatch.setattr(import_module("stowage.feasibility"), "make_feasibility_engine", factory)
    monkeypatch.setattr(import_module("stowage.planner"), "finalize", lambda i, r, **kw: r)
    monkeypatch.setattr(import_module("stowage.checker"), "check", lambda *a: CheckReport(passed=True))
    with rule_freeze_v1():
        result = import_module("stowage.solver.repair")._repair(instance, candidate, None,
            time_limit_s=1, feasibility_config=config)
    assert result.status == "ok" and seen == [config]
    assert result.metrics["repair_configured_mask_replay_passed"]
    assert not result.metrics["repair_mask_replay_passed"]
    assert result.metrics["repair_feasibility_config"]["extra"] == {"global_screen": 0}
