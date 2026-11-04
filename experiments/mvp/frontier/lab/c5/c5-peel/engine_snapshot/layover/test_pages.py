# Copied from experiments/mvp/frontier/lab/c3/c3-layover/test_pages.py; engine integration changes are local.
"""Prefix arithmetic and real arrival replay; no long solver run required."""
import importlib.util
from pathlib import Path
import sys
from types import SimpleNamespace

import numpy as np
from stowage.model import Instance
from stowage.moves import apply_plan

HERE = Path(__file__).resolve().parent
from . import method

def fake_model():
    m = method.pages.Model.__new__(method.pages.Model)
    m.deck_tiers = None; m.deck_mass_factor = 1.; m.oracle = None
    m.arrival = {'aboard':'0010102'}
    m.occupied = {'0010102':'aboard'}
    m.T = SimpleNamespace(slot_ids=['0010102','0010104','0010106'],
                          box_index={'aboard':0}, height=np.array([2.591,2.591]),
                          share=np.array([12000.,8000.]), mass=np.array([12000.,8000.]),
                          rank=np.array([2,1]), e2_tol=0, e1_threshold=15000.,
                          is_dg=np.array([False,False]), closes_stack=np.array([False,False]),
                          stack_base_z=np.array([0.]))
    m.pod={0:1,1:2};m.ports=[1,2];m.heavy={0:False,1:False}
    m.layout={};m.layout_deck=False;m.static=np.ones((2,3),dtype=bool)
    return m


def test_prefix_subtracts_mass_height_and_slots():
    m=fake_model()
    raw=method.pages.BASE.Page('20',(0,),(0,1,2),30000.,8.,False,'cover')
    page=m._capped(raw)
    assert page.prefix == (0,)
    assert page.slots == (1,2)
    assert page.limit_kg == 18000.
    assert abs(page.hmax-5.409)<1e-9
    assert m.legal(page,[1])
    assert m.height_of(page,0) == 2.591+1.3


def test_prefix_rank_and_heavy_rules_reject_illegal_top():
    m=fake_model()
    page=m._capped(method.pages.BASE.Page('20',(0,),(0,1,2),30000.,8.,False,'cover'))
    m.T.rank[1]=3
    assert not m.legal(page,[1])
    m.T.rank[1]=1;m.T.mass[1]=16000.
    assert not m.legal(page,[1])


def test_fixed_prefix_creates_future_restow_price():
    m=fake_model()
    page=m._capped(method.pages.BASE.Page('20',(0,),(0,1,2),30000.,8.,False,'cover'))
    m.arrival_deck={'cover':set()};m.arrival_hold_ports={'cover':set()}
    assert m.pattern_restow_cost(page,[1]) == 2.
    m.pod[0]=2;m.pod[1]=1
    assert m.pattern_restow_cost(page,[1]) == 0.


def test_current_discharge_is_explicit_and_buffer_is_pricing_item():
    # Legacy layover has a due hold box and a retained box above it.
    path=Path('data/fixtures/layover.json')
    i=Instance.model_validate_json(path.read_text())
    working, actions, ex=method.prepare(i)
    assert {a.box for a in actions} == set(i.discharge_list)
    assert set(working.load_list) == set(i.load_list)|set(ex.buffer)
    assert not working.discharge_list
    assert all(b not in {q.container_id for q in working.onboard.placements} for b in i.discharge_list)
    assert any(m.kind=='restow_off' for m in ex.moves)


def test_fresh_train_tiny_candidate_passes_unchanged_full_checker():
    path=HERE/'cohort/train.layover.tiny.o30.s331101000.json'
    if not path.exists():
        import pytest
        pytest.skip('fresh cohort not generated')
    i=Instance.model_validate_json(path.read_text())
    context={}
    candidates=method.propose(i,seed=0,budget_s=3.,context=context)
    assert candidates
    problem=method.Problem(i)
    scored=problem.score(candidates[0])
    assert scored is not None
    replay=apply_plan(i,scored.plan)
    assert replay.legal
    assert method.check(i,scored.plan).passed
    assert {m.container_id for m in scored.plan.moves if m.kind=='discharge'} == set(i.discharge_list)


def fresh_tiny_with_arrival(slot):
    path=HERE/'cohort/train.layover.tiny.o30.s331101000.json'
    i=Instance.model_validate_json(path.read_text())
    survivor=i.onboard.placements[1].model_copy(update={'slot':slot})
    return i.model_copy(update={'discharge_list':[],
                               'onboard':i.onboard.model_copy(update={'placements':[survivor]})})


def test_hold_load_restores_fixed_deck_prefix_without_gratuitous_shift():
    i=fresh_tiny_with_arrival('0030282')
    working,prefix,ex=method.prepare(i)
    p=method.Problem(working)
    early,late=i.load_list
    actions=method.order_with_prefixes(p,{late:'0010202',early:'0010204'},ex,prefix)
    assert actions is not None
    scored=method.Problem(i).score(actions)
    assert scored is not None
    moves=scored.plan.moves
    arrival=i.onboard.placements[0].container_id
    assert [m.kind for m in moves if getattr(m,'container_id',None)==arrival] == ['restow_off','restow_on']
    replay=apply_plan(i,scored.plan)
    assert replay.legal
    assert {p.container_id for p in replay.final_state.ship.placements} == set(i.load_list)|{arrival}


def test_page_type_groups_distinguish_fixed_prefixes():
    i=fresh_tiny_with_arrival('0030202')
    working,_,_=method.prepare(i)
    model=method.pages.Model(method.Problem(working))
    assert any(page.prefix for page in model.pages)
    for same_type in model.types.values():
        assert len({model.pages[k].prefix for k in same_type}) == 1


def test_voluntary_prefix_restow_has_real_off_on_price():
    i=fresh_tiny_with_arrival('0030202')
    box=i.onboard.placements[0].container_id
    working,prefix,ex=method.prepare(i,box)
    assert box in working.load_list
    assert ex.location(box)==('quay','quay')
    # A single-box prefix bought off and put back costs exactly two now.
    p=method.Problem(working)
    early,late=i.load_list
    actions=method.order_with_prefixes(p,{box:'0030202',late:'0010202',early:'0010204'},ex,prefix)
    scored=method.Problem(i).score(actions)
    assert scored is not None
    replay=apply_plan(i,scored.plan)
    assert replay.count.restow_lifts == 2
    assert replay.count.weighted_nonproductive >= 2


def test_many_prefix_branch_restores_every_arrival_and_passes_full_checker():
    i=fresh_tiny_with_arrival('0030202')
    early,late=i.load_list
    arrival=i.onboard.placements[0].container_id
    second=i.onboard.placements[0].model_copy(update={'container_id':early,'slot':'0010202'})
    yard=i.yard.model_copy(update={'stacks':[s.model_copy(update={'boxes':[b for b in s.boxes if b!=early]})
                                                  for s in i.yard.stacks if any(b!=early for b in s.boxes)]})
    i=i.model_copy(update={'load_list':[late], 'yard':yard,
                          'onboard':i.onboard.model_copy(update={'placements':i.onboard.placements+[second]})})
    label,roots=method.restow_options(i)[0]
    assert label=='all_prefixes' and len(roots)==2
    # Duplicate requests and already-cleared closures are charged only once.
    working,prefix,ex=method.prepare(i,roots+roots)
    assert not working.onboard.placements
    assert len([a for a in prefix if isinstance(a,method.RestowOff)])==2
    assert set(working.load_list)=={arrival,early,late}
    p=method.Problem(working)
    actions=method.order_with_prefixes(p,{arrival:'0030202',early:'0010202',late:'0030204'},ex,prefix)
    scored=method.Problem(i).score(actions)
    assert scored is not None
    replay=apply_plan(i,scored.plan)
    assert replay.legal
    assert replay.count.restow_lifts==4
    assert {p.container_id for p in replay.final_state.ship.placements}=={arrival,early,late}
    assert method.check(i,scored.plan).passed


def test_tiny_direct_shift_saves_one_counted_lift_and_passes_full_checker():
    i=Instance.model_validate_json((HERE/'cohort/train.layover.tiny.o30.s331101000.json').read_text())
    through=next(p.container_id for p in i.onboard.placements if p.container_id not in i.discharge_list)
    working,prefix,ex=method.prepare(i,shift=(through,'0010202'))
    assert ex.location(through)==('ship','0010202')
    assert not ex.buffer
    p=method.Problem(working)
    early,late=i.load_list
    actions=method.order_with_prefixes(p,{early:'0010282',late:'0030202'},ex,prefix)
    scored=method.Problem(i).score(actions)
    assert scored is not None
    report=method.check(i,scored.plan)
    assert report.passed
    assert report.count.restow_lifts==1
    assert scored.objective==5.
