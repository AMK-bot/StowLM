"""Independent capacity arithmetic and DG-widened passing-plan eligibility."""
from itertools import product
import json
from pathlib import Path
import sys

sys.path.insert(0,str(Path(__file__).resolve().parent))

import medium_layout
import medium_ports


def test_two_port_hall_masks_match_independent_integer_shipments():
    # Enumerate actual cell shipments rather than masks; every feasible
    # shipment must obey these necessary subset-capacity inequalities.
    caps = [1,2]
    choices = [[(a,b) for a in range(cap+1) for b in range(cap+1-a)]
               for cap in caps]
    for demand in product(range(4), repeat=2):
        costs=[]
        for shipments in product(*choices):
            if tuple(map(sum,zip(*shipments))) != demand:
                continue
            costs.append(sum(2000*(a+b>0)+2000*((a>0)+(b>0))
                             for a,b in shipments))
        got=medium_ports.enumerate_cover_masks(caps,demand)['cost_scaled']
        assert got == (min(costs) if costs else None)


def test_port_split_needs_five_edges_over_four_covers():
    result=medium_ports.enumerate_cover_masks([4,4,18,18,8,8],[37,14])
    assert result['cost_scaled']==18000
    assert sum(bool(m) for m in result['masks'])==4
    assert sum(m.bit_count() for m in result['masks'])==5


def test_capacity_cardinality_and_outward_rounding():
    assert medium_layout.minimum_covers(51,[4,4,18,18,8,8])==4
    assert medium_layout.minimum_covers(61,[4,4,18,18,8,8])==7
    assert medium_layout._floor(19999.999999999996)==19999
    assert medium_layout._ceil(20000.000000000004,2)==40001


def test_widened_mask_retains_all_three_checked_medium_stows():
    from stowage.feasibility.tables import build_tables
    from stowage.io import load_instance
    from stowage.model import MovePlan
    from stowage.moves import apply_plan
    from strengthening import safe_unary_mask
    here=Path(__file__).resolve().parent
    index=json.loads((here/'medium-references/upper_index.json').read_text())
    for row in index.values():
        instance=load_instance(row['input'])
        tables=build_tables(instance)
        mask,info=safe_unary_mask(instance,tables)
        assert info['dg_rows_rebuilt_optimistically']>0
        replay=apply_plan(instance,MovePlan.model_validate(row['plan']))
        assert replay.legal
        assert replay.count.weighted_nonproductive==row['lifts']
        for placement in replay.final_state.ship.placements:
            assert mask[tables.box_index[placement.container_id],
                        tables.slot_index[placement.slot]]
