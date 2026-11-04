"""Public oracle integration; hull-free cases certify no physics."""
from __future__ import annotations

import pytest

from stowage.actions import get_action_space
from stowage.actions.samples import tiny_instance
from stowage.actions.space import Place
from stowage.feasibility import FeasibilityConfig, IllegalActionError
from stowage.model.fixtures import build_instance
from stowage.moves import apply_plan
from stowage.solver.scalable_oracle import build_engine


def local_fixture():
    instance = tiny_instance(75, n_load=2, n_other=0, p40=0)
    instance.vessel.hull = None  # Explicitly tests local state only; never a gold input.
    return instance


def test_local_adapter_checks_membership_and_preserves_undo_copy():
    instance = local_fixture()
    engine = build_engine(instance, get_action_space())
    action = Place(instance.load_list[0], "0010102")
    assert engine.is_legal(action)
    assert action in engine.legal_actions()
    cloned = engine.copy()
    cloned.push(action)
    assert cloned.depth == 1 and engine.depth == 0
    assert cloned.pop() == action
    assert cloned.depth == 0 and cloned.is_legal(action)
    with pytest.raises(IllegalActionError):
        engine.push(Place(instance.load_list[0], "9999982"))
    assert engine.depth == 0


def test_static_reefer_rejection_is_not_weakened():
    instance = build_instance()
    instance.vessel.hull = None
    # Isolate reefer membership from fixture cargo's unrelated heat request,
    # which correctly fails native preflight without explicit clearance data.
    instance.containers = [box.model_copy(update={"special_stowage": []})
                           for box in instance.containers]
    reefer = next(box.id for box in instance.containers if box.reefer)
    engine = build_engine(instance, get_action_space())
    assert not engine.is_legal(Place(reefer, "0020102"))


def test_complete_local_plan_records_diagnostic_provenance():
    from stowage.solver.scalable import ScalableConfig, ScalableSolver
    instance = local_fixture()
    result = ScalableSolver(ScalableConfig(beam_width=1, branch_factor=2)).plan(instance, time_limit_s=3)
    assert result.status == "ok", result.message
    assert apply_plan(instance, result.plan).legal
    assert result.metrics["oracle_mode"] == "native-v2"
    assert result.plan.provenance.versions["scalable-oracle"] == result.metrics["oracle_mode"]
    assert result.plan.ballast is None


def test_explicit_public_oracle_settings_are_respected():
    engine = build_engine(local_fixture(), get_action_space(),
                          FeasibilityConfig(extra={"lp_budget": 3}))
    assert engine.adapter_mode == "native-v2"


def test_native_v2_factory_passes_through_without_private_hooks(monkeypatch):
    from stowage import versions
    from stowage.solver import scalable_oracle
    from stowage.feasibility import FeasibilityStats

    class NativeEngine:
        __module__ = "stowage.feasibility.v2"
        def __init__(self, instance, space):
            self.instance, self.space = instance, space
        def is_legal(self, action):
            return False
        def legal_actions(self):
            return ()
        def stats(self):
            return FeasibilityStats()
    instance = local_fixture()
    space = get_action_space()
    native = NativeEngine(instance, space)
    monkeypatch.setattr(versions, "FEASIBILITY_VERSION", "2.0")
    adapter = scalable_oracle.build_engine(instance, space,
        factory=lambda instance, space, config: native)
    assert adapter.backend is native
    assert adapter.adapter_mode == "custom"
    assert not adapter.is_legal(Place(instance.load_list[0], "0010102"))
    assert not adapter.legal_actions()


def test_public_mask_rejects_unsupported_override():
    from stowage.actions.space import RestowOff
    instance = local_fixture()
    engine = build_engine(instance, get_action_space())
    action = RestowOff(instance.load_list[0])
    assert not engine.is_legal(action)
    with pytest.raises(IllegalActionError):
        engine.push(action)
    assert engine.depth == 0


def test_native_shortlist_cache_matches_public_actions_and_tracks_undo_copy():
    from stowage.solver.scalable_oracle import SolverOracle
    instance = local_fixture()
    first = Place(instance.load_list[0], "0010182")
    next_action = Place(instance.load_list[1], "0030282")
    illegal = Place(instance.load_list[0], "9999982")

    class Backend:
        def __init__(self):
            self.instance, self.space = instance, get_action_space()
            self.history, self.legal_calls = [], 0

        def legal_actions(self):
            self.legal_calls += 1
            return (next_action,) if self.history else (first,)

        def is_legal(self, action):
            raise AssertionError("Native preferred-pair membership must use the public cached set")

        def push(self, action):
            assert action == first and not self.history
            self.history.append(action)

        def pop(self):
            return self.history.pop()

        def copy(self):
            copied = Backend()
            copied.history = list(self.history)
            return copied

    backend = Backend()
    oracle = SolverOracle(backend, mode="native-v2-diagnostic-global-off")
    assert list(oracle.legal_candidates([illegal, first, next_action, first])) == [first, first]
    assert list(oracle.legal_candidates([first, illegal])) == [first]
    assert backend.legal_calls == 1
    cloned = oracle.copy()
    assert list(cloned.legal_candidates([first, illegal])) == [first]
    assert cloned.backend.legal_calls == 0
    cloned.push(first)
    assert list(cloned.legal_candidates([first, illegal, next_action])) == [next_action]
    assert cloned.backend.legal_calls == 1
    # Mutating the copy must not invalidate the untouched original's set.
    assert list(oracle.legal_candidates([first, next_action])) == [first]
    assert backend.legal_calls == 1
    assert cloned.pop() == first
    assert list(cloned.legal_candidates([next_action, first])) == [first]
    assert cloned.backend.legal_calls == 2


def test_custom_shortlist_oracle_retains_lazy_membership():
    from stowage.solver.scalable_oracle import SolverOracle
    instance = local_fixture()
    action = Place(instance.load_list[0], "0010182")

    class Backend:
        def __init__(self):
            self.instance, self.space, self.probes = instance, get_action_space(), []

        def legal_actions(self):
            raise AssertionError("Custom fixtures retain their lazy preferred-pair path")

        def is_legal(self, candidate):
            self.probes.append(candidate)
            return candidate == action

    backend = Backend()
    oracle = SolverOracle(backend, mode="custom")
    assert list(oracle.legal_candidates([action])) == [action]
    assert backend.probes == [action]


def test_index_shortlist_and_lazy_iteration_never_materialize_all_actions():
    import numpy as np
    from stowage.actions.space import Discharge
    from stowage.solver.scalable_oracle import SolverOracle
    instance = local_fixture()
    box = instance.load_list[0]
    choices = (Place(box, "0010102"), Place(box, "0010182"))
    discharge = Discharge(box)

    class Backend:
        def __init__(self):
            self.instance, self.space = instance, get_action_space()
            self.calls, self.decoded, self.history = 0, [], []
        def legal_index(self):
            self.calls += 1
            return np.array([1] if self.history else [0, 1], dtype=np.int64)
        def index_of(self, action):
            return choices.index(action) if action in choices else None
        def decode_index(self, index):
            self.decoded.append(int(index))
            return choices[int(index)]
        def legal_discharges(self):
            return (discharge,)
        def legal_actions(self):
            raise AssertionError("Indexed native engine must not materialize legal_actions")
        def push(self, action):
            self.history.append(action)
        def pop(self):
            return self.history.pop()
        def copy(self):
            other = Backend()
            other.history = list(self.history)
            return other

    backend = Backend()
    oracle = SolverOracle(backend)
    assert list(oracle.legal_candidates([choices[1], discharge, choices[0], choices[1]])) == [
        choices[1], discharge, choices[0], choices[1]]
    assert backend.calls == 1 and backend.decoded == []
    cloned = oracle.copy()
    assert list(cloned.legal_candidates(choices)) == list(choices)
    assert cloned.backend.calls == 0
    cloned.push(choices[0])
    assert list(cloned.legal_candidates(choices)) == [choices[1]]
    cloned.pop()
    assert list(cloned.legal_candidates(choices)) == list(choices)
    iterator = oracle.iter_legal_actions()
    assert next(iterator) == choices[0]
    assert backend.decoded == [0]  # Only the consumed pair is decoded.
    assert list(iterator) == [choices[1], discharge]


def test_index_counts_hold_geometry_and_completion_are_public_and_state_specific():
    from stowage.solver.scalable_oracle import SolverOracle
    instance = local_fixture()
    engine = build_engine(instance, get_action_space())
    expected = engine.legal_actions()
    boxes = list(instance.load_list) + ["unknown"]
    from stowage.conventions import parse_slot
    assert engine.legal_pair_counts(boxes) == {
        box: sum(isinstance(a, Place) and a.box == box for a in expected) for box in boxes}
    assert engine.legal_hold_counts(boxes) == {
        box: sum(isinstance(a, Place) and a.box == box and not parse_slot(a.slot).on_deck
                 for a in expected) for box in boxes}
    check = engine.completion_check()
    assert check == engine.backend.completion_check()
    check["c1f"] = False
    assert engine.completion_check() == engine.backend.completion_check()
    action = next(iter(expected))
    copied = engine.copy()
    copied.push(action)
    assert copied.legal_pair_counts([action.box])[action.box] == 0
    assert engine.legal_pair_counts([action.box])[action.box] > 0
    copied.pop()
    assert copied.legal_pair_counts(boxes) == engine.legal_pair_counts(boxes)


def test_custom_completion_diagnostics_are_unknown_when_unavailable():
    from stowage.solver.scalable_oracle import SolverOracle
    instance = local_fixture()
    class Backend:
        def __init__(self):
            self.instance, self.space = instance, get_action_space()
    assert SolverOracle(Backend(), mode="custom").completion_check() == {}
