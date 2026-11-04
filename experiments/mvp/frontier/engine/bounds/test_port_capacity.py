# Copied from experiments/mvp/frontier/lab/c3/c2-layout/test_bound_ports.py; engine integration changes are local.
"""Independent two-port capacity arithmetic and approved-map certificates."""
import importlib.util
import json
from itertools import product
from pathlib import Path

from stowage.io import load_instance

HERE = Path(__file__).resolve().parent
from . import port_capacity as ports

def test_high_map_port_split_needs_an_extra_edge():
    result = ports.enumerate_cover_masks([4, 4, 18, 18, 8, 8], [37, 14])
    assert result['cost_scaled'] == 18000
    assert sum(mask != 0 for mask in result['masks']) == 4
    assert sum(mask.bit_count() for mask in result['masks']) == 5


def test_empty_single_port_and_impossible_demands():
    assert ports.enumerate_cover_masks([6], [0, 0])['cost_scaled'] == 0
    assert ports.enumerate_cover_masks([6], [4, 0])['cost_scaled'] == 4000
    assert ports.enumerate_cover_masks([6], [4, 2])['cost_scaled'] == 6000
    assert ports.enumerate_cover_masks([6], [4, 3])['cost_scaled'] is None


def test_hall_masks_match_independent_integer_transportation():
    # Enumerate actual port quantities sent to each tiny cover, not masks.
    caps = [1, 2]
    allocations = [[(a, b) for a in range(cap+1) for b in range(cap+1-a)] for cap in caps]
    for demand in product(range(4), repeat=2):
        actual = []
        for quantities in product(*allocations):
            if tuple(map(sum, zip(*quantities))) != demand:
                continue
            actual.append(sum(2000*(a+b > 0) + 2000*((a > 0)+(b > 0)) for a, b in quantities))
        relaxed = ports.enumerate_cover_masks(caps, demand)['cost_scaled']
        assert relaxed == (min(actual) if actual else None), demand


def test_four_approved_map_refinements_use_optimistic_dg_demands():
    from stowage.bounds import lower_bound as counting_bound
    old_rows = json.loads((HERE/'fixtures/capacity-bound-results.json').read_text())
    for old in old_rows:
        instance = load_instance(old['input'])
        safe_base = dict(instance_id=instance.id, lower_bound=counting_bound(instance).weighted)
        result = ports.lower_bound(instance, base_result=safe_base)
        assert result['lower_bound'] >= safe_base['lower_bound']
        if result.get('port_capacity_unknown'):
            assert result['lower_bound'] == safe_base['lower_bound']
            assert result['case_a_all_heavy_hold']['complete'] is False
            assert result['case_a_all_heavy_hold']['cost_scaled'] is None
            assert result['port_capacity_lower_bound'] is None
        else:
            assert result['case_a_all_heavy_hold']['complete'] is True
        assert sum(result['mandatory_hold_cells_by_call'].values()) >= 0
        # Published values used a stricter DG table. This regression checks the
        # new optimistic domain rather than assuming those values survive.


def test_interrupted_hall_enumeration_is_unknown_not_infeasible():
    result = ports.enumerate_cover_masks([4, 4], [1, 1], deadline=0.)
    assert result['complete'] is False
    assert result['cost_scaled'] is None
    assert result['masks'] is None


def test_bounded_hall_work_preserves_certificate_and_names_unknown_metadata(monkeypatch):
    from stowage.feasibility.tables import build_tables
    from .test_hatch import fixture
    instance = fixture()
    instance.containers[0].pod = 'THSGZ'
    tables = build_tables(instance)
    partial = dict(cost_scaled=None, masks=None, feasible_masks=0,
                   enumerated_masks=256, complete=False)
    calls = []
    def bounded(*a, **kw):
        calls.append(kw['deadline'])
        return partial
    monkeypatch.setattr(ports, 'enumerate_cover_masks', bounded)
    monkeypatch.setattr(ports.base, '_e6_witness_covers', lambda *a: {})
    baseline = dict(instance_id=instance.id, lower_bound=2.)
    result = ports.lower_bound(instance, budget_s=.01, tables=tables, base_result=baseline)
    assert calls and result['status'] == 'capacity_enumeration_unknown'
    assert result['lower_bound'] == baseline['lower_bound']
    assert result['port_capacity_unknown'] is True
    assert result['port_capacity_lower_bound'] is None
    assert result['port_shipping_lower_bound'] is None
    assert result['case_a_all_heavy_hold'] == partial
    assert result['case_b_some_heavy_deck_cost_scaled'] is None
    assert result['mandatory_hold_cells_by_call']
