"""Canonical constructor adapter mechanics; no hydrostatic claims."""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from stowage.actions import get_action_space
from stowage.actions.space import Place
from stowage.feasibility import FeasibilityConfig
from stowage.solver import twostep_construct as module
from stowage.solver import twostep
from stowage.solver.twostep import _canonical_instance
from tests.solver.test_twostep import interchangeable_instance
from tests.solver.test_twostep_fast_prestow import small_cargo


def mock_constructor(monkeypatch, *, complete=True, after_plan=None):
    calls = []
    class Constructor:
        def __init__(self, config):
            self.config = config

        def plan(self, canonical, *, seed, time_limit_s):
            calls.append((canonical, self.config, seed, time_limit_s))
            slots = ("0010102", "0030102")
            actions = [Place(cid, slot) for cid, slot in zip(canonical.load_list, slots)]
            if not complete:
                actions = actions[:1]
            plan = get_action_space("boxslot").expand(canonical, actions)
            if after_plan:
                after_plan()
            return SimpleNamespace(status="ok", message=None, plan=plan,
                                   planner_version="mock-constructor", metrics={})
    monkeypatch.setattr(module, "ScalableSolver", Constructor)
    monkeypatch.setattr(module, "monotonic", lambda: 0.)
    return calls


def test_passes_only_canonical_input_and_unchanged_config(monkeypatch):
    real = interchangeable_instance()
    canonical = _canonical_instance(real)
    calls = mock_constructor(monkeypatch)
    config = FeasibilityConfig(extra={"global_screen": 0})
    outcome, metrics = module.construct_preplan(canonical, deadline=12., seed=37,
                                                feasibility_config=config)
    assert outcome.status == "ok", outcome.message
    passed, shared, seed, budget = calls[0]
    assert passed is canonical and passed is not real
    assert all(len(stack.boxes) == 1 for stack in passed.yard.stacks)
    assert shared.constructor_v4 and shared.max_neighborhoods == 0
    assert shared.feasibility_config is config and seed == 37 and budget == 12.
    assert {s.representative_box for s in outcome.slots} == set(canonical.load_list)
    assert metrics["constructor_full_checker_run"] is False
    assert metrics["constructor_ballast_finisher_run"] is False
    assert metrics["carrier_validation_status"] == "ok"


def test_real_yard_is_rejected_before_constructor(monkeypatch):
    calls = mock_constructor(monkeypatch)
    outcome, _ = module.construct_preplan(interchangeable_instance(), deadline=12.)
    assert outcome.status == "error"
    assert not calls and not outcome.slots
    assert "yard-blind" in outcome.message


def test_false_complete_plan_is_rejected_for_missing_inventory(monkeypatch):
    calls = mock_constructor(monkeypatch, complete=False)
    canonical = _canonical_instance(interchangeable_instance())
    outcome, metrics = module.construct_preplan(canonical, deadline=12.)
    assert calls and outcome.status == "dead_end"
    assert "Missing load-list cargo" in outcome.message
    assert outcome.slots == outcome.actions == ()
    assert len(metrics["carrier_missing_loads"]) == 1
    assert metrics["carrier_validation_status"] == "rejected"


def test_expired_deadline_never_constructs_solver(monkeypatch):
    calls = mock_constructor(monkeypatch)
    outcome, metrics = module.construct_preplan(_canonical_instance(interchangeable_instance()), deadline=0.)
    assert outcome.status == "timeout" and not calls
    assert metrics["constructor_status"] == "not_started"
    assert outcome.slots == outcome.actions == ()


def test_late_raw_completion_is_not_accepted(monkeypatch):
    clock = [0.]
    calls = mock_constructor(monkeypatch, after_plan=lambda: clock.__setitem__(0, 10.))
    monkeypatch.setattr(module, "monotonic", lambda: clock[0])
    outcome, metrics = module.construct_preplan(_canonical_instance(interchangeable_instance()), deadline=5.)
    assert calls and metrics["constructor_status"] == "ok"
    assert outcome.status == "timeout" and not outcome.slots
    assert metrics["constructor_deadline_overrun_s"] == 5.


@pytest.mark.parametrize("gate", ["securing", "e6"])
def test_final_geometry_and_e6_rejections_are_explicit(monkeypatch, gate):
    mock_constructor(monkeypatch)
    if gate == "securing":
        monkeypatch.setattr(module, "securing_state_violations", lambda *a: ("unsupported support geometry",))
    else:
        monkeypatch.setattr(module, "e6_completion_possible", lambda *a, **kw: False)
    outcome, metrics = module.construct_preplan(_canonical_instance(interchangeable_instance()), deadline=12.)
    assert outcome.status == "dead_end"
    assert metrics["carrier_validation_failures"] and not outcome.slots


def successful_proposal(canonical):
    slots = ("0010102", "0010104", "0030102", "0030104",
             "0010202", "0010204", "0030202", "0030204")
    boxes = {b.id: b for b in canonical.containers}
    actions = tuple(Place(cid, slot) for cid, slot in zip(canonical.load_list, slots))
    return twostep.PreStowPlan(actions=actions, slots=tuple(
        twostep.SlotCategory(a.slot, twostep.category_of(boxes[a.box], canonical), a.box)
        for a in actions))


def test_dispatch_canonicalizes_once_and_ignores_real_yard(monkeypatch):
    real = small_cargo()
    changed = real.model_copy(update={"yard": real.yard.model_copy(update={
        "stacks": [s.model_copy(update={"boxes": real.yard.stacks[-1-i].boxes})
                   for i, s in enumerate(real.yard.stacks)]})})
    observed = []
    canonicalizations = []
    original = twostep._canonical_instance
    def canonicalize(instance):
        canonicalizations.append(instance)
        return original(instance)
    def primary(canonical, engine, **kwargs):
        observed.append(canonical.model_dump())
        return successful_proposal(canonical)
    monkeypatch.setattr(twostep, "_canonical_instance", canonicalize)
    monkeypatch.setattr(twostep, "_fast_prestow", primary)
    monkeypatch.setattr(twostep, "make_feasibility_engine", lambda *a, **kw: object())
    a = twostep.preplan(real, seed=7, time_limit_s=5.)
    b = twostep.preplan(changed, seed=7, time_limit_s=5.)
    assert a.status == b.status == "ok" and a.slots == b.slots
    assert observed[0] == observed[1] and len(canonicalizations) == 2


@pytest.mark.parametrize("explicit_config", [False, True])
@pytest.mark.parametrize("greedy_success", [False, True])
def test_success_reaches_terminal_and_exposes_method(monkeypatch, explicit_config, greedy_success):
    from stowage.model import MovePlan
    from stowage.solver import twostep_terminal
    instance = small_cargo()
    called = []
    configs = []
    config = FeasibilityConfig(extra={"global_screen": 1}) if explicit_config else None
    def engine(canonical, space, *, config):
        configs.append(config)
        return object()
    def primary(canonical, engine, **kwargs):
        return (successful_proposal(canonical) if greedy_success else
                twostep.PreStowPlan(status="dead_end", message="mock greedy failure"))
    def constructor(canonical, **kwargs):
        configs.append(kwargs["feasibility_config"])
        assert not greedy_success, "Complete greedy search must skip constructor"
        return successful_proposal(canonical), {"constructor_status": "ok", "constructor_shared_search_family": True}
    def terminal(real, proposal, **kwargs):
        called.append((real, proposal))
        configs.append(kwargs["feasibility_config"])
        plan = MovePlan(id="mock-terminal", instance_id=real.id, call_index=real.call_index)
        return plan, {"terminal_status": "ok"}, "ok", None
    monkeypatch.setattr(module, "construct_preplan", constructor)
    monkeypatch.setattr(twostep, "_fast_prestow", primary)
    monkeypatch.setattr(twostep, "make_feasibility_engine", engine)
    monkeypatch.setattr(twostep_terminal, "terminal_plan", terminal)
    result = twostep.TwoStepPlanner(feasibility_config=config).plan(instance, seed=7, time_limit_s=5.)
    assert result.status == "ok" and len(called) == 1
    assert called[0][0] is instance
    assert result.metrics["preplan_method"] == ("bounded_greedy" if greedy_success else "canonical_constructor_after_greedy")
    assert result.metrics["constructor_status"] == ("not_started" if greedy_success else "ok")
    assert result.metrics["preplan_constructor_fallback_used"] is (not greedy_success)
    assert all(c is configs[0] for c in configs)
    if explicit_config:
        assert configs[0] is config
    assert result.metrics["effective_global_screen"] == (1 if explicit_config else 0)


def test_incomplete_constructor_fallback_retains_both_failures(monkeypatch):
    calls = mock_constructor(monkeypatch, complete=False)
    instance = small_cargo()
    captured = []
    def primary(canonical, engine, **kwargs):
        captured.append(canonical)
        return twostep.PreStowPlan(status="dead_end", message="mock greedy failure")
    monkeypatch.setattr(twostep, "make_feasibility_engine", lambda *a, **kw: object())
    monkeypatch.setattr(twostep, "_fast_prestow", primary)
    result = twostep.preplan(instance, time_limit_s=5.)
    assert result.status == "dead_end" and calls and captured
    assert result.search_metrics["constructor_status"] == "ok"  # raw false completion
    assert result.search_metrics["preplan_constructor_status"] == "dead_end"
    assert result.search_metrics["carrier_validation_status"] == "rejected"
    assert result.search_metrics["carrier_missing_loads"]
    assert result.search_metrics["preplan_greedy_primary_status"] == "dead_end"
    assert result.search_metrics["preplan_constructor_fallback_used"] is True
    assert result.search_metrics["preplan_method"] == "canonical_constructor_after_greedy"


def test_tiny_dispatch_keeps_legacy_search(monkeypatch):
    from tests.solver.test_twostep import PhysicalOnlyOracle
    def forbidden(*args, **kwargs):
        raise AssertionError("Tiny must retain legacy DFS")
    monkeypatch.setattr(module, "construct_preplan", forbidden)
    monkeypatch.setattr(twostep, "make_feasibility_engine", PhysicalOnlyOracle)
    result = twostep.preplan(interchangeable_instance(), time_limit_s=5.)
    assert result.status == "ok" and result.search_metrics is None


@pytest.mark.parametrize("deadline, initial_deadline", [(20., 14.), (110., 20.)])
def test_greedy_slice_is_bounded_and_constructor_keeps_unused_time(monkeypatch, deadline, initial_deadline):
    clock = [10.]
    deadlines = []
    config = FeasibilityConfig(extra={"global_screen": 1})
    def primary(canonical, engine, **kwargs):
        deadlines.append(kwargs["deadline"])
        clock[0] = 11.
        return twostep.PreStowPlan(status="dead_end")
    def fallback(canonical, **kwargs):
        assert kwargs["feasibility_config"] is config
        deadlines.append(kwargs["deadline"])
        return successful_proposal(canonical), {"constructor_status": "ok"}
    monkeypatch.setattr(twostep, "monotonic", lambda: clock[0])
    monkeypatch.setattr(module, "construct_preplan", fallback)
    monkeypatch.setattr(twostep, "make_feasibility_engine", lambda *a, **kw: object())
    monkeypatch.setattr(twostep, "_fast_prestow", primary)
    result = twostep._preplan(small_cargo(), beam_width=4, deadline=deadline, feasibility_config=config)
    assert result.status == "ok" and deadlines == [initial_deadline, deadline]
    assert result.search_metrics["effective_global_screen"] == 1


def test_late_greedy_completion_is_discarded_before_constructor_fallback(monkeypatch):
    clock = [10.]
    def primary(canonical, engine, **kwargs):
        clock[0] = kwargs["deadline"]
        return successful_proposal(canonical)
    def fallback(canonical, **kwargs):
        return successful_proposal(canonical), {"constructor_status": "ok"}
    monkeypatch.setattr(twostep, "monotonic", lambda: clock[0])
    monkeypatch.setattr(twostep, "make_feasibility_engine", lambda *a, **kw: object())
    monkeypatch.setattr(twostep, "_fast_prestow", primary)
    monkeypatch.setattr(module, "construct_preplan", fallback)
    result = twostep._preplan(small_cargo(), beam_width=4, deadline=20.)
    assert result.status == "ok"
    assert result.search_metrics["preplan_greedy_primary_status"] == "timeout"
    assert result.search_metrics["preplan_constructor_fallback_used"] is True
