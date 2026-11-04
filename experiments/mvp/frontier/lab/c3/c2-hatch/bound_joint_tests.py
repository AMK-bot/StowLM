"""Current-order relaxation allows arbitrary ship restows, charging only one."""
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).parent))
from itertools import permutations
import pytest
from bound_tests import fixture
from bound_joint import lower_bound
from stowage.feasibility.tables import build_tables
from stowage.model import Instance


@pytest.mark.parametrize('relocate,restow', [(1, 1), (0, 1), (1, 0), (.25, .5)])
def test_first_aboard_cycle_matches_independent_order_enumeration(relocate, restow):
    data = fixture().model_dump()
    data['objective'].update(relocate=relocate, restow=restow)
    instance = Instance.model_validate(data)
    ids = instance.load_list
    # Yard bottom->top A,B,C; final support/hatch requires A->B->C.
    final_slots = ['0010102', '0010104', '0010182']
    tables = build_tables(instance)
    tables.static[:] = False
    for bid, slot in zip(ids, final_slots):
        tables.static[tables.box_index[bid], tables.slot_index[slot]] = True
    costs = []
    for order in permutations(ids):
        rank = {bid: k for k, bid in enumerate(order)}
        relocated = {upper for k, lower in enumerate(ids)
                     for upper in ids[k+1:] if rank[lower] < rank[upper]}
        restowed = {upper for k, lower in enumerate(ids)
                    for upper in ids[k+1:] if rank[upper] < rank[lower]}
        costs.append(len(relocated) * relocate + len(restowed) * restow)
    result = lower_bound(instance, tables=tables, budget_s=2)
    assert result.status == 'optimal'
    assert result.witness_current_order == pytest.approx(min(costs))
    assert result.lower_bound == pytest.approx(4 + min(costs))
