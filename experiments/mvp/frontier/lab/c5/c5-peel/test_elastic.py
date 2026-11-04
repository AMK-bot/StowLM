"""Small exact assignment matrices test preemptive goals without map tuning."""
from pathlib import Path
import sys
from types import SimpleNamespace
from time import monotonic

import numpy as np
import pytest
from scipy.sparse import csc_matrix

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from elastic import ElasticAssignment, Assignment
from tiers import load_table


def fixture_solver(monkeypatch, *, level=4, partial=False, nboxes=2,
                   pair=None, rows=None, rules=None, groups=None, extras=None, costs=None, bounds=None):
    pair = pair or [(b, b) for b in range(nboxes)]
    n, width = len(pair), len(pair)+nboxes+len(extras or {})
    rows = rows if rows is not None else [[2., 0., 0., 0., -1.]]
    rules = rules or ['iacs.member_irs.container_securing']
    groups = groups or ['securing']
    a = ElasticAssignment.__new__(ElasticAssignment)
    a.level, a.allow_partial, a.tested = level, bool(partial and level == 3), 6-level
    a.table, a.diagnostics, a._elastic_ready = load_table(), [], False
    a.n, a.pair, a._built = n, pair, None
    boxes = {f'b{b}': SimpleNamespace(id=f'b{b}', special_stowage=[], reefer=False) for b in range(nboxes)}
    a.T = SimpleNamespace(slot_cells=np.array([[q, -1] for q in range(n)]),
        box_ids=list(boxes), units=np.ones(nboxes), mass=np.arange(nboxes)*100.+100.,
        slot_deck=np.ones(n, dtype=bool))
    a.m = SimpleNamespace(boxes=list(range(nboxes)), T=a.T, pages=[],
        p=SimpleNamespace(boxes=boxes), ports=[1])
    a.slot_page = defaultdict_list = {q: [] for _, q in pair}
    E = np.zeros((nboxes, width))
    for v, (b, q) in enumerate(pair):
        E[b, v] = 1.
    for b in range(nboxes):
        E[b, n+b] = 1.
    c = np.zeros(width)
    if costs:
        c[:len(costs)] = costs
    for col in (extras or {}):
        c[col] = 1000.
    def native_build(self):
        self.row_rules = list(rules)
        self.row_info = [dict(rule_id=rule, group=group, proxy=True) for rule, group in zip(rules, groups)]
        self.extra_variables = dict(extras or {})
        self._built = (c.copy(), csc_matrix(rows),
            np.ones(len(rows)) if bounds is None else np.array(bounds, dtype=float),
            csc_matrix(E), np.ones(nboxes))
        return self._built
    monkeypatch.setattr(Assignment, 'build', native_build)
    return a


def test_l4_securing_is_elastic_but_original_overage_is_zero(monkeypatch):
    a = fixture_solver(monkeypatch, extras={4: {'group': 'securing'}})
    r = a.solve(time_limit=2.)
    assert r['x'] is not None
    assert r['uncovered'] == pytest.approx(0.)
    assert r['x'][4] == 0.
    assert r['x'][a._elastic_col[0]] == pytest.approx(1.)
    d = a.diagnostics[-1]
    assert d['row_slacks'][0]['rule_id'] == 'iacs.member_irs.container_securing'
    assert d['row_slacks'][0]['measured_proxy'] == pytest.approx(2.)
    assert d['row_slacks'][0]['bound'] == 1.
    assert d['row_slacks'][0]['proxy'] is True
    assert a.tail({}, seconds=2.) == {0: 0, 1: 1}


def test_l3_higher_securing_is_hard_and_partial_tail_omits_box(monkeypatch):
    a = fixture_solver(monkeypatch, level=3, partial=True, extras={4: {'group': 'securing'}})
    r = a.solve(time_limit=2.)
    assert r['x'] is not None and r['x'][0] <= .500001
    assert r['x'][4] == 0.
    assert [g['name'] for g in a.diagnostics[-1]['goals']][:3] == ['ashore_count', 'ashore_TEU', 'ashore_grams']
    assert a.tail({}, seconds=2.) == {1: 1}
    assert a.dive(deadline=monotonic()+2., tail_s=1.) == {1: 1}


def test_l5_drops_previously_peeled_securing(monkeypatch):
    a = fixture_solver(monkeypatch, level=5, extras={4: {'group': 'securing'}})
    r = a.solve(time_limit=2.)
    assert r['x'] is not None and r['x'][0] == pytest.approx(1.)
    assert a._built[1].shape[0] == 0
    assert 'iacs.member_irs.container_securing' in a.diagnostics[-1]['dropped_rules']


def test_unknown_t0_row_never_elastic_even_at_l5(monkeypatch):
    a = fixture_solver(monkeypatch, level=5, rows=[[2., 0., 0., 0.]],
                       rules=['unknown.new_rule'], groups=['unknown'])
    assert a.solve(time_limit=2.)['x'] is None
    assert a.tail({}, seconds=1.) is None


def test_restow_objective_native_slack_stays_soft(monkeypatch):
    a = fixture_solver(monkeypatch, rows=[[2., 0., 0., 0., -1.]],
        rules=['expert.rotation.no_overstow'], groups=['restow_objective'],
        extras={4: {'group': 'restow_objective'}})
    r = a.solve(time_limit=2.)
    assert r['x'] is not None and r['x'][4] == pytest.approx(1.)
    assert a.diagnostics[-1]['row_slacks'] == []


def test_partial_lex_count_then_teu_then_mass_beats_lift_cost(monkeypatch):
    a = fixture_solver(monkeypatch, level=3, partial=True,
        rows=[[1., 1., 0., 0.]], rules=['expert.physical.slot_occupancy'], groups=['cell'],
        costs=[10000., 0.])
    a.T.units[:] = [2., 1.]
    assert a.tail({}, seconds=2.) == {0: 0}
    a.T.units[:] = 1.
    assert a.tail({}, seconds=2.) == {1: 1}


def test_tested_breach_precedes_cost_in_integer_tail(monkeypatch):
    a = fixture_solver(monkeypatch, nboxes=1, pair=[(0, 0), (0, 1)],
        rows=[[2., 0., 0., -1.]], extras={3: {'group': 'securing'}}, costs=[0., 100.])
    assert a.tail({}, seconds=2.) == {0: 1}


def test_l0_solver_is_exact_original_delegation(monkeypatch):
    a = ElasticAssignment.__new__(ElasticAssignment)
    a.level = 0
    sentinel = object()
    monkeypatch.setattr(Assignment, 'solve', lambda self, *args: sentinel)
    assert a.solve(time_limit=2.) is sentinel


def test_large_moment_coefficient_cannot_round_elastic_slack_to_zero(monkeypatch):
    a = fixture_solver(monkeypatch, nboxes=1, pair=[(0, 0)],
        rows=[[50000., 0., -1.]], rules=['iscode.loading.trim'],
        groups=['moment_proxy'], extras={2: {'group': 'moment_proxy'}})
    assert a.tail({}, seconds=2.) == {0: 0}
    d = a.diagnostics[-1]['row_slacks'][0]
    assert d['rule_id'] == 'iscode.loading.trim'
    assert d['measured_proxy'] == pytest.approx(50000.)
    assert d['bound'] == 1.
    assert d['slack'] == pytest.approx(49999.)


def test_partial_moments_use_loaded_mass_not_full_load_rhs(monkeypatch):
    a = fixture_solver(monkeypatch, level=3, partial=True,
        rows=[[-2., -6., 0., 0., -1.], [0., 1., 0., 0., 0.]],
        rules=['iscode.loading.trim', 'expert.physical.slot_occupancy'],
        groups=['moment_proxy', 'cell'], extras={4: {'group': 'moment_proxy'}},
        bounds=[-4.5, 0.])
    # The second box cannot load. The first alone has LCG20 >= target15;
    # keeping the full-load moment RHS would incorrectly forbid it.
    r = a.solve(time_limit=2.)
    assert r['x'] is not None and r['x'][0] == pytest.approx(1., abs=1e-5)
    assert a._raw_b[0] == 0.
    assert a._raw_A[0, 0] == pytest.approx(-.5)
    assert a.tail({}, seconds=2.) == {0: 0}


def test_root_lp_infeasibility_skips_repeated_integer_tails(monkeypatch):
    a = fixture_solver(monkeypatch, extras={4: {'group': 'securing'}})
    a.solve = lambda *args, **kwargs: dict(x=None, status=2, s=0., uncovered=None)
    def forbidden(*args, **kwargs):
        raise AssertionError('a restricted LP proof must not launch a CP tail')
    a.tail = forbidden
    assert a.dive(deadline=monotonic()+2.) is None
    assert a.diagnostics[-1]['kind'] == 'restricted_model_exhaustion'
    assert 'not map infeasibility' in a.diagnostics[-1]['scope']
    assert a.restricted_infeasible
    assert len(a.restricted_proof['signature']) == 64


def test_root_lp_timeout_still_tries_integer_tail(monkeypatch):
    a = fixture_solver(monkeypatch, extras={4: {'group': 'securing'}})
    a.solve = lambda *args, **kwargs: dict(x=None, status=1, s=0., uncovered=None)
    calls = []
    a.tail = lambda *args, **kwargs: calls.append(kwargs) or {0: 0, 1: 1}
    assert a.dive(deadline=monotonic()+2.) == {0: 0, 1: 1}
    assert len(calls) == 1
