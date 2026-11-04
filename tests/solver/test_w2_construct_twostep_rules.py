"""Activation-gated constructor/two-step rule-freeze regressions."""
from time import monotonic

import pytest
from ortools.linear_solver import pywraplp

from stowage.actions.expander import Expander
from stowage.model.fixtures import build_instance
from stowage.rules.catalogue import rule_freeze_v1
from stowage.solver import twostep
from stowage.solver.construct import _build_assignment_model
from stowage.solver.construct_sequence import _ready, sequence
from stowage.solver.twostep_terminal import _precedences


def plain():
    instance = build_instance()
    template = instance.containers[2]
    boxes = [template.model_copy(update={"id": b.id, "special_stowage": [], "oog": None,
             "dg": None, "reefer": False, "pod": "THSGZ", "gross_mass_kg": 12000+i})
             for i, b in enumerate(instance.containers[1:3])]
    return instance.model_copy(update={"containers": boxes, "load_list": [b.id for b in boxes],
        "vessel": instance.vessel.model_copy(update={"hull": None, "bridge": None})})


@pytest.mark.parametrize("enabled", [False, True])
def test_terminal_categories_keep_d25_interchangeability_in_both_settings(enabled):
    instance = plain()
    with rule_freeze_v1(enabled=enabled):
        same = twostep.category_of(instance.containers[0], instance) == twostep.category_of(instance.containers[1], instance)
    assert same


@pytest.mark.parametrize("enabled", [False, True])
def test_constructor_same_class_kg_inversion_is_hard_only_after_activation(enabled):
    instance = plain()
    with rule_freeze_v1(enabled=enabled):
        model = _build_assignment_model(instance, physics=False)
        for cid, slot in zip(instance.load_list, ["0010102", "0010104"]):
            model.solver.Add(model.x[cid, slot] == 1)
        model.solver.SetTimeLimit(1000)
        status = model.solver.Solve()
    assert (status == pywraplp.Solver.INFEASIBLE) is enabled


def test_constructor_mask_cannot_be_disabled_after_activation(monkeypatch):
    import stowage.solver.construct_sequence as module
    instance = plain()
    class MaskCalled(Exception):
        pass
    def factory(*args):
        raise MaskCalled
    monkeypatch.setattr(module, "make_feasibility_engine", factory)
    with rule_freeze_v1(), pytest.raises(MaskCalled):
        sequence(instance, dict(zip(instance.load_list, ["0010102", "0030102"])),
                 monotonic()+1, replay_mask=False)


@pytest.mark.parametrize("side,far,near", [("port", "0010182", "0010282"),
                                           ("starboard", "0010282", "0010182")])
def test_far_rows_precede_yard_accessibility_and_terminal_rank(side, far, near):
    instance = plain()
    instance = instance.model_copy(update={"port": instance.port.model_copy(update={"berthing_side": side})})
    a,b = instance.load_list
    slots = [twostep.SlotCategory(near, (), a), twostep.SlotCategory(far, (), b)]
    with rule_freeze_v1():
        ready, _ = _ready(instance, Expander(instance), {a: near, b: far})
        assert ready[0].slot == far
        assert (1, 0) in _precedences(instance, slots)


def test_active_category_ignores_legacy_class_threshold_overrides():
    instance = plain()
    instance = instance.model_copy(update={"rule_overrides": {
        "expert.e2.weight_order": {"medium_min_kg": 30000, "heavy_min_kg": 1000}}})
    with rule_freeze_v1():
        assert twostep.category_of(instance.containers[0], instance)[3] == "M"


def test_active_kg_tolerance_can_cross_legacy_class_boundary():
    instance = plain()
    a,b = instance.containers
    instance = instance.model_copy(update={"containers": [
        a.model_copy(update={"gross_mass_kg": 19999}),
        b.model_copy(update={"gross_mass_kg": 20000})],
        "rule_overrides": {"expert.e2.weight_order": {"weight_order_tolerance_kg": 1}}})
    with rule_freeze_v1():
        model = _build_assignment_model(instance, physics=False)
        for cid, slot in zip(instance.load_list, ["0010102", "0010104"]):
            model.solver.Add(model.x[cid, slot] == 1)
        model.solver.SetTimeLimit(1000)
        assert model.solver.Solve() in (pywraplp.Solver.OPTIMAL, pywraplp.Solver.FEASIBLE)


def test_active_prestow_backtracks_to_deck_even_with_compatible_hold(monkeypatch):
    from stowage.actions.space import Place
    instance = plain()
    box = instance.containers[0].model_copy(update={"gross_mass_kg": 25000})
    instance = instance.model_copy(update={"containers": [box], "load_list": [box.id]})

    class HoldDeadEndOracle:
        """Isolate the policy: only the legal hold prefix has no completion."""
        def __init__(self, instance, space):
            self.action = None
        def legal_actions(self):
            return [] if self.action else [Place(box.id, "0010102"), Place(box.id, "0010182")]
        def is_legal(self, action):
            return action in self.legal_actions()
        def is_complete(self):
            return self.action is not None and self.action.slot == "0010182"
        def dead_end(self):
            return None
        def copy(self):
            result = HoldDeadEndOracle(None, None)
            result.action = self.action
            return result
        def push(self, action):
            self.action = action

    monkeypatch.setattr(twostep, "make_feasibility_engine", HoldDeadEndOracle)
    with rule_freeze_v1():
        prestow = twostep.preplan(instance, time_limit_s=1)
    assert prestow.status == "ok"
    assert prestow.slots[0].slot == "0010182"


@pytest.mark.parametrize("hatch_weight,expected", [(1.0, "0010182"), (0.0, "0010202")])
def test_active_carrier_moves_precede_heavy_deck_tiebreak(monkeypatch, hatch_weight, expected):
    """Opening/closing a hold must not buy a soft heavy-under-deck win."""
    from stowage.actions.space import Place
    instance = plain()
    box = instance.containers[0].model_copy(update={"gross_mass_kg": 25000})
    instance = instance.model_copy(update={"containers": [box], "load_list": [box.id],
        "objective": instance.objective.model_copy(update={
            "hatch_cover": hatch_weight, "future_hatch_cover": hatch_weight})})

    class ChoiceOracle:
        """Both cargo placements are available; physical expansion is real."""
        def __init__(self, instance, space):
            self.action = None
        def legal_actions(self):
            return [] if self.action else [Place(box.id, "0010202"), Place(box.id, "0010182")]
        def is_legal(self, action):
            return action in self.legal_actions()
        def is_complete(self):
            return self.action is not None
        def dead_end(self):
            return None
        def copy(self):
            result = ChoiceOracle(None, None)
            result.action = self.action
            return result
        def push(self, action):
            self.action = action

    monkeypatch.setattr(twostep, "make_feasibility_engine", ChoiceOracle)
    with rule_freeze_v1():
        prestow = twostep.preplan(instance, time_limit_s=2)
    assert prestow.status == "ok"
    assert prestow.slots[0].slot == expected
