"""Arrival occupancy, closure and exact heat-envelope regressions."""
from pathlib import Path
from types import SimpleNamespace
import numpy as np
from stowage.model import Instance
from stowage.moves import apply_plan
from alns import Problem
from arrival import prepare, restow_options, replay_actions, arrival_condition
from arrival_model import Model, Page

HERE=Path(__file__).resolve().parent


def instance(size='tiny', occupancy=30, seed=331401000):
    path=HERE/'cohort'/f'train.layover.{size}.o{occupancy}.s{seed}.json'
    return Instance.model_validate_json(path.read_text())


def test_preparation_discharge_closure_is_actual_not_counted_guess():
    i=instance()
    branch=prepare(i)
    assert not set(i.discharge_list)&set(branch.fixed)
    assert branch.required == (set(i.load_list)|{p.container_id for p in i.onboard.placements})-set(i.discharge_list)
    assert any(m.kind=='discharge' for m in branch.expander.moves)
    assert branch.metadata['prefix_restow_off_lifts']==sum(m.kind=='restow_off' for m in branch.expander.moves)
    assert i.onboard.placements # Input was not emptied or mutated.


def test_model_retains_native_indices_and_full_pages():
    branch=prepare(instance())
    p=Problem(branch.working);m=Model(p,branch)
    T=p.engine.T
    assert set(m.boxes)=={T.box_index[b] for b in branch.required}
    assert m.fixed_pairs=={T.box_index[b]:T.slot_index[q] for b,q in branch.fixed.items()}
    for b,q in m.fixed_pairs.items():
        assert list(np.flatnonzero(m.static[b]))==[q]
        assert any(q in page.slots for page in m.pages)
    fixed_cells={int(c) for q in m.fixed_pairs.values() for c in T.slot_cells[q] if c>=0}
    for b in m.free_boxes:
        assert all(not fixed_cells.intersection(int(c) for c in T.slot_cells[q] if c>=0) for q in np.flatnonzero(m.static[b]))


def test_all_restow_branch_contains_every_retained_box_as_item():
    i=instance('small',30,331401024)
    choices=dict(restow_options(i))
    branch=prepare(i,choices['all'],label='all')
    assert not branch.fixed
    expected=(set(i.load_list)|{p.container_id for p in i.onboard.placements})-set(i.discharge_list)
    assert set(branch.working.load_list)==expected
    assert branch.metadata['prefix_restow_off_lifts']>0


def test_replay_refuses_missing_retained_cargo_and_moved_fixed_pair():
    i=instance();branch=prepare(i)
    assignment=dict(branch.fixed)
    assert replay_actions(branch,assignment) is None # New cargo missing.
    if assignment:
        box=next(iter(assignment));assignment[box]='9999999'
        assert replay_actions(branch,assignment) is None


def test_heat_test_uses_actual_support_height_without_all_denied_fallback():
    m=Model.__new__(Model)
    m.T=SimpleNamespace(heat=[True],box_ids=['hot'],slot_cells=np.array([[0]]),cell_ids=['c'])
    box=SimpleNamespace(length_ft=20,height_m=2.591,oog=None)
    heat=SimpleNamespace(x_min_m=-5.,x_max_m=5.,y_min_m=-5.,y_max_m=5.,z_min_m=-1.,z_max_m=2.)
    vessel=SimpleNamespace(heat_sources=[heat],tanks=[])
    heat.kind='structure';heat.id='heat';heat.tank_id=None
    m.p=SimpleNamespace(boxes={'hot':box},cells={'c':SimpleNamespace(lcg_m=0.,tcg_m=0.)},instance=SimpleNamespace(vessel=vessel))
    m.heat_clearance=1.
    page=Page('20',(0,),(0,),30000.,10.,False,None)
    assert not m.heat_ok(page,0,0,0.)
    assert m.heat_ok(page,0,0,3.)


def test_arrival_condition_reads_given_ship_tanks():
    i=instance()
    from stowage.checker.visibility import _loading_condition
    expected=_loading_condition(i,i.onboard)
    got=arrival_condition(i)
    assert got['gm']==float(expected.gm_m)
    assert got['draught_aft_m']==float(expected.t_aft_m)
    assert got['draught_fwd_m']==float(expected.t_fwd_m)


def test_kept_heat_breach_is_not_mistaken_for_core_inherited_tolerance():
    m=Model.__new__(Model)
    m.T=SimpleNamespace(heat=[True],box_ids=['kept'],slot_cells=np.array([[0]]),cell_ids=['c'],
                        closes_stack=[False],is_dg=[False],share=[10000.],height=[2.591],
                        stack_base_z=[0.],rank=[0],e2_tol=0)
    box=SimpleNamespace(length_ft=20,height_m=2.591,oog=None)
    heat=SimpleNamespace(x_min_m=-5.,x_max_m=5.,y_min_m=-5.,y_max_m=5.,z_min_m=-1.,z_max_m=2.,
                         kind='structure',id='heat',tank_id=None)
    m.p=SimpleNamespace(boxes={'kept':box},cells={'c':SimpleNamespace(lcg_m=0.,tcg_m=0.)},
                        instance=SimpleNamespace(vessel=SimpleNamespace(heat_sources=[heat],tanks=[])))
    m.heat_clearance=1.;m.fixed_pairs={0:0};m.static=np.ones((1,1),dtype=bool)
    m.heavy={0:False};m.sight_frames=[]
    page=Page('20',(0,),(0,),30000.,10.,False,None)
    assert not m.legal(page,[0])
