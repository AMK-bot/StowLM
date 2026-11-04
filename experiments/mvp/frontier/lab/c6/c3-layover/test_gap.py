"""Small real-map checks of the assignment LP and of the cold boundary."""
import ast
from pathlib import Path

import numpy as np
import pytest

from gap import Assignment, Settings
from stow import reference


HERE = Path(__file__).parent


class Page:
    deck, kind, slots, stacks = True, 'long', (0, 1, 2), (0,)
    cover, limit_kg, hmax = 'C', 90000., 10.


class Table:
    stack_base_z = np.array([12.5])


class Container:
    def __init__(self, mass, height=2.591):
        self.gross_mass_kg, self.height_m, self.length_ft, self.oog = mass, height, 40, None


class Hull:
    lpp_m, beam_m, depth_m, scantling_draught_m, bilge_keel = 124.41, 22.5, 11.3, 8.7, False


def solver():
    a = Assignment.__new__(Assignment)
    a.T = Table()
    a.T.box_ids = {0: 'a', 1: 'b', 2: 'c'}
    a.row_cache = {}
    a.motions = reference(Hull, 1.9)

    class Model:
        def _slot_xy(self, page, k):
            return 60., 5.
    a.m = Model()
    a.m.p = type('P', (), dict(boxes=dict(a=Container(20000.), b=Container(15000.), c=Container(8000.))))
    return a


def test_heavy_low_beats_heavy_high():
    a = solver()
    assert a.stack_ratio(Page, [0, 1, 2]) < a.stack_ratio(Page, [2, 1, 0])


def test_cached_rows_are_the_same_rows():
    a = solver()
    assert a.stack_ratio(Page, [0, 1, 2]) == a.stack_ratio(Page, [0, 1, 2])


def test_settings_default_keeps_every_pair():
    assert Settings().pairs_per_box == 0


def test_method_reads_no_stored_data():
    """Cold boundary: nothing in the runtime sources touches stored plans, results or the context's stored fields."""
    for name in ('method.py', 'gap.py', 'stow.py', 'rows.py', 'chainlab.py'):
        text = (HERE/name).read_text()
        ast.parse(text)
        for word in ("'stored", 'stored_plan', 'stored_lifts', "'baseline'", 'results/', 'json.loads'):
            assert word not in text, (name, word)


def prefix_solver(settings=None):
    """Two real-height boxes over two full physical tiers, with the arrival box immovable."""
    from types import SimpleNamespace
    class P:
        deck, kind, slots, stacks = False, 'long', (0, 1), (0,)
        cover, limit_kg, hmax = 'C', 90000., 10.
    T = SimpleNamespace(units=np.array([2, 2]), stack_base_z=np.array([12.5]), box_ids=['a', 'b'],
                        slot_cells=np.array([[0, 1], [2, 3]]), mass=np.array([20000., 8000.]),
                        share=np.array([20000., 8000.]), height=np.array([2.896, 2.591]),
                        rank=np.array([2, 1]), closes_stack=np.array([False, False]), e2_tol=0,
                        e1_threshold=None, is_dg=np.array([False, False]))
    m = SimpleNamespace(T=T, boxes=[0, 1], pages=[P()], fixed_pairs={0: 0},
                        static=np.ones((2, 2), bool), ports=[1, 2], pod={0: 2, 1: 1},
                        heavy={0: True, 1: False})
    m.p = SimpleNamespace(boxes=dict(a=Container(20000., 2.896), b=Container(8000.)))
    m._slot_xy = lambda page, k: (60., 5.)
    m.legal = lambda page, boxes, any_port=False: len(boxes) <= 2 and (not boxes or boxes[0] == 0)
    return Assignment(m, reference(Hull, 1.9), settings)


def test_fixed_pair_is_unique_and_prefix_heights_are_physical():
    a = prefix_solver()
    assert [pair for pair in a.pair if pair[0] == 0] == [(0, 0)]
    assert a.prefix_length(0) == 1
    assert a.preserve_prefix(0, [1, 0]) == [0, 1]
    assert a.slot_cg_z(1, 1) == pytest.approx(12.5+2.896+2.591/2)


def test_fixed_bounds_keep_box_and_forbid_uncovered():
    a = prefix_solver()
    c = a.build()[0]
    lower, upper = a._fixed_bounds(np.zeros(len(c)), np.full(len(c), np.inf))
    assert lower[a.pair_index[(0, 0)]] == upper[a.pair_index[(0, 0)]] == 1.
    assert upper[a.n+a.box_pos[0]] == 0.


def test_lp_and_integer_tail_keep_arrival_and_include_its_moments():
    a = prefix_solver(Settings(lcg=(59., 61.), tcg=6.))
    result = a.solve()
    assert result['status'] == 0
    assert result['uncovered'] == pytest.approx(0., abs=1e-7)
    assert result['x'][a.pair_index[(0, 0)]] == pytest.approx(1.)
    assert a.tail({}) == {0: 0, 1: 1}
    with pytest.raises(ValueError, match='cannot move'):
        a.tail({0: 1})
    moments = {name: (coefficient, rhs) for name, coefficient, rhs in a._moments()}
    coeff, bound = moments['lcg_max']
    assert dict(coeff)[a.pair_index[(0, 0)]] == pytest.approx(20*60.)
    assert bound == pytest.approx(61*28.)


def test_background_moment_is_offset_once():
    a = prefix_solver(Settings(lcg=(59., 61.), tcg=6., vertical_max_tm=1000.,
                              background_mass_t=10., background_moments_tm=(500., 20., 70.)))
    rows = {name: rhs for name, _, rhs in a._moments()}
    assert rows['lcg_max'] == pytest.approx(61*38.-500.)
    assert rows['lcg_min'] == pytest.approx(-59*38.+500.)
    assert rows['tcg_max'] == pytest.approx(6*38.-20.)
    assert rows['tcg_min'] == pytest.approx(6*38.+20.)
    assert rows['vertical'] == 930.


def test_work_quota_stops_before_second_lp():
    a = prefix_solver(Settings(max_lp_calls=0))
    assert a.solve()['work_exhausted'] == 'lp_calls'
    assert a.work['lp_calls'] == 0


def test_expired_deadline_does_not_stop_count_bounded_dive():
    a = prefix_solver(Settings(max_lp_calls=3, max_dive_steps=3, max_tail_calls=2))
    assert a.dive(deadline=-1.) == {0: 0, 1: 1}
    assert a.work['lp_calls'] <= 3
    assert a.work['tail_calls'] <= 2


def test_large_soft_moment_remains_soft_in_integer_tail():
    # Every full assignment violates LCG=0; original scale*.001 slack rounded
    # to zero. A legal complete integer assignment must still exist with slack.
    a = prefix_solver(Settings(lcg=(0., 0.), moment_weight=.1))
    lp = a.solve()
    assert lp['status'] == 0 and lp['uncovered'] < 1e-7
    log = []
    assert a.tail({}, log=log) == {0: 0, 1: 1}
    assert any(q > .001 for q in log[-1]['slack_quanta'].values())
    assert log[-1]['objective_lifts'] >= 168.  # 28t at 60m, weighted .1


def test_constant_soft_target_is_priced_and_does_not_reject_fixed_point():
    a = prefix_solver(Settings(lcg=(0., 0.), moment_weight=.1))
    a.build()
    log = []
    assert a.tail({0: 0, 1: 1}, log=log) == {0: 0, 1: 1}
    assert log[-1]['slack_quanta']
    assert log[-1]['objective_lifts'] >= 168.


def test_soft_moment_does_not_relax_hard_stack_capacity():
    a = prefix_solver(Settings(lcg=(0., 0.), moment_weight=.1))
    a.m.pages[0].limit_kg = 27000.  # 20t fixed + 8t new cannot fit
    a.build()
    assert a.tail({}) is None
    assert a.tail({0: 0, 1: 1}) is None


def test_dive_does_not_retry_identical_tail_after_history_exhaustion():
    a = prefix_solver(Settings(max_tail_calls=6))
    calls = []
    a.tail = lambda fixed, **kwargs: calls.append(dict(fixed)) or None
    assert a.dive(deadline=-1.) is None
    assert calls == [{0: 0}]


def simple_joint(solver):
    # Full cargo is 28t. A bounded physical tank must add >=12t; draft must
    # cover their displacement. Every pair contributes its true box mass.
    mass = np.array([solver.T.mass[b]/1000. for b, q in solver.pair])
    return dict(A_pair=np.array([-mass, mass, np.zeros(len(mass))]),
                A_aux=np.array([[-20., 0.], [20., -10.], [1., 0.]]),
                b=np.array([-40., 0., .9]), names=['minimum mass', 'buoyancy', 'tank-only cap'],
                bounds=[(0., 1.), (0., 6.)], quantum=[1e-4, 1e-3], cost=[.01, .001])


@pytest.mark.parametrize('tail', ['milp', 'cp'])
def test_joint_tail_keeps_prefix_and_recovers_physical_tank_and_draft(tail):
    a = prefix_solver(Settings(joint_loading=simple_joint, joint_tail_solver=tail))
    solved = a.solve()
    assert solved['uncovered'] < 1e-7
    assert a.tail({}) == {0: 0, 1: 1}
    fraction, draft = a.last_joint_solution
    assert .6-1e-7 <= fraction <= .9+1e-7
    assert draft >= (28.+20.*fraction)/10.-1e-7
    assert draft <= 6.


@pytest.mark.parametrize('tail', ['milp', 'cp'])
def test_auxiliary_only_hard_row_is_enforced(tail):
    def impossible(solver):
        spec = simple_joint(solver)
        spec['b'][-1] = .4  # minimum displacement needs .6 tank fill
        return spec
    a = prefix_solver(Settings(joint_loading=impossible, joint_tail_solver=tail))
    a.build()
    assert a.tail({}) is None


def test_joint_milp_continuous_auxiliary_satisfies_exact_equality():
    def exact(solver):
        spec = simple_joint(solver)
        # Fraction must be 1/3, which is unavailable on the CP 1e-4 grid.
        # MILP retains its exact continuous domain.
        spec['A_pair'] = np.vstack([np.zeros(solver.n), np.zeros(solver.n)])
        spec['A_aux'] = np.array([[1., 0.], [-1., 0.]])
        spec['b'] = np.array([1./3., -1./3.])
        return spec
    a = prefix_solver(Settings(joint_loading=exact))
    a.build()
    assert a.tail({}) == {0: 0, 1: 1}
    assert a.last_joint_solution[0] == pytest.approx(1./3., abs=1e-9)


@pytest.mark.parametrize('tail', ['milp', 'cp'])
def test_pressed_empty_physical_tank_integrality(tail):
    def pressed(solver):
        spec = simple_joint(solver)
        spec['b'][-1] = 1.
        spec['aux_integrality'] = [1, 0]
        return spec
    a = prefix_solver(Settings(joint_loading=pressed, joint_tail_solver=tail))
    a.build()
    assert a.tail({}) == {0: 0, 1: 1}
    assert a.last_joint_solution[0] == pytest.approx(1.)
    assert a.last_joint_solution[1] >= 4.8-1e-6


def test_pressed_tank_cannot_use_fractional_fill_to_evade_cap():
    def pressed(solver):
        spec = simple_joint(solver)
        spec['aux_integrality'] = [1, 0]
        return spec
    a = prefix_solver(Settings(joint_loading=pressed))
    assert a.solve()['uncovered'] < 1e-7  # continuous relaxation still feasible
    assert a.tail({}) is None  # actual binary tank must satisfy .6 <= fill <= .9


def test_joint_root_lp_infeasibility_skips_integer_tail():
    def impossible(solver):
        spec = simple_joint(solver)
        spec['b'][-1] = .4
        return spec
    a = prefix_solver(Settings(joint_loading=impossible))
    def must_not_call(*args, **kwargs):
        raise AssertionError('integer tail cannot rescue an infeasible complete LP relaxation')
    a.tail = must_not_call
    log = []
    assert a.dive(deadline=-1., log=log) is None
    assert a.work['lp_calls'] == 1
    assert a.work['tail_calls'] == 0
    assert log[-1]['local_joint_lp_infeasible'] is True
    assert log[-1]['map_infeasibility_proven'] is False


def test_joint_dive_never_buys_uncovered_cargo():
    # LP objective would gladly leave the new box behind if that could satisfy
    # a displacement cap. Complete joint construction must return LP status2.
    def cap(solver):
        mass = np.array([solver.T.mass[b] for b, q in solver.pair])
        return dict(A_pair=mass[None,:], A_aux=np.zeros((1,1)), b=np.array([21000.]),
                    names=['mass cap'], bounds=[(0.,1.)], quantum=[1e-4], cost=[0.])
    a = prefix_solver(Settings(joint_loading=cap))
    result = a.solve()
    assert result['status'] == 2 and result['x'] is None
    c = a._built[0]
    _, upper = a._fixed_bounds(np.zeros(len(c)), np.full(len(c), np.inf))
    assert np.array_equal(upper[a.n:a.n+len(a.m.boxes)], [0.,0.])


def test_unknown_joint_root_lp_still_attempts_tail():
    a = prefix_solver(Settings(joint_loading=simple_joint))
    a.build()
    a.solve = lambda *args, **kwargs: dict(status=1,x=None,uncovered=None,s=0.,fun=None)
    calls = []
    a.tail = lambda fixed, **kwargs: calls.append(dict(fixed)) or {0:0,1:1}
    assert a.dive(deadline=-1.) == {0:0,1:1}
    assert calls == [{0:0}]


@pytest.mark.parametrize('joint',[True,False])
def test_final_joint_tail_releases_all_greedy_fixings_but_keeps_arrival(joint):
    """Four bounded tails cannot otherwise undo five earlier fixing batches."""
    from types import SimpleNamespace as NS
    a=prefix_solver()
    m,T=a.m,a.T
    m.boxes=list(range(6))
    T.box_ids=[str(b) for b in m.boxes]
    T.slot_cells=np.arange(12).reshape(6,2)
    T.units=np.full(6,2);T.mass=np.array([20000.]+[8000.]*5)
    T.share=T.mass.copy();T.height=np.full(6,2.591)
    T.rank=np.array([2]+[1]*5);T.closes_stack=np.zeros(6,bool);T.is_dg=np.zeros(6,bool)
    m.p.boxes={str(b):Container(float(T.mass[b])) for b in m.boxes}
    m.static=np.ones((6,6),bool);m.pod={b:1 for b in m.boxes};m.ports=[1]
    m.heavy={b:False for b in m.boxes}
    m.pages=[NS(deck=False,kind='long',slots=tuple(range(6)),stacks=(0,),
                cover='C',limit_kg=100000.,hmax=30.)]
    m.legal=lambda *args,**kwargs:True
    settings=Settings(max_dive_steps=5,max_tail_calls=4,
                      joint_loading=simple_joint if joint else None)
    a=Assignment(m,a.motions,settings)
    c=a.build()[0]
    x=np.zeros(len(c))
    for b in m.boxes:x[a.pair_index[(b,b)]]=1.
    a.solve=lambda *args,**kwargs:dict(status=0,x=x,uncovered=0.,s=0.,fun=0.)
    # One box per batch deliberately leaves five independent fixing batches.
    a.column_from=lambda x,j,used,have:[len(have)]
    calls=[]
    a.tail=lambda fixed,**kwargs:calls.append(dict(fixed)) or None
    assert a.dive(deadline=-1.,tail_left=0) is None
    assert len(calls)==4
    assert calls[0]=={b:b for b in range(6)}
    assert calls[-1]==({0:0} if joint else {0:0,1:1,2:2})
    assert all(call[0]==0 for call in calls)
