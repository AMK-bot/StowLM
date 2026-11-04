"""Native lane eligibility probes; CP models are inspected without solving."""
import random
from types import SimpleNamespace as NS

import numpy as np
import pytest

from experiments.mvp.pipeline.binding.hooks import binding_scope
from experiments.mvp.pipeline.binding.test_hooks import compiled_rule, fixture_instance
from experiments.mvp.reader.occupancy import apply_reader_constraint
from llm.instructions.schema import MaxTiers
from stowage.actions import get_action_space
from stowage.actions.space import Place
from stowage.feasibility import FeasibilityConfig


KINDS = ('close_bay', 'max_tiers', 'dg_keep_away', 'one_port_per_hatch')


def _rule(instance, kind):
    # Zero deck tiers binds on the initial bottom tier; a cap of one would only
    # bind after an additional supported tier appears in a later prefix.
    return (apply_reader_constraint(instance, MaxTiers(type='max_tiers', zone='deck', max_tiers=0))
            if kind == 'max_tiers' else compiled_rule(instance, kind))


@pytest.mark.parametrize('kind', KINDS)
def test_master_allowed_group_slots_exclude_every_forbidden_box(kind):
    import alns
    from master import MasterModel
    instance = fixture_instance()
    rule = _rule(instance, kind)
    baseline = MasterModel(alns.Problem(instance), keep_heavy_in_hold=False)
    assert any(rule.forbids(box, baseline.T.slot_ids[int(slot)])
               for group, slots in zip(baseline.groups, baseline.allowed)
               for box in group for slot in slots)
    with binding_scope(instance, rule):
        model = MasterModel(alns.Problem(instance), keep_heavy_in_hold=False)
        assert any(len(slots) for slots in model.allowed)
        assert all(not rule.forbids(box, model.T.slot_ids[int(slot)])
                   for group, slots in zip(model.groups, model.allowed)
                   for box in group for slot in slots)


@pytest.mark.parametrize('kind', KINDS)
def test_columns_variable_capacity_and_native_expand_obey_static_edges(monkeypatch, kind):
    import alns
    import columns
    instance = fixture_instance()
    rule = _rule(instance, kind)
    captured = []
    class StopBeforeNumericalSearch(Exception):
        pass
    class Solver:
        def __init__(self):
            self.parameters = NS()
        def Solve(self, model):
            captured.append(model.Proto())
            raise StopBeforeNumericalSearch()
    monkeypatch.setattr(columns.cp_model, 'CpSolver', Solver)
    with binding_scope(instance, rule):
        planner = columns.ColumnPlanner(alns.Problem(instance))
        with pytest.raises(StopBeforeNumericalSearch):
            planner.solve(time_limit_s=1., workers=1, seed=0)
        domains = {var.name: list(var.domain) for var in captured[0].variables
                   if var.name.startswith('n')}
        assert domains
        considered = 0
        for g, group in enumerate(planner.groups):
            rows = [planner.T.box_index[box] for box in group]
            length = 40 if int(planner.T.units[rows[0]]) == 2 else 20
            for c, key in enumerate(planner.columns):
                slots = planner.column_slots[key][length]
                allowed = [int(slot) for slot in slots
                           if bool(planner.T.static[rows, int(slot)].all())]
                name = f'n{g}_{c}'
                if allowed:
                    considered += 1
                    assert domains[name] == [0, min(len(allowed), len(group))]
                    assignment, missing = planner.expand({(g, c): 1})
                    assert missing == 0 and assignment
                    assert all(not rule.forbids(box, slot) for box, slot in assignment.items())
                else:
                    assert name not in domains
        assert considered > 0


@pytest.mark.parametrize('kind', KINDS)
def test_two_step_canonical_carrier_exact_domains_exclude_forbidden_slots(kind):
    from stowage.solver import twostep
    instance = fixture_instance()
    rule = _rule(instance, kind)
    canonical = twostep._canonical_instance(instance)
    space = get_action_space('boxslot')
    config = FeasibilityConfig(extra={'global_screen': 0, 'completion_screens': 0})
    plain = twostep.make_feasibility_engine(canonical, space, config=config)
    baseline = {box: set(plain.legal_slots_for_box(box)) for box in canonical.load_list}
    assert any(rule.forbids(box, slot) for box, slots in baseline.items() for slot in slots)
    with binding_scope(instance, rule):
        engine = twostep.make_feasibility_engine(canonical, space, config=config)
        domains = {box: set(engine.legal_slots_for_box(box)) for box in canonical.load_list}
        assert any(domains.values())
        assert all(slots <= baseline[box] for box, slots in domains.items())
        assert all(not rule.forbids(box, slot) for box, slots in domains.items() for slot in slots)
        assert all(not engine.is_legal(Place(box, slot)) for box, slots in baseline.items()
                   for slot in slots if rule.forbids(box, slot))


@pytest.mark.parametrize('kind', KINDS)
def test_alns_rebuilder_candidates_and_score_gate_both_enforce_instruction(monkeypatch, kind):
    import alns
    instance = fixture_instance()
    rule = _rule(instance, kind)
    plain = alns.Problem(instance)
    bad = next(Place(box, slot) for box in instance.load_list
               for slot in plain.engine.legal_slots_for_box(box) if rule.forbids(box, slot))
    assert plain.score((bad,)) is not None  # A real otherwise replayable counterexample.
    considered = []
    original = alns.Rebuilder._estimate
    def estimate(self, ex, placed, box, slot, noise):
        considered.append((box, slot))
        assert not rule.forbids(box, slot)
        return original(self, ex, placed, box, slot, noise)
    monkeypatch.setattr(alns.Rebuilder, '_estimate', estimate)
    with binding_scope(instance, rule) as audit:
        problem = alns.Problem(instance)
        result = alns.Rebuilder(problem, random.Random(0)).rebuild(
            [], [bad.box], noise=0., deadline=float('inf'))
        assert result and len(result) == 1 and considered
        assert not rule.forbids(result[0].box, result[0].slot)
        assert problem.score((bad,)) is None
        assert audit['scored_rejected'] == 1
