"""Physical cut scope and restricted-pool guards, independent of map outcomes."""
from types import SimpleNamespace as NS

import numpy as np

from .cuts import respects_cuts, securing_cut, restricted_master


def _model():
    tables = NS(slot_n=np.array([1, 1, 1, 1]),
        slot_stacks=np.array([[0], [0], [1], [1]]),
        slot_k=np.array([[0], [1], [0], [1]]),
        slot_ids=['a0', 'a1', 'b0', 'b1'], stack_ids=['a', 'b'],
        box_ids=['x', 'y', 'z', 'w'])
    pages = [NS(kind='20', stacks=(0,), slots=(0, 1), deck=True),
             NS(kind='20', stacks=(1,), slots=(2, 3), deck=True)]
    columns = [NS(page=0, boxes=(0, 1), cost=0), NS(page=1, boxes=(2, 3), cost=0)]
    model = NS(T=tables, pages=pages, columns=columns, boxes=[0, 1, 2, 3],
        p=NS(instance=NS(onboard=NS(placements=[]))), banned=set())
    model.assignment = lambda chosen: {tables.box_ids[b]: tables.slot_ids[pages[columns[k].page].slots[t]]
        for k in chosen for t, b in enumerate(columns[k].boxes)}
    return model


def _proof():
    return dict(status='infeasible_fixed_stow', proof='guarded securing witness',
        witnesses={'max_lashing_ratio': ['a0', 'a1']},
        witness_details={'max_lashing_ratio': {'source': 'verified'}},
        lower_ratios={'max_lashing_ratio': 1.1})


def test_chain_cut_preserves_whole_support_page():
    cut = securing_cut(_model(), [0, 1], _proof())
    assert cut['columns'] == [0] and cut['rhs'] == 0
    assert cut['kind'] == 'securing_chain_no_good'
    assert cut['supports'][0]['box_slots'] == [['x', 'a0'], ['y', 'a1']]
    assert not respects_cuts([0, 1], [cut])
    assert respects_cuts([1], [cut])


def test_other_physical_certificate_excludes_only_whole_stow():
    proof = _proof()
    proof['witness_details'] = {}
    cut = securing_cut(_model(), [0, 1], proof)
    assert cut['columns'] == [0, 1] and cut['rhs'] == 1
    assert cut['kind'] == 'certified_stow_no_good'


def test_no_physical_cut_from_local_lp_failure_or_missing_proof():
    for proof in (dict(status='unknown', local_certificate={'physical_proof': False}),
                  dict(status='infeasible_fixed_stow', proof=None),
                  dict(status='loading_feasible', proof='not a physical proof')):
        assert securing_cut(_model(), [0, 1], proof) is None


def test_mixed_holed_overlapping_and_incomplete_families_refuse_cuts():
    model = _model(); model.pages[0].kind = 'mixed'
    assert securing_cut(model, [0, 1], _proof()) is None
    model = _model(); model.T.slot_k[0, 0] = 1
    assert securing_cut(model, [0, 1], _proof()) is None
    model = _model(); model.pages[1].stacks = (0,)
    assert securing_cut(model, [0, 1], _proof()) is None
    assert securing_cut(_model(), [0], _proof()) is None


def test_restricted_master_honors_cut_and_banned_columns():
    model = _model()
    model.columns += [NS(page=0, boxes=(2, 3), cost=0), NS(page=1, boxes=(0, 1), cost=0)]
    cut = securing_cut(model, [0, 1], _proof())
    chosen, _ = restricted_master(model, [cut], seconds=1., seed=0, hint=[0, 1])
    assert set(chosen) == {2, 3} and respects_cuts(chosen, [cut])
    model.banned.add(2)
    chosen, _ = restricted_master(model, [cut], seconds=1., seed=0)
    assert chosen is None
