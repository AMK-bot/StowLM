# Provenance: copied from experiments/mvp/frontier/lab/c3/c2-layout/test_lns_lp.py.
"""Synthetic fixed-charge LP tests; no map input or checker modifications."""
import importlib.util
from pathlib import Path
import sys


from . import lns_lp as lns
from .test_c3_lns import Pool


def test_fixed_charge_lp_matches_integral_hold_charge_and_hint():
    m = Pool([1, 2], [('A', False, [0], 2)], [(0, [1, 0])])
    lp = lns.fixed_charge_lp(m, [0], fixed=[0], seconds=2)
    assert lp['status'] == 'OPTIMAL'
    assert lp['objective'] == 6
    assert lp['x'] == {0: 1.}
    result = lns.improve(m, [0], [], pricing_s=0, seconds=2)
    assert result['fixed_charge_lp_objective_restricted_pool'] == 6
    assert result['objective_lifts'] == 6
    assert result['ip_hint_source'] == 'fixed_charge_lp'


def test_fixed_charge_lp_deduplicates_internal_and_cover_restow():
    m = Pool([1, 1, 2], [('A', False, [0], 1), ('A', True, [1], 2)],
             [(0, [0]), (1, [1, 2])])
    lp = lns.fixed_charge_lp(m, [0, 1], fixed=[0, 1], seconds=2)
    assert lp['status'] == 'OPTIMAL' and lp['objective'] == 6


def test_fractional_fixed_charge_lp_is_a_relaxation_not_ip_solution():
    # Every box is in two of three pair patterns. Only x=.5 for each covers
    # all boxes; there is no integer exact cover. A cover costs two LP lifts.
    m = Pool([1, 1, 1], [('A', False, [0], 2), ('A', False, [1], 2),
                         ('A', False, [2], 2)],
             [(0, [0, 1]), (1, [1, 2]), (2, [0, 2])])
    lp = lns.fixed_charge_lp(m, [0, 1, 2], seconds=2)
    assert lp['status'] == 'OPTIMAL'
    assert lp['objective'] == 2
    assert lp['x'] == {0: .5, 1: .5, 2: .5}


def test_lp_fixed_column_hold_only_and_nogoods_stay_enforced():
    m = Pool([1], [('A', True, [0], 1)], [(0, [0])])
    assert lns.fixed_charge_lp(m, [0], fixed=[0], hold_only=[0], seconds=2)['status'] == 'INFEASIBLE'
    assert lns.fixed_charge_lp(m, [0], forbidden_pairs=[(0, 0)], seconds=2)['status'] == 'INFEASIBLE'
    assert lns.fixed_charge_lp(m, [0], seconds=0)['status'] == 'DEADLINE'
    assert lns.fixed_charge_lp(m, [], seconds=2)['status'] == 'UNCOVERED_POOL'
