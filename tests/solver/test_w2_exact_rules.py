"""Activated A* objective preferences preserve the shared physical action domain."""
from fractions import Fraction
from time import monotonic

import pytest

from stowage.actions.expander import Expander
from stowage.actions.space import Place
from stowage.model import BallastCondition, Instance
from stowage.model.fixtures import build_instance
from stowage.moves import initial_state
from stowage.rules.catalogue import rule_freeze_v1


def one_box(*, mass=25000., oog=None):
    data = build_instance().model_dump()
    box = data['containers'][1]
    box.update(gross_mass_kg=mass, dg=None, special_stowage=[], oog=oog)
    data.update(containers=[box], load_list=[box['id']], discharge_list=[])
    data['yard']['stacks'] = [dict(key='A-001-01', length_ft=20, boxes=[box['id']])]
    return Instance.model_validate(data)


class TwoChoiceEngine:
    """Bounded public-engine double; all offered actions still replay physically."""
    def __init__(self, instance, choices):
        self.instance, self.choices = instance, choices
        self.complete = False
    def copy(self):
        other = TwoChoiceEngine(self.instance, self.choices)
        other.complete = self.complete
        return other
    def is_complete(self):
        return self.complete
    def legal_actions(self):
        return [] if self.complete else [Place(self.instance.load_list[0], slot) for slot in self.choices]
    def push(self, action):
        assert action.slot in self.choices and not self.complete
        self.complete = True
    def dead_end(self):
        return None


def run_choices(monkeypatch, instance, choices, enabled):
    import stowage.solver.exact as exact
    import stowage.solver.exact_engine as engine
    monkeypatch.setattr(engine, 'make_feasibility_engine',
                        lambda inst, space: TwoChoiceEngine(inst, choices))
    monkeypatch.setattr(exact, '_validate_terminal', lambda inst, plan, ship, **kw:
                        ('yes', BallastCondition(source='planner', tank_masses_kg={}), plan.provenance))
    with rule_freeze_v1(enabled=enabled):
        return engine.solve_engine(instance, time_limit_s=3., max_nodes=20,
                                   seed=0, num_workers=1)


@pytest.mark.parametrize('enabled', [False, True])
def test_equal_move_terminals_compare_heavy_count_only_when_activated(monkeypatch, enabled):
    instance = one_box()
    instance.objective.hatch_cover = instance.objective.future_hatch_cover = 0.
    result = run_choices(monkeypatch, instance, ('0010182', '0010102'), enabled)
    assert result.plan is not None
    assert result.plan.moves[0].kind == ('hatch_cover_off' if enabled else 'load')


def test_moves_precede_heavy_on_deck_preference(monkeypatch):
    result = run_choices(monkeypatch, one_box(), ('0010182', '0010102'), True)
    assert result.plan is not None
    assert [move.kind for move in result.plan.moves] == ['load']


@pytest.mark.parametrize('enabled', [False, True])
def test_equal_move_terminals_compare_shared_killed_slot_metric(monkeypatch, enabled):
    instance = one_box(mass=10000., oog={'over_height_m': .2})
    stack = next(s for s in instance.vessel.stacks if s.id == '00102D')
    removed = stack.cell_ids.pop()
    instance.vessel.cells = [c for c in instance.vessel.cells if c.id != removed]
    result = run_choices(monkeypatch, instance, ('0010182', '0010282'), enabled)
    assert result.plan is not None
    assert result.plan.moves[-1].dst == ('0010282' if enabled else '0010182')


@pytest.mark.parametrize('side,expected', [('port', '0010182'), ('starboard', '0010282')])
def test_seed_sequences_far_rows_before_near_rows(monkeypatch, side, expected):
    from stowage.solver.exact_engine import _greedy_seed
    instance = one_box(mass=10000.)
    instance.port.berthing_side = side
    instance.port.max_crane_height_tiers = 2
    choices = ('0010182', '0010282')
    with rule_freeze_v1(enabled=True):
        result = _greedy_seed(instance, TwoChoiceEngine(instance, choices), Expander(instance),
                              initial_state(instance), deadline=monotonic()+2.,
                              weights={'load': Fraction(0)})
    assert result is not None
    assert result[-1][0].slot == expected


def test_real_engine_exact_uses_kg_order_and_accepts_unavoidable_heavy_deck(monkeypatch):
    """Local-rule integration only; no hull/ballast physics certificate is claimed."""
    import stowage.solver.exact as exact
    from stowage.checker.rules import check_static_rules
    from stowage.moves import apply_plan
    from stowage.solver.exact_engine import solve_engine
    data = one_box(mass=21000.).model_dump()
    first = data['containers'][0]
    second = {**first, 'id': build_instance().containers[2].id, 'gross_mass_kg': 22000.}
    data['containers'].append(second)
    data['load_list'].append(second['id'])
    data['yard']['stacks'].append(dict(key='A-002-01', length_ft=20, boxes=[second['id']]))
    kept = next(s for s in data['vessel']['stacks'] if s['id'] == '00101D')
    kept['hatch_cover_id'] = None
    data['vessel'].update(stacks=[kept], hatch_covers=[], hull=None)
    data['vessel']['cells'] = [c for c in data['vessel']['cells'] if c['id'] in kept['cell_ids']]
    instance = Instance.model_validate(data)
    def terminal(inst, plan, ship, **kw):
        part = check_static_rules(inst, ship, call_index=inst.call_index)
        assert not [v for v in part.violations if v.severity == 'hard']
        return 'yes', BallastCondition(source='planner', tank_masses_kg={}), plan.provenance
    monkeypatch.setattr(exact, '_validate_terminal', terminal)
    with rule_freeze_v1(enabled=True):
        result = solve_engine(instance, time_limit_s=3., max_nodes=100, seed=0, num_workers=1)
        assert result.plan is not None
        replay = apply_plan(instance, result.plan)
        assert replay.legal and replay.count.total_lifts_now == 2
        assert [(p.slot, p.container_id) for p in replay.final_state.ship.placements] == [
            ('0010182', second['id']), ('0010184', first['id'])]
        assert result.metrics['heavy_on_deck_count'] == 2


@pytest.mark.parametrize('enabled', [False, True])
def test_seed_real_engine_loads_direct_delivery_from_truck_without_yard_source(enabled):
    """An activated declared port policy derives direct delivery for this DG box."""
    from stowage.actions import get_action_space
    from stowage.feasibility import make_feasibility_engine
    from stowage.moves import apply_plan
    from stowage.solver.exact_engine import _greedy_seed

    data = one_box(mass=10000.).model_dump()
    box = data['containers'][0]
    box.update(dg=build_instance().containers[1].dg.model_dump(), direct_delivery=True)
    # Synthetic port policy uses the fixture's fully documented class-3 box;
    # this test concerns truck availability, not class-1 IMDG qualification.
    data['port']['port_rules'] = [dict(rule_id='port.dg.direct_delivery', params={'classes': ['3']})]
    data['yard']['stacks'] = []
    data['vessel'] = build_instance(with_dg_doc=True).model_dump()['vessel']
    data['vessel']['hull'] = None
    data['vessel']['fixed_loads'] = []
    from stowage.ports import load_port
    destination = load_port('small_feeder')
    data['rotation']['calls'] = data['rotation']['calls'][:2]
    data['rotation']['calls'][1].update(locode=destination.locode, archetype='small_feeder')
    box['pod'] = destination.locode
    with rule_freeze_v1(enabled=enabled):
        instance = Instance.model_validate(data)
        space = get_action_space()
        engine = make_feasibility_engine(instance, space)
        action = Place(box['id'], '0010182')
        assert engine.is_legal(action) is enabled, (engine.screen_diagnostics(), engine.dead_end())
        ex = Expander(instance)
        assert box['id'] not in ex.where
        result = _greedy_seed(instance, engine, ex, initial_state(instance),
                              deadline=monotonic()+3., weights={'load': Fraction(0)})
        if not enabled:
            # Legacy indexes physically located manifest cargo only; retain its
            # empty seed for this newly introduced, unlocated truck export.
            assert result is not None and result[-1] == ()
            return
        assert result is not None
        assert result[-1] == (action,)
        plan = space.expand(instance, result[-1])
        replay = apply_plan(instance, plan)
        assert replay.legal and replay.count.loads == replay.count.total_lifts_now == 1
        assert replay.count.relocations == 0
        assert plan.moves[0].src == 'truck'
