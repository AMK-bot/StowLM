"""Fixed-assignment sequencing mechanics; fixture doubles do not certify hulls."""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from stowage.actions.expander import Expander
from stowage.actions.space import Place
from stowage.conventions import make_container_id
from stowage.model import Placement, YardStack, YardState
from stowage.moves import apply_move, apply_plan, initial_state
from stowage.solver import construct_sequence as module
from tests.solver.test_construct_model_1028 import plain_instance


class ReplayOracle:
    """Real physical moves only, without stability/IMDG qualification."""

    def __init__(self, instance, space):
        self.instance, self.space = instance, space
        self.ex, self.state, self.history = Expander(instance), initial_state(instance), []

    def copy(self):
        other = object.__new__(type(self))
        other.__dict__.update(self.__dict__)
        other.ex, other.history = self.ex.copy(), list(self.history)
        return other

    def is_legal(self, action):
        try:
            trial, state = self.ex.copy(), self.state
            for move in trial.step(action):
                state = apply_move(self.instance, state, move)
            return True
        except (ValueError, KeyError):
            return False

    def push(self, action):
        assert self.is_legal(action), "sequencer attempted a rejected public push"
        for move in self.ex.step(action):
            self.state = apply_move(self.instance, self.state, move)
        self.history.append(action)

    def is_complete(self):
        return (all(self.ex.where.get(cid, (None,))[0] == "ship" for cid in self.instance.load_list)
                and not self.ex.buffer
                and all(self.ex.where.get(cid, (None,))[0] != "ship" for cid in self.instance.discharge_list))

    def completion_check(self):
        return {"c1a": True, "c1f": True, "c2": True}


def independent_yard(instance):
    return instance.model_copy(update={"yard": YardState(yard_id=instance.yard.yard_id,
        stacks=[YardStack(key=f"A-{2*i+1:03d}-01", length_ft=20, boxes=[cid])
                for i, cid in enumerate(instance.load_list)])})


@pytest.fixture
def physical_mask(monkeypatch):
    monkeypatch.setattr(module, "make_feasibility_engine", ReplayOracle)


def test_bottom_up_and_hatch_hold_first(physical_mask):
    instance = independent_yard(plain_instance())
    a, b = sorted(instance.load_list)
    assignment = {a: "0010182", b: "0010102"}
    diagnostics = {}
    actions, plan = module.sequence(instance, assignment, module.monotonic()+2,
                                   diagnostics=diagnostics)
    assert actions == (Place(b, "0010102"), Place(a, "0010182"))
    assert apply_plan(instance, plan).legal
    assert all(event["completion"]["c2"] for event in diagnostics["trace"]
               if event["event"] == "accepted")


def test_stack_support_predecessor_overrides_retrieval_order(physical_mask):
    instance = independent_yard(plain_instance())
    a, b = sorted(instance.load_list)
    actions, _ = module.sequence(instance, {a: "0010104", b: "0010102"}, module.monotonic()+2)
    assert [action.box for action in actions] == [b, a]


def test_backtracks_when_first_mask_legal_choice_is_a_trap(monkeypatch):
    instance = independent_yard(plain_instance())
    a, b = sorted(instance.load_list)

    class Trap(ReplayOracle):
        def is_legal(self, action):
            if self.history and self.history[0].box == a:
                return False
            return super().is_legal(action)

    monkeypatch.setattr(module, "make_feasibility_engine", Trap)
    diagnostics = {}
    actions, plan = module.sequence(instance, {a: "0010102", b: "0030102"},
        module.monotonic()+2, diagnostics=diagnostics)
    assert [action.box for action in actions] == [b, a]
    assert diagnostics["backtracks"] >= 1
    assert diagnostics["mask_rejections"] == 1
    assert apply_plan(instance, plan).legal


def test_rejected_child_reports_completion_without_pushing(monkeypatch):
    instance = independent_yard(plain_instance())
    a, b = sorted(instance.load_list)
    pushes = []

    class Reject(ReplayOracle):
        def __init__(self, *args):
            super().__init__(*args)
            self._cur = None

        def is_legal(self, action):
            return False

        def push(self, action):
            pushes.append(action)
            raise AssertionError("rejected action was pushed")

        def _child(self, state, action):
            return action

        def completion_check(self):
            return {"c1a": self._cur is None, "c2": self._cur is None}

    monkeypatch.setattr(module, "make_feasibility_engine", Reject)
    with pytest.raises(module.SequenceFailure) as caught:
        module.sequence(instance, {a: "0010102", b: "0030102"}, module.monotonic()+2)
    details = caught.value.details
    rejection = next(event for event in details["trace"] if event["event"] == "mask_rejected")
    assert rejection["current_completion"]["c2"]
    assert rejection["child_completion"]["checks"] == {"c1a": False, "c2": False}
    assert details["prefix_actions"] == []
    assert details["pending"] == {a: "0010102", b: "0030102"}
    assert not pushes


def test_restored_background_keeps_final_slot_after_hold_access(physical_mask):
    instance = independent_yard(plain_instance())
    a, b = sorted(instance.load_list)
    retained = instance.containers[0].model_copy(update={"id": make_container_id("QZXU", 999999)})
    instance = instance.model_copy(update={
        "containers": [*instance.containers, retained],
        "onboard": instance.onboard.model_copy(update={
            "placements": [Placement(container_id=retained.id, slot="0010182")]})})
    actions, plan = module.sequence(instance, {a: "0010102", b: "0030102"}, module.monotonic()+2)
    assert actions[-1] == Place(retained.id, "0010182")
    replay = apply_plan(instance, plan)
    assert replay.legal
    assert {p.container_id: p.slot for p in replay.final_state.ship.placements}[retained.id] == "0010182"
    assert any(move.kind == "restow_off" for move in plan.moves)


def test_deadline_before_setup_never_starts_mask(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("expired sequencing budget initialized an engine")
    monkeypatch.setattr(module, "make_feasibility_engine", forbidden)
    with pytest.raises(module.SequenceFailure) as caught:
        module.sequence(plain_instance(), {}, module.monotonic()-1)
    assert caught.value.details["reason"] == "deadline"


def test_node_budget_allows_last_complete_action(physical_mask):
    instance = plain_instance()
    a = instance.load_list[0]
    instance = instance.model_copy(update={"load_list": [a]})
    actions, _ = module.sequence(instance, {a: "0010102"}, module.monotonic()+2, max_nodes=1)
    assert actions == (Place(a, "0010102"),)


def test_visibility_uses_source_cap_as_separate_diagnostic():
    instance = plain_instance()
    ex = Expander(instance)
    cid = instance.load_list[0]
    ex.step(Place(cid, "0010182"))
    ids = [stack.id for stack in instance.vessel.stacks]
    caps = [float("inf")]*len(ids)
    stack_id = ex.cells["0010182"].stack_id
    caps[ids.index(stack_id)] = ex.stacks[stack_id].base_z_m+1.
    engine = SimpleNamespace(T=SimpleNamespace(stack_ids=ids, extra={"stack_visibility_top": caps}))
    result = module._visibility(engine, ex)
    assert result["available"] and not result["passed"]
    assert result["violations"][0]["stack"] == stack_id


def test_bound_exhaustion_does_not_claim_infeasibility(monkeypatch):
    instance = independent_yard(plain_instance())
    a, b = sorted(instance.load_list)
    class Reject(ReplayOracle):
        def is_legal(self, action):
            return False
    monkeypatch.setattr(module, "make_feasibility_engine", Reject)
    with pytest.raises(module.SequenceFailure) as caught:
        module.sequence(instance, {a: "0010102", b: "0030102"},
                        module.monotonic()+2, max_nodes=1)
    assert caught.value.details["reason"] == "node_limit"
    assert "infeasible" not in str(caught.value)


def test_rejection_counts_survive_both_trace_limits(monkeypatch):
    instance = independent_yard(plain_instance())
    a, b = sorted(instance.load_list)
    class Reject(ReplayOracle):
        def is_legal(self, action):
            return False
    monkeypatch.setattr(module, "make_feasibility_engine", Reject)
    monkeypatch.setattr(module, "_ready", lambda *args: ([Place(a, "0010102")]*130, []))
    monkeypatch.setattr(module, "_static_trial", lambda *args: [])
    monkeypatch.setattr(module, "audit_candidate_stages", lambda *args, **kwargs:
        {"available": True, "failed_stages": ["local_fast"], "stages": [
            {"tests": {"stack_mass": False}}]})
    monkeypatch.setattr(module, "_rejected_child", lambda *args: {"checks": {"c1a": False}})
    with pytest.raises(module.SequenceFailure) as caught:
        module.sequence(instance, {a: "0010102", b: "0030102"}, module.monotonic()+2, max_trace=2)
    details = caught.value.details
    assert len(details["trace"]) == 2
    assert len(details["rejection_trace"]) == 128
    assert details["rejection_depth_counts"] == {"0": 130}
    assert details["rejection_family_counts"]["c1a"] == 130
    assert details["rejection_family_counts"]["local/stack_mass"] == 130
    assert details["last_rejection"]["action"]["box"] == a
    assert details["last_rejection_prefix_actions"] == []


def test_final_c2_audit_survives_later_search_deadline(monkeypatch):
    instance = independent_yard(plain_instance())
    a, b = sorted(instance.load_list)
    clock, audits = [0.], []
    class Slow(ReplayOracle):
        def is_legal(self, action):
            clock[0] = 2.
            return True
    def audit(*args):
        audits.append(True)
        return {"available": True, "feasible": True, "certificate": False}
    monkeypatch.setattr(module, "monotonic", lambda: clock[0])
    monkeypatch.setattr(module, "make_feasibility_engine", Slow)
    monkeypatch.setattr(module, "_final_assignment_c2", audit)
    with pytest.raises(module.SequenceFailure) as caught:
        module.sequence(instance, {a: "0010102", b: "0030102"}, 1.)
    assert caught.value.details["reason"] == "deadline"
    assert caught.value.details["final_assignment_c2"]["feasible"]
    assert len(audits) == 1


def test_final_assignment_c2_uses_the_same_numeric_slack():
    import numpy as np
    instance = plain_instance()
    cid = instance.load_list[0]
    mass = next(box.gross_mass_kg for box in instance.containers if box.id == cid)
    screen = SimpleNamespace(A=np.asarray([[1.]]), At=np.zeros((1, 0)), const=np.asarray([0.]),
        lo=np.asarray([mass+1.]), hi=np.asarray([mass+1.]), tank_lo=[], tank_hi=[],
        _slack=lambda: np.asarray([2.]))
    tables = SimpleNamespace(cell_ids=["0010102"], cell_index={"0010102": 0})
    engine = SimpleNamespace(T=tables, _global=screen, instance=instance)
    result = module._final_assignment_c2(engine, {cid: "0010102"}, module.monotonic()+1)
    assert result["available"] and result["feasible"]
    assert result["same_engine_numeric_slack"] and not result["certificate"]
    screen._slack = lambda: np.asarray([0.])
    result = module._final_assignment_c2(engine, {cid: "0010102"}, module.monotonic()+1)
    assert result["available"] and result["feasible"] is False


def test_raw_completion_true_does_not_hide_unavailable_required_model():
    engine = SimpleNamespace(_global_error="qualification is stale",
                             completion_check=lambda: {"c1a": True, "c2": True})
    result = module._completion(engine)
    assert result["c2"] is True  # Exact raw API value remains visible.
    assert result["available"] is False
    assert result["required_model_error"] == "qualification is stale"
