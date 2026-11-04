"""Targeted sampling must visit both mass extrema without changing trim limits."""
import importlib.util
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace as NS

import numpy as np
import pytest


@pytest.fixture
def projection():
    path=Path(__file__).resolve().parents[2]/'experiments/linear-model/epoch_requalification/targeted.py'
    spec=importlib.util.spec_from_file_location('targeted_frontier_test',path)
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    return module.FrontierProjection


def condition():
    model=NS(tank_adjustable=np.array([True,True]),tank_ids=('aft','fore'),
        tank_capacity_kg=np.array([100.,100.]),const_mass_kg=100.,
        const_moment_x_kgm=500.,const_moment_y_kgm=0.,const_moment_z_kgm=0.,
        cell_lcg_m=np.zeros(0),cell_tcg_m=np.zeros(0),tank_lcg_m=np.array([0.,10.]),
        tank_tcg_m=np.zeros(2),tank_vcg_m=np.zeros(2),km_line=(0.,10.),
        trim_line=(0.,.01,-.05),draught_line=(1.,.001),gm_min_m=.15,
        tank_fsm_max_kgm=np.zeros(2),max_trim_m=1.,max_list_rad=.03,
        band=NS(displacement_min_kg=100.,displacement_max_kg=300.,
            draught_min_m=.1,draught_max_m=5.,max_abs_trim_m=1.,max_abs_heel_rad=.03))
    instance=NS(vessel=NS(hull=NS(max_draught_m=5.,min_draught_fwd_m=.1)),
        port=NS(max_draught_m=5.))
    return model,instance,np.zeros(0),np.zeros(0),np.zeros(2)


def test_minimum_and_maximum_objectives_reach_distinct_displacements(projection):
    minimum,maximum=projection('min'),projection('max')
    low=minimum(*condition());high=maximum(*condition())
    assert low.sum()==pytest.approx(0.)
    assert high.sum()==pytest.approx(200.)
    for result in (minimum,maximum):
        state=result.solved[0]
        assert abs(state['affine_trim_m']-state['target_affine_trim_m'])<=.005+1e-10


def test_both_mode_visits_same_frontier_at_opposite_mass_extrema(projection):
    sampler=projection('both')
    sampler(*condition())
    sampler.calls=11
    sampler(*condition())
    assert [r['mass_objective'] for r in sampler.solved]==['min','max']
    assert [r['displacement_kg'] for r in sampler.solved]==pytest.approx([100.,300.])


def test_unknown_objective_rejected(projection):
    with pytest.raises(ValueError,match='mass objective'):
        projection('random')


def test_grid_visits_interior_mass_nodes_and_preserves_random_partial_fills(projection):
    sampler=projection('grid')
    for node in range(4):
        sampler.calls=node
        model,instance,cell,heights,tank=condition()
        # The original proposal is already near the required symmetric mix.
        tank[:]=20.*(node+1)
        result=sampler(model,instance,cell,heights,tank)
        assert result is not None
        target=100.+(.2,.4,.6,.8)[node]*200.
        assert abs(100.+result.sum()-target)<=1.+1e-8
        assert np.all((result>=0.)&(result<=100.))
        assert sampler.solved[-1]['mass_objective']=='grid'
        assert sampler.solved[-1]['target_displacement_kg']==pytest.approx(target)


def test_selected_pair_knots_are_actual_mass_bounds_not_objective_hints(projection):
    sampler=projection('grid',('test',))
    model,instance,cell,heights,tank=condition()
    model.tank_ids=('test-P','test-S')
    # First point fixes both tanks empty, incompatible with interior mass140.
    assert sampler(model,instance,cell,heights,tank) is None
    # Advance to the balanced half-fill point: both actual masses must be50.
    sampler.calls=16
    model.band.displacement_min_kg=160.
    model.band.displacement_max_kg=360.
    model.trim_line=(-.65,0.,0.)
    result=sampler(model,instance,cell,heights,tank)
    assert result==pytest.approx([50.,50.])
    assert sampler.solved[-1]['selected_tank_corner']['pattern']=='balanced'


def test_resume_rejects_changed_sampler_and_preserves_existing_observation(projection,tmp_path):
    # The class shares its defining module's globals without registering an
    # import alias in sys.modules, so retrieve the pure guard there directly.
    namespace=projection.__init__.__globals__
    args=NS(ship='feeder_800',level='light',samples=256,seed=3823,
        mass_objective='grid',tank_pairs=['DB-M','HEEL','WING-A'],no_public=False)
    config=namespace['sampler_config'](args)
    source='a'*64
    record=dict(physical_inputs_start={'current':'physics'},sampler_builder_version='1.0',
        sampler_source_sha256=source,sampler_config=config,
        sampling_projection={'accepted_corners':[{'do_not_erase':True}]})
    inputs=dict(physical_inputs=record['physical_inputs_start'],config=config,
        builder_version='1.0',source_sha256=source)
    record['sampler_inputs_hash']=hashlib.sha256(json.dumps(inputs,
        sort_keys=True,separators=(',',':')).encode()).hexdigest()
    path=tmp_path/'train-light.json'
    path.write_text(json.dumps(record))
    original=path.read_bytes()
    reused=namespace['resume_record'](path,args,source)
    assert reused['sampling_projection']['accepted_corners']==[{'do_not_erase':True}]
    args.mass_objective='min'
    with pytest.raises(ValueError,match='config/source epoch changed'):
        namespace['resume_record'](path,args,source)
    args.mass_objective='grid'
    with pytest.raises(ValueError,match='config/source epoch changed'):
        namespace['resume_record'](path,args,'b'*64)
    assert path.read_bytes()==original
