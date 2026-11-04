"""Independent two-port capacity arithmetic and approved-map certificates."""
import importlib.util
import json
from itertools import product
from pathlib import Path

from stowage.io import load_instance

HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location('c3_bound_ports_tested', HERE/'bound_ports.py')
ports = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ports)


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


def test_four_approved_map_refinements():
    old_rows = json.loads((HERE/'bound_results.json').read_text())
    expected = [65., 90., 70., 51.]
    for old, want in zip(old_rows, expected, strict=True):
        result = ports.lower_bound(load_instance(old['input']), base_result=old)
        assert result['lower_bound'] == want
        assert result['lower_bound'] >= old['lower_bound']
        if want == 65:
            assert sorted(result['mandatory_hold_cells_by_call'].values()) == [14, 37]
            assert result['case_a_all_heavy_hold']['cost_scaled'] == 18000
        if want == 70:
            assert result['case_a_all_heavy_hold']['cost_scaled'] is None
            assert result['case_b_some_heavy_deck_cost_scaled'] == 24000
