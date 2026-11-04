# Provenance: copied from lab/c4/c2-layout/test_moments.py.
"""Small exact examples exercise moment coordinates, dual signs and fixed RHS."""
from collections import defaultdict
from pathlib import Path
from types import SimpleNamespace as NS
import sys

import numpy as np
import pytest

from .moment_pages import Model, Page, Pattern, BaseModel


def example(*, geometry='stability'):
    m = object.__new__(Model)
    m.pages = [Page('20', (0,), (0, 1), 60000., 20., False, 'A'),
               Page('20', (1,), (2, 3), 60000., 20., True, 'A')]
    m.p = NS(cells={'c0': NS(lcg_m=20., tcg_m=-2.), 'c1': NS(lcg_m=20., tcg_m=-2.),
                    'c2': NS(lcg_m=60., tcg_m=2.), 'c3': NS(lcg_m=60., tcg_m=2.)},
             boxes={'a': NS(height_m=2.591, oog=None), 'b': NS(height_m=2.896, oog=None)})
    m.T = NS(stack_base_z=np.array([1., 10.]), mass=np.array([10000., 20000.]),
             height=np.array([2.591, 2.896]), units=np.ones(2), box_ids=['a', 'b'],
             cell_ids=['c0', 'c1', 'c2', 'c3'], slot_cells=np.array([[0], [1], [2], [3]]))
    m.boxes, m.stacks = [0, 1], [0, 1]
    m.columns = [Pattern(0, (0, 1), 0.), Pattern(1, (0, 1), 0.)]
    m.geometry_cache = {}
    m.moment_geometry = geometry
    m.cargo_vmoment_max = None
    m.lcg_min = m.lcg_max = m.tcg_min = m.tcg_max = None
    m.lcg_target = None
    m.fixed_columns = []
    m.row, m.stack_row = {0: 0, 1: 1}, {0: 0, 1: 1}
    m.master_matrix = None
    m.master_matrix_size = 0
    m.cost_cache, m.banned = {}, set()
    m.moment_penalty = 10.
    m.vertical_weight = .02
    m.hold_reward = 1.
    m.heavy = {0: False, 1: False}
    m.closed_hold = set()
    m.stack_cost = .02
    m.moment_duals = {}
    m.type_of = {0: ('low',), 1: ('high',)}
    m.order = [0, 1]
    m.oracle = None
    m.factor = defaultdict(lambda: 1.)
    m.pod = {0: 1, 1: 1}
    m.ports = [1]
    return m


def test_true_halfheight_and_accumulated_highcube_moment():
    m = example()
    mass, vertical, longitudinal, transverse = m.column_moments(m.columns[0])
    assert mass == 30.
    assert vertical == pytest.approx(10*(1+2.591/2)+20*(1+2.591+2.896/2))
    assert longitudinal == 600.
    assert transverse == -60.
    # The new objective is linear in vertical moment, not mass times height².
    assert m._column_cost(m.columns[0], 3) == pytest.approx(.02*vertical-2.)


def test_stability_and_expanded_envelope_cg_are_explicitly_distinct():
    m = example()
    m.p.boxes['a'].oog = NS(over_length_fore_m=2., over_length_aft_m=0.,
                             over_width_port_m=1., over_width_starboard_m=0.)
    m.T.height[0] += 1.
    body = m.column_moments(m.columns[0])
    assert body[2:] == (600., -60.)
    m.moment_geometry = 'envelope'; m.geometry_cache = {}
    envelope = m.column_moments(m.columns[0])
    assert envelope[1]-body[1] == pytest.approx(25.)  # first CG +0.5 m; second +1 m
    assert envelope[2]-body[2] == 10.
    assert envelope[3]-body[3] == 5.


def test_long_box_mass_is_not_doubled_and_xy_averages_support_cells():
    m = example()
    m.pages[0] = Page('long', (0, 1), (0,), 60000., 20., False, 'A')
    m.T.slot_cells = np.array([[0, 2], [1, -1], [2, -1], [3, -1]])
    g = m.column_moments(Pattern(0, (0,), 0.))
    assert g == pytest.approx((10., 10*(10+2.591/2), 400., 0.))


def test_moment_rows_reduce_fixed_contributions_and_preserve_signed_limits():
    m = example()
    m.lcg_min, m.lcg_max = 30., 50.
    m.tcg_min, m.tcg_max = -1., 1.
    m.cargo_vmoment_max = 500.
    rows = {name: (coeff, rhs) for name, coeff, rhs in m.moment_rows(fixed_columns=[0], columns=[0, 1])}
    assert rows['lcg_max'] == ({1: 300.}, 900.)
    assert rows['lcg_min'] == ({1: -900.}, -300.)
    assert rows['tcg_max'] == ({1: 30.}, 90.)
    assert rows['tcg_min'] == ({1: -90.}, -30.)
    assert rows['vertical_max'][1] == pytest.approx(500-m.column_moments(m.columns[0])[1])
    assert all(set(coeff) == {1} for coeff, _ in rows.values())


def test_complete_seed_cannot_bypass_moment_target(monkeypatch):
    m = example()
    m.cargo_vmoment_max = 100.
    monkeypatch.setattr(BaseModel, 'first_fit', lambda *args, **kwargs: [0])
    assert m.first_fit(set(m.boxes), set(m.stacks)) is None
    m.cargo_vmoment_max = 1000.
    assert m.first_fit(set(m.boxes), set(m.stacks)) == [0]


def test_master_vertical_target_creates_correct_dual_and_low_stack_selection():
    m = example()
    low = m.column_moments(m.columns[0])[1]
    m.cargo_vmoment_max = low
    # Make the high page superficially cheaper by using the master's phase-2
    # lift objective. A binding physical row must still price its height.
    m.columns = [Pattern(0, (0, 1), 2.), Pattern(1, (0, 1), 0.)]
    _, x, _, _, uncovered = m.solve_lp(active_boxes=set(m.boxes), active_stacks=set(m.stacks),
                                      hold_full=False, phase=2)
    assert x[0] == pytest.approx(1.)
    assert uncovered < 1e-7
    assert m.moment_duals['vertical_max'] < 0.
    assert m.moment_slacks['vertical_max'] < 1e-7


def test_master_soft_guards_keep_complete_columns_when_target_impossible():
    m = example()
    m.columns = [m.columns[0]]
    m.cargo_vmoment_max = 100.
    _, x, _, _, short = m.solve_lp(active_boxes=set(m.boxes), active_stacks=set(m.stacks),
                                  hold_full=False, phase=1)
    assert x[0] == pytest.approx(1.)
    assert m.actual_uncovered < 1e-7
    assert short == pytest.approx(m.column_moments(m.columns[0])[1]-100.)
    assert m.moment_slacks['vertical_max'] > 0.


def test_priced_gain_matches_whole_column_objective_and_dual_rows():
    m = example()
    m.lcg_max, m.lcg_target = 30., 30.
    m.cargo_vmoment_max = 200.
    m.tcg_min, m.tcg_max = -1., 1.
    m.moment_duals = {'lcg_max': -.2, 'vertical_max': -.5, 'tcg_min': -.1, 'tcg_max': -.3}
    m._pricing = lambda page_index, height: (2, {
        b: (int(m.T.height[b] > 2.7), (1, 1), ((1, np.array([0, 1])),)) for b in m.boxes})
    values = np.array([300., 400.])
    for page_index in (0, 1):
        priced = m.price(page_index, values, phase=3)
        for _, (gain, boxes) in priced.items():
            c = Pattern(page_index, boxes, 0.)
            g = m.column_moments(c)
            expected = sum(values[b] for b in boxes)-m.vertical_weight*g[1]
            if not m.pages[page_index].deck:
                expected += sum(m.hold_gain(b) for b in boxes)
            for name, component, sign, mass_coeff, _ in m._target_specs():
                expected += m.moment_duals.get(name, 0.)*(sign*g[component]+mass_coeff*g[0])
            assert gain == pytest.approx(expected)


def test_engine_moment_model_initialization_and_counted_pricing_on_real_fixture():
    from time import monotonic
    from alns import Problem
    from stowage.model.fixtures import build_instance
    from .advanced_pages import Work
    model = Model(Problem(build_instance()), work=Work(lp_rounds=2, polish_rounds=1, dive_nodes=1),
                  safety_deadline=monotonic()+30., cargo_vmoment_max=0., restricted_master=False)
    root = model.generate(active_boxes=set(model.boxes), active_stacks=set(model.stacks),
                          phase=1, deadline=monotonic()+30.)
    assert root['rounds'] <= 2
    assert root['uncovered'] == model.actual_uncovered
    assert root['coverage_and_target_slack'] >= root['uncovered']
    assert root['moment_slacks']['vertical_max'] >= 0.
