"""D73 row adjustment applies with demo rules on every terminal dispatch.

Physical-only fixtures test terminal policy and real move replay, not hydrostatic
certification. Final ballast and full checker approval remain separate gates.
"""
from time import monotonic

import pytest

from stowage.actions import get_action_space
from stowage.actions.space import Place
from stowage.moves import apply_plan
from stowage.rules.catalogue import rule_freeze_v1
from stowage.solver import twostep, twostep_terminal
from tests.solver.test_twostep import PhysicalOnlyOracle, interchangeable_instance


def _single_booking(size):
    instance = interchangeable_instance()
    cid = instance.load_list[-1]
    box = next(b for b in instance.containers if b.id == cid)
    instance = instance.model_copy(update={"size_class": size,
        "containers": [box], "load_list": [cid],
        "yard": instance.yard.model_copy(update={"stacks": [
            instance.yard.stacks[0].model_copy(update={"boxes": [cid]})]})})
    prestow = twostep.PreStowPlan(slots=(twostep.SlotCategory(
        "0010182", twostep.category_of(box, instance), cid),))
    return instance, prestow


def _force_cp_fallback(monkeypatch):
    def reject_fast(*args, metrics, **kwargs):
        metrics["terminal_method"] = "controlled fast search failure"
        return None
    monkeypatch.setattr(twostep_terminal, "_fast_terminal", reject_fast)


@pytest.mark.parametrize("dispatch", ["tiny", "small", "small_cp_fallback"])
def test_demo_d73_adjustment_requires_legal_same_category_neighbor(monkeypatch, dispatch):
    with rule_freeze_v1(enabled=False):
        instance, prestow = _single_booking("tiny" if dispatch == "tiny" else "small")
        if dispatch == "small_cp_fallback":
            _force_cp_fallback(monkeypatch)
        original = prestow.slots[0].slot
        checked = []
        class OriginalRowUnavailable(PhysicalOnlyOracle):
            def legal_actions(self):
                return [a for a in super().legal_actions()
                        if not isinstance(a, Place) or a.slot != original]
            def is_legal(self, action):
                checked.append(action)
                return super().is_legal(action)
        plan, metrics, status, message = twostep_terminal.terminal_plan(
            instance, prestow, deadline=monotonic()+3, seed=0,
            engine_factory=OriginalRowUnavailable)
        assert status == "ok", message
        action, = get_action_space("boxslot").from_plan(instance, plan)
        assert action.slot != original and action in checked
        assert action.box == prestow.slots[0].representative_box
        assert action.slot[:3] == original[:3] and action.slot[-2:] == original[-2:]
        assert metrics["carrier_approval_adjustments"] == 1
        assert metrics["carrier_approval_adjustment_limit"] == 4
        assert apply_plan(instance, plan).legal
        if dispatch != "small":
            assert metrics["terminal_cp_solves"] > 0
        else:
            assert metrics["terminal_cp_solves"] == 0
        if dispatch == "small_cp_fallback":
            assert metrics["terminal_cp_fallback"] == 1


@pytest.mark.parametrize("dispatch", ["tiny", "small", "small_cp_fallback"])
def test_demo_rejected_neighbors_never_count_as_carrier_approval(monkeypatch, dispatch):
    with rule_freeze_v1(enabled=False):
        instance, prestow = _single_booking("tiny" if dispatch == "tiny" else "small")
        if dispatch == "small_cp_fallback":
            _force_cp_fallback(monkeypatch)
        class NoLegalRow(PhysicalOnlyOracle):
            def legal_actions(self):
                return []
        plan, metrics, status, _ = twostep_terminal.terminal_plan(
            instance, prestow, deadline=monotonic()+3, seed=0, engine_factory=NoLegalRow)
        assert status == "dead_end" and plan is None
        assert metrics["carrier_approval_adjustments"] == 0
        assert metrics["carrier_approval_adjustment_limit"] == 4
        assert metrics["terminal_candidates"] == 0
