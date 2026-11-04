"""Scoped-oracle equivalence and the measured greedy terminal policy.

Hull-free fixtures certify local rules and physical replay only. The benchmark
runner supplies ballast/full-checker certification on generated physical ships.
"""
from time import monotonic

import pytest

from stowage.actions import get_action_space
from stowage.actions.samples import tiny_instance
from stowage.actions.space import Place
from stowage.feasibility import FeasibilityConfig, make_feasibility_engine
from stowage.moves import apply_plan
from stowage.rules.catalogue import rule_freeze_v1
from stowage.solver import twostep, twostep_adapter, twostep_terminal
from tests.solver.test_twostep import PhysicalOnlyOracle, interchangeable_instance


@pytest.fixture(autouse=True)
def demo_rules():
    with rule_freeze_v1(enabled=False):
        yield


def _local_instance():
    instance = tiny_instance(141, n_load=6, n_other=0, p40=0)
    return instance.model_copy(update={
        "vessel": instance.vessel.model_copy(update={"hull": None, "bridge": None})})


def test_scoped_queries_equal_full_masks_through_push_copy_and_pop():
    instance = _local_instance()
    space = get_action_space("boxslot")
    config = FeasibilityConfig(extra={"global_screen": 0})
    full = make_feasibility_engine(instance, space, config=config)
    scoped = twostep_adapter.make_planning_engine(instance, space, config=config)
    for _ in range(3):
        legal = full.legal_actions()
        for cid in instance.load_list:
            expected = tuple(a.slot for a in legal if isinstance(a, Place) and a.box == cid)
            assert scoped.legal_slots_for_box(cid) == expected
        for cid in instance.load_list:
            for slot in scoped.index_slots:
                action = Place(cid, slot)
                assert scoped.is_legal(action) == full.is_legal(action)
        places = [a for a in legal if isinstance(a, Place)]
        if not places:
            break
        action = places[0]
        child = scoped.copy()
        child.push(action)
        assert scoped.depth + 1 == child.depth
        assert child.pop() == action
        assert child.legal_slots_for_box(action.box) == scoped.legal_slots_for_box(action.box)
        scoped.push(action)
        full.push(action)
    assert scoped.is_legal(Place("missing", "0010182")) is False
    assert scoped.is_legal(Place(instance.load_list[0], "9999998")) is False
    # A partial-domain rejection must never poison the current full-domain mask.
    assert scoped.legal_actions() == full.legal_actions()


def test_scoped_discharge_equals_full_domain_without_mutating_state():
    from tests.solver.test_twostep_contracts import _no_load_instance
    instance = _no_load_instance(discharge=True, equipment=True)
    instance = instance.model_copy(update={
        "vessel": instance.vessel.model_copy(update={"hull": None})})
    config = FeasibilityConfig(extra={"global_screen": 0})
    full = make_feasibility_engine(instance, get_action_space("boxslot"), config=config)
    scoped = twostep_adapter.make_planning_engine(instance, config=config)
    assert scoped.legal_discharges() == full.legal_discharges()
    assert scoped.legal_actions() == full.legal_actions()
    for action in full.legal_discharges():
        assert scoped.is_legal(action)
        scoped.push(action)
        full.push(action)
    assert scoped.is_complete() == full.is_complete()


def test_scoped_membership_and_push_never_enumerate_full_mask(monkeypatch):
    instance = _local_instance()
    scoped = twostep_adapter.make_planning_engine(instance,
        config=FeasibilityConfig(extra={"global_screen": 0}))
    def forbidden():
        raise AssertionError("scoped query enumerated full mask")
    monkeypatch.setattr(scoped._native, "_ensure", forbidden)
    for cid in instance.load_list:
        slots = scoped.legal_slots_for_box(cid)
        if slots:
            action = Place(cid, slots[0])
            assert scoped.is_legal(action)
            scoped.push(action)
            assert scoped.depth == 1
            assert scoped.pop() == action
            return
    pytest.fail("fixture has no legal placement")


def _small_prestow():
    instance = interchangeable_instance().model_copy(update={"size_class": "small"})
    boxes = {box.id: box for box in instance.containers}
    prestow = twostep.PreStowPlan(slots=tuple(
        twostep.SlotCategory(slot, twostep.category_of(boxes[cid], instance), cid)
        for cid, slot in zip(instance.load_list, ("0010182", "0010282"))))
    return instance, prestow


def test_fast_terminal_substitutes_same_booked_category_and_measures_moves():
    instance, prestow = _small_prestow()
    plan, metrics, status, message = twostep_terminal.terminal_plan(
        instance, prestow, deadline=monotonic()+3, seed=0, engine_factory=PhysicalOnlyOracle)
    assert status == "ok", message
    actions = get_action_space("boxslot").from_plan(instance, plan)
    assert actions[0].box == instance.load_list[-1]
    assert metrics["terminal_cp_solves"] == 0
    assert metrics["terminal_fast_attempts"] == 2
    assert metrics["terminal_category_substitutions"] == 2
    expected_categories = {slot.slot: slot.category for slot in prestow.slots}
    boxes = {box.id: box for box in instance.containers}
    assert all(twostep.category_of(boxes[a.box], instance) == expected_categories[a.slot] for a in actions)
    replay = apply_plan(instance, plan)
    assert replay.legal and replay.count.relocations == 0
    assert metrics["objective"] == replay.count.weighted_nonproductive


def test_failed_greedy_substitution_is_recorded_before_representative_replay():
    instance, prestow = _small_prestow()
    class TrapOracle(PhysicalOnlyOracle):
        def is_legal(self, action):
            if self.history and self.history[0].box == instance.load_list[-1]:
                return False
            return super().is_legal(action)
    plan, metrics, status, message = twostep_terminal.terminal_plan(
        instance, prestow, deadline=monotonic()+3, seed=0, engine_factory=TrapOracle)
    assert status == "ok", message
    assert apply_plan(instance, plan).legal
    assert metrics["terminal_fast_failures"] == 1
    assert metrics["terminal_fast_selected_policy"] == "carrier_representatives"
    assert metrics["terminal_category_substitutions"] == 0
    assert metrics["terminal_candidates"] == 1


def test_fast_terminal_expired_budget_constructs_no_engine():
    instance, prestow = _small_prestow()
    def forbidden(*args, **kwargs):
        raise AssertionError("expired budget constructed an engine")
    plan, metrics, status, _ = twostep_terminal.terminal_plan(
        instance, prestow, deadline=monotonic()-1, seed=0, engine_factory=forbidden)
    assert plan is None and status == "timeout"
    assert metrics["terminal_fast_attempts"] == metrics["terminal_cp_solves"] == 0


def test_fast_terminal_orders_audit_slots_by_support_dependency():
    instance, prestow = _small_prestow()
    # Audit records deliberately list the upper tier first and omit a trace.
    prestow = twostep.PreStowPlan(slots=(
        twostep.SlotCategory("0010184", prestow.slots[0].category, instance.load_list[0]),
        twostep.SlotCategory("0010182", prestow.slots[1].category, instance.load_list[1])))
    plan, metrics, status, message = twostep_terminal.terminal_plan(
        instance, prestow, deadline=monotonic()+3, seed=0, engine_factory=PhysicalOnlyOracle)
    assert status == "ok", message
    actions = get_action_space("boxslot").from_plan(instance, plan)
    assert [a.slot for a in actions] == ["0010182", "0010184"]
    assert apply_plan(instance, plan).legal
    assert metrics["terminal_cp_solves"] == 0


def test_fast_terminal_retains_d73_mask_approved_bounded_row_adjustment():
    instance, prestow = _small_prestow()
    cid = instance.load_list[-1]
    box = next(b for b in instance.containers if b.id == cid)
    instance = instance.model_copy(update={"containers": [box], "load_list": [cid],
        "yard": instance.yard.model_copy(update={"stacks": [
            instance.yard.stacks[0].model_copy(update={"boxes": [cid]})]})})
    original = "0010182"
    prestow = twostep.PreStowPlan(slots=(twostep.SlotCategory(
        original, twostep.category_of(box, instance), cid),))
    checked = []
    class UnavailableOriginal(PhysicalOnlyOracle):
        def is_legal(self, action):
            checked.append(action)
            return action.slot != original and super().is_legal(action)
    plan, metrics, status, message = twostep_terminal.terminal_plan(
        instance, prestow, deadline=monotonic()+3, seed=0, engine_factory=UnavailableOriginal)
    assert status == "ok", message
    action, = get_action_space("boxslot").from_plan(instance, plan)
    assert action.slot != original and action in checked
    assert metrics["carrier_approval_adjustments"] == 1
    assert metrics["carrier_approval_adjustment_limit"] == 4
    assert apply_plan(instance, plan).legal
