"""Tighten-only adapter tests, with synthetic objects and a train-only smoke."""
from copy import copy
from types import SimpleNamespace as NS

import numpy as np
import pytest

from llm.instructions.apply import ConstrainedInstance
from stowage.actions.space import Place

from experiments.mvp.reader.constraints import (
    InfeasibleInstruction, compile_one_port_per_hatch,
    constrain_frontier_problem, wrap_engine,
)


def synthetic():
    cells, stacks = [], []
    for bay, cover in [(1, "A"), (3, "B")]:
        for deck, tier in [(False, 2), (True, 82)]:
            sid = f"s{bay}-{tier}"
            stacks.append(NS(id=sid, on_deck=deck, hatch_cover_id=cover))
            cells.append(NS(id=f"{bay:03d}01{tier:02d}", stack_id=sid))
    covers = [NS(id=c, hold_stack_ids=[s.id for s in stacks if s.hatch_cover_id == c and not s.on_deck],
                 deck_stack_ids=[s.id for s in stacks if s.hatch_cover_id == c and s.on_deck]) for c in ["A", "B"]]
    return NS(id="synthetic", vessel=NS(cells=cells, stacks=stacks, hatch_covers=covers),
              containers=[NS(id="x", pod="SGSIN"), NS(id="y", pod="MYPKG")],
              rotation=NS(calls=[NS(locode=p) for p in ["SGSIN", "MYPKG"]]),
              onboard=NS(placements=[]), discharge_list=[], load_list=["x", "y"])


def test_same_cover_spans_hold_and_deck_and_long_footprint_all_covers():
    compiled = compile_one_port_per_hatch(synthetic(), {"A": "SGSIN", "B": "MYPKG"})
    assert compiled.forbids("x", "0010102") is None
    assert compiled.forbids("x", "0010182") is None
    assert compiled.forbids("y", "0010102")
    assert compiled.forbids("y", "0010182")
    assert compiled.forbids("x", "0020102")  # A and B touched: B rejects x.
    assert compiled.forbids("y", "0020182")  # A and B touched: A rejects y.


def test_independent_audit_detects_mixing_between_zones():
    compiled = compile_one_port_per_hatch(synthetic(), {"A": "SGSIN", "B": "MYPKG"})
    assert not compiled.audit_positions({"x": "0010102", "y": "0030182"})
    errors = compiled.audit_positions({"x": "0010102", "y": "0010182"})
    assert any("mixed discharge ports at A" in e for e in errors)


def test_previous_constraints_are_intersected_and_input_mapping_copied():
    instance = synthetic()
    base = ConstrainedInstance(instance, _all_boxes={"0010102"})
    mapping = {"A": "SGSIN", "B": "MYPKG"}
    compiled = compile_one_port_per_hatch(instance, mapping, base=base)
    assert compiled.forbids("x", "0010102")
    assert compiled.forbids("x", "0030102")
    assert compiled.forbids("x", "0010182") is None
    mapping["A"] = "MYPKG"
    assert compiled.cover_ports["A"] == "SGSIN"
    assert base._all_boxes == {"0010102"}


def test_unallocated_cover_closed_and_bad_references_fail():
    instance = synthetic()
    compiled = compile_one_port_per_hatch(instance, {"A": "SGSIN"})
    assert compiled.forbids("y", "0030102")
    assert compiled.forbids("unknown", "0010102")
    assert compiled.forbids("x", "0090102")
    for mapping in [{"not-real": "SGSIN"}, {"A": "XXXXY"}, {"A": ["SGSIN", "MYPKG"]}]:
        with pytest.raises(InfeasibleInstruction):
            compile_one_port_per_hatch(instance, mapping)


def test_retained_cargo_fixes_cover_and_conflict_fails_closed():
    instance = synthetic()
    instance.onboard.placements = [NS(container_id="x", slot="0010102")]
    with pytest.raises(InfeasibleInstruction):
        compile_one_port_per_hatch(instance, {"A": "MYPKG", "B": "SGSIN"})
    instance.onboard.placements.append(NS(container_id="y", slot="0010182"))
    with pytest.raises(InfeasibleInstruction):
        compile_one_port_per_hatch(instance, {"A": "SGSIN", "B": "MYPKG"})
    instance.discharge_list = ["y"]
    assert compile_one_port_per_hatch(instance, {"A": "SGSIN", "B": "MYPKG"})


class FakeEngine:
    def __init__(self, instance):
        self.instance = instance
        self.index_boxes = ["x", "y"]
        self.index_slots = ["0010102", "0010182", "0030102", "0030182", "0020102"]
        self.T = NS(static=np.ones((2, 5), dtype=bool))
        self.T.static[0, 1] = False  # An original restriction may never be undone.
        self.actions = [Place(b, s) for i, b in enumerate(self.index_boxes)
                        for j, s in enumerate(self.index_slots) if self.T.static[i, j]]
    def legal_index(self):
        return np.flatnonzero(self.T.static.ravel())
    def legal_actions(self):
        return self.actions
    def is_legal(self, action):
        return action in self.actions
    def legal_slots_for_box(self, box):
        return [a.slot for a in self.actions if a.box == box]
    def copy(self):
        return copy(self)


def test_mask_and_frontier_static_are_same_intersection_without_shared_mutation():
    instance = synthetic()
    compiled = compile_one_port_per_hatch(instance, {"A": "SGSIN", "B": "MYPKG"})
    original = FakeEngine(instance)
    snapshot = original.T.static.copy()
    wrapped = wrap_engine(original, compiled)
    expected = [a for a in original.legal_actions() if not compiled.forbids(a.box, a.slot)]
    assert wrapped.legal_actions() == expected
    assert wrapped.is_legal(Place("x", "0030102")) is False
    assert set(wrapped.legal_index()) == {i*5+j for i, b in enumerate(original.index_boxes)
        for j, s in enumerate(original.index_slots) if snapshot[i, j] and not compiled.forbids(b, s)}
    assert np.array_equal(original.T.static, snapshot)
    assert np.array_equal(wrapped.T.static.ravel().nonzero()[0], wrapped.legal_index())
    assert wrapped.T.static.flags.writeable is False
    assert wrapped.copy().T is wrapped.T


def test_frontier_problem_rejects_bad_start_before_scoring():
    instance = synthetic()
    compiled = compile_one_port_per_hatch(instance, {"A": "SGSIN", "B": "MYPKG"})
    calls = []
    problem = NS(engine=FakeEngine(instance), score=lambda actions: calls.append(actions) or "scored")
    constrain_frontier_problem(problem, compiled)
    assert problem.score([Place("x", "0030102")]) is None
    assert calls == []
    assert problem.score([Place("x", "0010102")]) == "scored"


def test_real_train_engine_smoke():
    from stowage.actions import get_action_space
    from stowage.feasibility import FeasibilityConfig, make_feasibility_engine
    from stowage.io import load_instance
    instance = load_instance("experiments/mvp/corpus/train/small/feeder_1700/random_yard/seed-031100000.json")
    ports = sorted({str(b.pod) for b in instance.containers if b.id in instance.load_list})
    cover_ports = {h.id: ports[i % len(ports)] for i, h in enumerate(instance.vessel.hatch_covers)}
    compiled = compile_one_port_per_hatch(instance, cover_ports)
    original = make_feasibility_engine(instance, get_action_space(), config=FeasibilityConfig(extra={"global_screen": 0}))
    snapshot = original.T.static.copy()
    wrapped = wrap_engine(original, compiled)
    plain = original.legal_index()
    filtered = wrapped.legal_index()
    assert set(filtered) <= set(plain)
    assert len(filtered) < len(plain)
    assert np.array_equal(original.T.static, snapshot)
    for flat in filtered:
        b, s = divmod(int(flat), len(original.index_slots))
        assert not compiled.forbids(original.index_boxes[b], original.index_slots[s])
