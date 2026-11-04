"""Small explicit geometry counterexamples; no physical-pass claim."""
from types import SimpleNamespace as NS

import pytest

from llm.lookahead.bounds import LookaheadConfig, OptimisticCapacity


def box(cid,height,*,reef=False,special=(),length=20,dg=None):
    return NS(id=cid,height_m=height,length_ft=length,reefer=reef,
              special_stowage=list(special),dg=dg)


def geometry(boxes,columns):
    """columns: (bay, on_deck, tiers, height, plugged_tiers)."""
    cells,stacks = [],[]
    for bay,deck,tiers,height,plugs in columns:
        sid = f's{bay}_{deck}'
        ids = [f'{bay:03d}01{tier:02d}' for tier in tiers]
        stacks.append(NS(id=sid,cell_ids=ids,on_deck=deck,max_height_m=height,base_z_m=0.))
        cells.extend(NS(id=cid,stack_id=sid,accepts_20=True,accepts_40=True,
                        accepts_45=True,reefer_plug=tier in plugs)
                     for cid,tier in zip(ids,tiers))
    instance = NS(containers=boxes,load_list=[b.id for b in boxes],discharge_list=[],
        stage='A',onboard=NS(placements=[]),plan_calls=[],vessel=NS(cells=cells,stacks=stacks))
    return instance


def capacity(instance,config=None):
    return OptimisticCapacity(instance,config,visibility_caps={})


def state(where=None,buffer=()):
    return NS(where=where or {},buffer=list(buffer))


def test_tall_pool_deficit_despite_individual_supported_future_domains():
    inst = geometry([box('a',3),box('b',3),box('c',3),box('short',2.4)],
        [(1,True,[82,84,86],8.,[]),(3,True,[82],2.9,[]),(5,True,[82],2.9,[])])
    result = capacity(inst).check(state(),native_contract=True)
    assert result.status == 'reject' and result.reason == 'pool_cell_height_deficit'
    assert result.certificate['pool_units'] == 3
    assert result.certificate['pool_capacity_units'] == 2
    assert result.certificate['pool_min_height_m'] == 3.


def test_short_box_outside_reefer_pool_can_support_tall_future_upper_slot():
    inst = geometry([box('short',2.4),box('tall',3.,reef=True)],
                    [(1,True,[82,84],5.6,[84])])
    result = capacity(inst).check(state(),native_contract=True)
    assert result.status == 'admit'  # 2.4 + 3.0 fits; 3.0 + 3.0 would not
    assert result.certificate['min_support_height_m'] == 2.4


def test_dg_future_upper_slot_not_removed_by_empty_ship_static_table():
    inst = geometry([box('short',2.4),box('dg',3.,reef=True,dg=NS(imdg_class='3'))],
                    [(1,True,[82,84],5.6,[84])])
    assert capacity(inst).check(state(),native_contract=True).status == 'admit'


def resetting_case(*,hold_full=False):
    boxes = [box('deckshort',2.4),box('tall',4.,reef=True),box('holdload',2.4)]
    inst = geometry(boxes,[(1,False,[2],3.,[]),
                          (1,True,[82,84],6.,[82,84]),(3,True,[82],3.,[])])
    where = {'deckshort':('ship','0010182')}
    if hold_full:
        where['holdload'] = ('ship','0010102')
    return inst,state(where)


def test_future_hold_access_resets_deck_and_adds_reloaded_demand_once():
    inst,ex = resetting_case()
    result = capacity(inst).check(ex,native_contract=True)
    assert result.status == 'admit'
    assert result.certificate['reset_deck_boxes'] == 1
    assert result.certificate['relaxed_boxes'] == 3
    # Reloading an already buffered load ID must not double-count it.
    ex.buffer = ['holdload']
    assert capacity(inst).check(ex,native_contract=True).certificate['relaxed_boxes'] == 3


def test_full_hold_without_discharge_certifies_no_future_deck_recovery():
    inst,ex = resetting_case(hold_full=True)
    result = capacity(inst).check(ex,native_contract=True)
    assert result.status == 'reject'
    assert result.certificate['reset_deck_boxes'] == 0


def test_pending_discharge_defers_even_with_full_hold():
    inst,ex = resetting_case(hold_full=True)
    inst.discharge_list = ['holdload']
    result = capacity(inst).check(ex,native_contract=True)
    assert result.status == 'unknown' and result.reason == 'pending_discharge'


def test_unsupported_free_upper_hold_is_included_in_recovery_guard():
    inst,ex = resetting_case()
    # Only the unsupported upper hold cell has a plug. The remaining reefer
    # still may gain hold access after a future short support is placed.
    inst.vessel.stacks[0].cell_ids.append('0010104')
    inst.vessel.stacks[0].max_height_m = 7.
    inst.vessel.cells.append(NS(id='0010104',stack_id=inst.vessel.stacks[0].id,
        accepts_20=True,accepts_40=True,accepts_45=True,reefer_plug=True))
    result = capacity(inst).check(ex,native_contract=True)
    assert result.status == 'admit' and result.certificate['reset_deck_boxes'] == 1


def test_visibility_cap_changes_future_capacity_not_just_next_action():
    inst = geometry([box('a',3.),box('b',3.),box('c',3.)],[(1,True,[82,84,86],12.,[])])
    assert capacity(inst).check(state(),native_contract=True).status == 'admit'
    limited = OptimisticCapacity(inst,visibility_caps={inst.vessel.stacks[0].id:8.})
    assert limited.check(state(),native_contract=True).status == 'reject'


@pytest.mark.parametrize('kind',['executor','scope','tail','relaxed'])
def test_unknown_scope_and_work_caps_fail_open(kind):
    inst,ex = resetting_case()
    cfg = LookaheadConfig(max_remaining_boxes=1) if kind == 'tail' else (
        LookaheadConfig(max_relaxed_boxes=1) if kind == 'relaxed' else LookaheadConfig())
    if kind == 'scope':
        inst.stage = 'B'
    result = capacity(inst,cfg).check(ex,native_contract=kind != 'executor')
    assert result.status == 'unknown'


def test_through_restow_buffer_is_counted_even_outside_load_list():
    inst = geometry([box('load',3),box('through',3)],[(1,True,[82],3.,[])])
    inst.load_list = ['load']
    result = capacity(inst).check(state(buffer=['through']),native_contract=True)
    assert result.status == 'reject' and result.certificate['demand_units'] == 2
