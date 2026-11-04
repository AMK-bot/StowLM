"""Index-mask iteration avoids materializing the full legal action vocabulary."""
from fractions import Fraction
from time import monotonic

import numpy as np

from stowage.actions import get_action_space
from stowage.actions.expander import Expander
from stowage.actions.space import Discharge, Place
from stowage.model import BallastCondition, Instance
from stowage.model.fixtures import build_instance
from stowage.moves import apply_move, initial_state


def one_box(*, discharge=False):
    data = build_instance().model_dump()
    box = data["containers"][1]
    box.update(dg=None, oog=None, special_stowage=[], gross_mass_kg=8000.)
    if discharge:
        box["pod"] = data["port"]["locode"]
    data.update(containers=[box], load_list=[] if discharge else [box["id"]],
                discharge_list=[box["id"]] if discharge else [])
    data["yard"]["stacks"] = ([] if discharge else [dict(key="A-001-01", length_ft=20,
                                                        boxes=[box["id"]])])
    data["onboard"]["placements"] = ([dict(container_id=box["id"], slot="0010182")]
                                          if discharge else [])
    return Instance.model_validate(data)


class IndexEngine:
    """Only the public index surface is allowed; object enumeration is a bug."""
    def __init__(self, instance):
        self.instance = instance
        self.ex = Expander(instance)
        self.state = initial_state(instance)
        self.index_boxes = tuple(sorted(box.id for box in instance.containers))
        self.index_slots = ("0010182", "0030182")
        self.n_slots = len(self.index_slots)
        self.decoded = []

    def legal_actions(self):
        raise AssertionError("index-capable solver must not enumerate action objects")

    def legal_index(self):
        return np.array([] if self.instance.discharge_list or self.is_complete() else [0, 1],
                        dtype=np.int64)

    def decode_index(self, flat):
        flat = int(flat)
        self.decoded.append(flat)
        return Place(self.index_boxes[flat // self.n_slots], self.index_slots[flat % self.n_slots])

    def legal_discharges(self):
        return tuple(Discharge(cid) for cid in self.instance.discharge_list
                     if any(p.container_id == cid for p in self.state.ship.placements))

    def is_complete(self):
        aboard = {p.container_id for p in self.state.ship.placements}
        return set(self.instance.load_list) <= aboard and not set(self.instance.discharge_list) & aboard

    def copy(self):
        from copy import copy
        result = copy(self)
        result.ex = self.ex.copy()
        result.decoded = []
        return result

    def push(self, action):
        for move in self.ex.step(action):
            self.state = apply_move(self.instance, self.state, move)

    def dead_end(self):
        return None


def test_index_iterator_decodes_lazily_and_preserves_place_then_discharge_order():
    from stowage.solver.exact_engine import iter_legal_actions

    instance = one_box()
    engine = IndexEngine(instance)
    cid = instance.load_list[0]
    engine.legal_discharges = lambda: (Discharge(cid),)
    actions = iter_legal_actions(engine, deadline=monotonic() + 1.)
    assert engine.decoded == []
    assert next(actions) == Place(cid, "0010182")
    assert engine.decoded == [0]
    assert list(actions) == [Place(cid, "0030182"), Discharge(cid)]
    assert engine.decoded == [0, 1]


def test_expired_index_iterator_does_not_decode_remaining_objects():
    from stowage.solver.exact_engine import iter_legal_actions

    engine = IndexEngine(one_box())
    actions = iter_legal_actions(engine, deadline=monotonic() - 1.)
    assert list(actions) == []
    assert engine.decoded == []


def test_fallback_iterator_keeps_non_index_test_doubles_compatible():
    from stowage.solver.exact_engine import iter_legal_actions

    cid = one_box().load_list[0]
    expected = [Place(cid, "0010182"), Discharge(cid)]
    class LegacyEngine:
        def legal_actions(self):
            return expected
    assert list(iter_legal_actions(LegacyEngine(), deadline=monotonic() + 1.)) == expected


def test_greedy_seed_ranks_indices_without_decoding_every_candidate():
    from stowage.solver.exact_engine import _greedy_seed

    instance = one_box()
    engine = IndexEngine(instance)
    result = _greedy_seed(instance, engine, Expander(instance), initial_state(instance),
                          deadline=monotonic() + 1., weights={"load": Fraction(0)})
    assert result is not None
    assert len(engine.decoded) == 1
    assert result[-1] == (Place(instance.load_list[0], "0010182"),)


def test_astar_index_path_loads_and_discharges_without_object_enumeration(monkeypatch):
    import stowage.solver.exact as exact
    import stowage.solver.exact_engine as module

    monkeypatch.setattr(module, "make_feasibility_engine", lambda inst, space: IndexEngine(inst))
    monkeypatch.setattr(exact, "_validate_terminal", lambda inst, plan, ship, **kw:
        ("yes", BallastCondition(source="planner", tank_masses_kg={}), plan.provenance))
    for discharge in (False, True):
        instance = one_box(discharge=discharge)
        result = exact.solve_exact(instance, time_limit_s=1.)
        assert result.status == "OPTIMAL"
        assert result.metrics["mask_verified"]
        assert [move.kind for move in result.plan.moves] == ["discharge" if discharge else "load"]
        assert result.plan.actions[0]["op"] == ("D" if discharge else "P")


def test_iterator_checks_deadline_between_decodes(monkeypatch):
    import stowage.solver.exact_engine as module

    clock = [0.]
    monkeypatch.setattr(module, "perf_counter", lambda: clock[0])
    engine = IndexEngine(one_box())
    actions = module.iter_legal_actions(engine, deadline=1.)
    assert next(actions) == Place(engine.index_boxes[0], "0010182")
    clock[0] = 2.
    assert list(actions) == []
    assert engine.decoded == [0]


def test_partial_index_surface_uses_protocol_fallback():
    from stowage.solver.exact_engine import iter_legal_actions

    expected = [Place(one_box().load_list[0], "0010182")]
    class OldDouble:
        def legal_index(self):
            raise AssertionError("an incomplete index API must use the fallback")
        def legal_actions(self):
            return expected
    assert list(iter_legal_actions(OldDouble(), deadline=monotonic() + 1.)) == expected


def test_diagnostic_c2_off_config_is_forwarded_but_never_labeled_production_g1(monkeypatch):
    from multiprocessing import get_context
    import stowage.solver.exact as exact
    import stowage.solver.exact_engine as module
    from stowage.feasibility import FeasibilityConfig

    expected = FeasibilityConfig(extra={"global_screen": 0})
    calls = get_context("fork").Value("i", 0)
    def factory(inst, space, *, config):
        assert config == expected
        calls.value += 1
        return IndexEngine(inst)
    monkeypatch.setattr(module, "make_feasibility_engine", factory)
    monkeypatch.setattr(exact, "_validate_terminal", lambda inst, plan, ship, **kw:
        ("yes", BallastCondition(source="planner", tank_masses_kg={}), plan.provenance))
    instance = one_box()
    result = exact.solve_exact(instance, time_limit_s=1., feasibility_config=expected)
    assert calls.value >= 2  # Search construction and independent replay use the same config.
    assert result.status == "OPTIMAL" and result.plan is not None
    assert result.metrics["checker_passed"] and result.metrics["configured_engine_replay_verified"]
    assert result.metrics["mask_verified"] is False and result.metrics["shippable"] is False
    assert result.metrics["g1_status"] == "configured_mask_verified_not_production"
    assert result.metrics["feasibility_config"]["extra"] == {"global_screen": 0}
    planned = exact.ExactPlanner(feasibility_config=expected).plan(instance, time_limit_s=1.)
    assert planned.plan is not None
    assert planned.metrics["mask_verified"] is False and planned.metrics["shippable"] is False


def test_relaxed_oracle_rejects_irrelevant_feasibility_config():
    import pytest
    from stowage.feasibility import FeasibilityConfig
    from stowage.solver.exact import solve_exact

    with pytest.raises(ValueError, match="only to operational"):
        solve_exact(one_box(), check_physics=False,
                    feasibility_config=FeasibilityConfig(extra={"global_screen": 0}))


def test_greedy_seed_keeps_discharges_when_placement_index_is_empty():
    from stowage.solver.exact_engine import _greedy_seed

    instance = one_box(discharge=True)
    engine = IndexEngine(instance)
    result = _greedy_seed(instance, engine, Expander(instance), initial_state(instance),
                          deadline=monotonic() + 1., weights={"discharge": Fraction(0)})
    assert result is not None
    assert result[-1] == (Discharge(instance.discharge_list[0]),)
    assert engine.decoded == []


def test_lazy_iteration_interruption_keeps_unseen_branch_bound(monkeypatch):
    import stowage.solver.exact_engine as module

    instance = one_box()
    engine = IndexEngine(instance)
    clock = [0.]
    decode = engine.decode_index
    def interrupted_decode(flat):
        action = decode(flat)
        clock[0] = 2.
        return action
    engine.decode_index = interrupted_decode
    monkeypatch.setattr(module, "perf_counter", lambda: clock[0])
    monkeypatch.setattr(module, "make_feasibility_engine", lambda inst, space: engine)
    # Call the cooperative internal loop directly to inspect its frontier
    # certificate. The public watchdog has separate deadline regressions.
    result = module.solve_engine(instance, time_limit_s=1., max_nodes=10,
                                 seed=0, num_workers=1)
    assert engine.decoded == [0]
    assert result.status == "TIME_LIMIT"
    assert result.plan is None and result.objective is None
    assert result.lower_bound == 0  # The unseen legal branch was not excluded.
