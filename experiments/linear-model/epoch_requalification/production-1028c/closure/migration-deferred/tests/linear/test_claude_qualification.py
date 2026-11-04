"""N1: independent corner evidence cannot be omitted or trained on holdouts."""
from copy import deepcopy
import json
import importlib.util
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

from stowage.linear import margin_policy as policy

ROOT = Path(__file__).resolve().parents[2]


def _module(name):
    path = ROOT / 'experiments/linear-model/epoch_requalification' / (name+'.py')
    spec = importlib.util.spec_from_file_location('claude_qualification_'+name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def qualification(monkeypatch, tmp_path):
    necessary, promote = _module('necessary'), _module('promote')
    exact = {name: ([0., 0.] if name in ('sf_n','bm_nm') else 0.)
             for name in policy._METRICS}
    def population(seed, aft=0.):
        signed = policy._zeros(2)
        signed.update({side.replace('unsafe','conservatism'): deepcopy(values)
                       for side,values in signed.copy().items()})
        signed['upper_conservatism']['draught_aft_m'] = aft
        return dict(seed=seed, samples=2, physical_inputs_start={'epoch':1},
            diagnostics={'signed_errors':signed},
            public_checker_comparison={'exact_condition':exact,'actual_metrics':exact})
    raw = population(3701)
    raw['physical_inputs']={'epoch':1}
    raw['diagnostics'].update(response_calibration_identity='response',
        reference_identity={'method':1},family_domain={})
    template = SimpleNamespace(station_x_m=np.array([0.,1.]),
        band=SimpleNamespace(displacement_max_kg=100.), _reference_params={'hull':{'lpp_m':10.}})
    monkeypatch.setattr(necessary,'qualify_boundary',lambda *a,**kw:{'evidence':[]})
    monkeypatch.setattr(policy,'_boundary_list',lambda *a,**kw:{})
    proxy = SimpleNamespace(merge_envelopes=promote.merge_envelopes,
        read=lambda path:{},pin=lambda path:{'path':str(path),'sha256':'a'*64})
    def build(**kwargs):
        return necessary.build(proxy,tmp_path,raw,[population(1,.6)],
            [population(2,.59)],{},template,None,**kwargs)
    return SimpleNamespace(necessary=necessary,population=population,build=build)


def test_necessary_promotion_rejects_missing_corner_evidence(qualification):
    with pytest.raises(ValueError,match='edge|corner'):
        qualification.build()


def _edges(qualification, monkeypatch, *, holdout=.7):
    records=dict(training=[qualification.population(54931,.8)],
        holdout=[qualification.population(54932,holdout)], pins=[],coverage={})
    monkeypatch.setattr(qualification.necessary,'load_edge_populations',
        lambda *a,**kw:records,raising=False)
    return records


def test_necessary_merges_corner_training_without_changing_multiplier(qualification,monkeypatch):
    _edges(qualification,monkeypatch)
    result=qualification.build()
    assert result['physical_optimistic']['upper_unsafe']['draught_aft_m']==pytest.approx(.8)
    gate=result['edge_holdout_qualification']
    assert gate['multiplier']==1.1
    assert gate['holdout_samples']==2
    assert gate['holdout_unsafe']['upper_unsafe']['draught_aft_m']==pytest.approx(.7)


def test_edge_holdout_breach_is_not_enrichment_or_retroactive_pass(qualification,monkeypatch):
    _edges(qualification,monkeypatch,holdout=.9)
    with pytest.raises(ValueError,match='holdout failed.*draught_aft_m'):
        qualification.build()


def test_edge_holdout_seed_cannot_overlap_training(qualification,monkeypatch):
    records=_edges(qualification,monkeypatch)
    records['holdout'][0]['seed']=54931
    with pytest.raises(ValueError,match='edge.*seed|seed.*edge'):
        qualification.build()


def test_edge_holdout_cannot_overlap_an_ancestral_training_seed(qualification,monkeypatch):
    records=_edges(qualification,monkeypatch)
    records['training'][0]['_edge_training_seeds']=[54931,54933]
    records['holdout'][0]['seed']=54933
    with pytest.raises(ValueError,match='edge.*seed|seed.*edge'):
        qualification.build()


def test_explicit_former_edge_holdouts_enrich_only_training_at_same_multiplier(qualification,monkeypatch):
    edges=_edges(qualification,monkeypatch,holdout=.95)
    edges['holdout'][0]['seed']=54936
    population=qualification.population(54934,1.)
    population.update(phase='holdout',independent_holdout=False,_edge_training_seeds=[54934])
    metadata=dict(original_phase='holdout',original_seed=54934,independent_holdout=False)
    monkeypatch.setattr(qualification.necessary,'load_calibration_enrichment',
        lambda *a,**kw:dict(populations=[population],pins=[],metadata=[metadata]),raising=False)
    interior={}
    def boundary(*args,**kwargs):
        interior['training']=args[2]
        interior['holdouts']=args[3]
        return {'evidence':[]}
    monkeypatch.setattr(qualification.necessary,'qualify_boundary',boundary)
    result=qualification.build(calibration_enrichment_folders=[Path('archived-v2')])
    assert result['physical_optimistic']['upper_unsafe']['draught_aft_m']==pytest.approx(1.)
    gate=result['edge_holdout_qualification']
    assert gate['multiplier']==1.1
    assert gate['holdout_seed']==54936
    assert 54934 in gate['calibration_seeds']
    assert result['calibration_enrichment']==[metadata]
    assert population in interior['training']
    assert population not in interior['holdouts']


def test_new_edge_holdout_cannot_replay_calibration_enrichment_under_new_seed(qualification,monkeypatch):
    edges=_edges(qualification,monkeypatch)
    edges['holdout'][0].update(seed=54936,_edge_sample_ids=['same-physical-state'])
    population=qualification.population(54934,1.)
    population.update(_edge_training_seeds=[54934],_edge_sample_ids=['same-physical-state'])
    monkeypatch.setattr(qualification.necessary,'load_calibration_enrichment',
        lambda *a,**kw:dict(populations=[population],pins=[],metadata=[]),raising=False)
    with pytest.raises(ValueError,match='holdout repeats.*enrichment'):
        qualification.build(calibration_enrichment_folders=[Path('archived-v2')])


def test_new_edge_holdout_cannot_overlap_explicit_calibration_enrichment(qualification,monkeypatch):
    _edges(qualification,monkeypatch)
    population=qualification.population(54932,1.)
    population.update(phase='holdout',independent_holdout=False,_edge_training_seeds=[54932])
    monkeypatch.setattr(qualification.necessary,'load_calibration_enrichment',
        lambda *a,**kw:dict(populations=[population],pins=[],metadata=[]),raising=False)
    with pytest.raises(ValueError,match='edge.*seed|seed.*edge'):
        qualification.build(calibration_enrichment_folders=[Path('archived-v1')])


@pytest.fixture
def edge_data():
    necessary=_module('necessary')
    band=SimpleNamespace(displacement_min_kg=90.,displacement_max_kg=110.,
        draught_min_m=1.,draught_max_m=9.,max_abs_trim_m=1.,max_abs_heel_rad=.1)
    domain=dict(fixed_x_min_m=-10.,fixed_x_max_m=10.,fixed_y_abs_max_m=10.,kg_min_m=0.,kg_max_m=10.)
    diagnostics=dict(family_calibration_cache_key='family',response_calibration_identity='response',
        reference_identity={'method':1},family_domain=domain)
    raw=dict(ship='feeder_500',physical_inputs={'epoch':1},diagnostics=diagnostics)
    template=SimpleNamespace(station_x_m=np.array([0.,1.]),band=band)
    state=dict(state_id='edge-1',admitted=True,mass_kg=100.,
        fixed_point=dict(x_m=0.,y_m=0.,z_m=2.,mass_kg=50.),
        tank_masses_kg=[10.],affine_restoring_kgm=50.,transverse_moment_kgm=0.,
        exact_gm_m=1.,affine_gm_m=.5,exact_trim_m=0.,affine_trim_m=.2,
        exact_list_rad=0.,affine_list_rad=0.,exact_draught_m=5.,affine_draught_m=5.7,
        exact_sf_n=[0.,0.],affine_sf_n=[4.,-3.],exact_bm_nm=[0.,0.],affine_bm_nm=[-5.,6.],
        quantity_pass={'gm':False,'sf':False},manual_pass={'trim':False})
    record=dict(format=1,kind=necessary.EDGE_KIND,complete=True,samples=1,attempts=1,seed=54931,
        ship='feeder_500',level='family',phase='train',role='grid',
        physical_inputs_start={'epoch':1},physical_inputs_end={'epoch':1},
        physical_inputs_unchanged=True,calibration=diagnostics,station_x_m=[0.,1.],states=[state],
        coverage={'required_strata':['deep'],'strata':{'deep':dict(requested=1,evaluated=1,retained=1)}})
    return SimpleNamespace(necessary=necessary,raw=raw,template=template,record=record)


def test_edge_residuals_recomputed_per_station_and_without_operational_filter(edge_data):
    d=edge_data
    result=d.necessary.edge_population(d.record,d.raw,d.template,transfer=policy._zeros(2))
    signed=result['diagnostics']['signed_errors']
    assert signed['lower_conservatism']['gm_m']==pytest.approx(.5)
    assert signed['upper_conservatism']['draught_aft_m']==pytest.approx(.8)
    assert signed['upper_conservatism']['sf_n']==[4.,0.]
    assert signed['lower_conservatism']['sf_n']==[0.,3.]
    assert result['samples']==1


@pytest.mark.parametrize('mutation,match', [
    (lambda r:r['states'][0].update(admitted=False),'admission'),
    (lambda r:r['states'][0]['fixed_point'].update(x_m=100.),'admission'),
    (lambda r:r['coverage']['strata'].clear(),'coverage'),
    (lambda r:r['coverage']['required_strata'].append('missing'),'stratum'),
    (lambda r:r['coverage']['strata']['deep'].update(evaluated=0),'coverage'),
    (lambda r:r['physical_inputs_end'].update(epoch=2),'epoch'),
    (lambda r:r['states'][0].update(affine_sf_n=[1.]),'quantity'),
    (lambda r:r.update(samples=2),'counts'),
])
def test_edge_population_rejects_omissions_forged_flags_and_stale_data(edge_data,mutation,match):
    d=edge_data;mutation(d.record)
    with pytest.raises(ValueError,match=match):
        d.necessary.edge_population(d.record,d.raw,d.template,transfer=policy._zeros(2))


@pytest.mark.parametrize('pin',[{}, {'path':'x','role':'extra'}, {'sha256':'x'}])
def test_edge_pin_with_missing_required_field_fails_closed(edge_data,pin):
    with pytest.raises(ValueError,match='path/checksum'):
        edge_data.necessary._checked_pin(SimpleNamespace(),pin)


@pytest.fixture
def edge_files(edge_data,tmp_path):
    d=edge_data;n=d.necessary
    def write(path,value):
        path.write_text(json.dumps(value))
    def pin(path):
        import hashlib
        return dict(path=str(path),sha256=hashlib.sha256(path.read_bytes()).hexdigest())
    source=tmp_path/'sampler.py';source.write_text('# frozen test sampler\n')
    exact={metric:([0.,0.] if metric in ('sf_n','bm_nm') else 0.) for metric in policy._METRICS}
    for phase in ('train','holdout'):
        reference=dict(phase=phase,physical_inputs_start={'epoch':1},physical_inputs_end={'epoch':1},
            physical_inputs_unchanged=True,public_checker_comparison={
                'exact_condition':exact,'actual_metrics':exact})
        write(tmp_path/(phase+'-light.json'),reference)
    for filename,phase,role,seed,count,offset in (
        ('necessary-edges-train.json','train','grid',54931,1,0.),
        ('necessary-edges-holdout.json','holdout','independent-edge-holdout',54932,64,3.),
        ('necessary-review.json','train','review-regression',54939,1,2.)):
        record=deepcopy(d.record)
        record.update(phase=phase,role=role,seed=seed,samples=count,attempts=count)
        states=[]
        for index in range(count):
            state=deepcopy(record['states'][0]);state['state_id']=f'{phase}-{index}'
            state['fixed_point']['x_m']=offset+index*.01
            states.append(state)
        record['states']=states
        record['coverage']['strata']['deep']=dict(requested=count,evaluated=count,retained=count)
        record.update(sampler_builder_version='1.0',sampler_source=pin(source),
            sampler_source_sha256=pin(source)['sha256'],
            sampler_config=dict(ship='feeder_500',level='family',seed=seed,samples=count,
                required_strata=['deep'],affine_feasibility_filter=False),
            reference_transfer_source=pin(tmp_path/(phase+'-light.json')))
        record['sampler_inputs_hash']=n._digest(dict(physical_inputs=record['physical_inputs_start'],
            config=record['sampler_config'],builder_version='1.0',source_sha256=record['sampler_source_sha256']))
        write(tmp_path/filename,record)
    proxy=SimpleNamespace(ROOT=tmp_path,read=lambda p:json.loads(p.read_text()),pin=pin,
                          sha=lambda p:pin(p)['sha256'])
    return SimpleNamespace(data=d,folder=tmp_path,promote=proxy,write=write)


def test_complete_edge_files_keep_review_training_and_edge_holdouts_independent(edge_files):
    f=edge_files;d=f.data
    result=d.necessary.load_edge_populations(f.promote,f.folder,d.raw,d.template)
    assert [r['seed'] for r in result['training']]==[54931,54939]
    assert [r['seed'] for r in result['holdout']]==[54932]
    assert result['holdout'][0]['samples']==64
    assert set(result['coverage'])=={'grid','independent-edge-holdout','review-regression'}


def test_explicit_edge_holdout_enrichment_preserves_original_source_and_phase(edge_files):
    f=edge_files;d=f.data;path=f.folder/'necessary-edges-holdout.json'
    before=path.read_bytes()
    result=d.necessary.load_calibration_enrichment(f.promote,[f.folder],d.raw,d.template)
    assert path.read_bytes()==before
    record=result['populations'][0]
    assert record['phase']=='holdout'
    assert record['seed']==54932
    assert record['independent_holdout'] is False
    assert record['_edge_training_seeds']==[54932]
    assert result['metadata'][0]['original_phase']=='holdout'
    assert result['metadata'][0]['original_role']=='independent-edge-holdout'
    assert result['metadata'][0]['independent_holdout'] is False
    assert result['metadata'][0]['source']==f.promote.pin(path)


def test_edge_enrichment_refuses_duplicate_populations(edge_files):
    f=edge_files;d=f.data
    with pytest.raises(ValueError,match='duplicate'):
        d.necessary.load_calibration_enrichment(f.promote,[f.folder,f.folder],d.raw,d.template)


@pytest.mark.parametrize('mutation,match',[
    ('train','identity|role'),('v3','former|v1|v2'),('epoch','epoch')])
def test_edge_enrichment_refuses_training_fresh_holdout_or_stale_epoch(edge_files,mutation,match):
    f=edge_files;d=f.data;path=f.folder/'necessary-edges-holdout.json'
    record=f.promote.read(path)
    if mutation=='train':record.update(phase='train',role='grid')
    elif mutation=='v3':record['sampler_builder_version']='3.0'
    else:record['physical_inputs_end']={'epoch':2}
    f.write(path,record)
    with pytest.raises(ValueError,match=match):
        d.necessary.load_calibration_enrichment(f.promote,[f.folder],d.raw,d.template)


def test_frozen_review_cannot_be_omitted_for_affected_hull(edge_files):
    f=edge_files;d=f.data;(f.folder/'necessary-review.json').unlink()
    with pytest.raises(ValueError,match='evidence missing.*necessary-review'):
        d.necessary.load_edge_populations(f.promote,f.folder,d.raw,d.template)


def test_edge_holdouts_cannot_replay_a_training_point(edge_files):
    f=edge_files;d=f.data;path=f.folder/'necessary-edges-holdout.json'
    record=f.promote.read(path);record['states'][0]['fixed_point']['x_m']=0.
    f.write(path,record)
    with pytest.raises(ValueError,match='holdout repeats'):
        d.necessary.load_edge_populations(f.promote,f.folder,d.raw,d.template)


def test_edge_reference_transfer_is_pinned_and_separate_by_phase(edge_files):
    f=edge_files;d=f.data;path=f.folder/'necessary-edges-holdout.json'
    record=f.promote.read(path)
    record['reference_transfer_source']=f.promote.pin(f.folder/'train-light.json')
    f.write(path,record)
    with pytest.raises(ValueError,match='not independent'):
        d.necessary.load_edge_populations(f.promote,f.folder,d.raw,d.template)


def test_v2_sampler_binds_its_imported_base_source(edge_files):
    f=edge_files;d=f.data;path=f.folder/'necessary-edges-train.json'
    base=f.folder/'base_sampler.py';base.write_text('# immutable base numerical construction\n')
    record=f.promote.read(path)
    record.update(sampler_builder_version='2.0',sampler_base_source=f.promote.pin(base))
    record['sampler_config']['sampler_base_source']=record['sampler_base_source']
    record['sampler_inputs_hash']=d.necessary._digest(dict(
        physical_inputs=record['physical_inputs_start'],config=record['sampler_config'],
        builder_version='2.0',source_sha256=record['sampler_source_sha256']))
    f.write(path,record)
    assert d.necessary.load_edge_populations(f.promote,f.folder,d.raw,d.template)['training']
    base.write_text('# changed construction\n')
    with pytest.raises(ValueError,match='checksum'):
        d.necessary.load_edge_populations(f.promote,f.folder,d.raw,d.template)


def test_independent_edge_qualification_requires_at_least64_holdouts(edge_files):
    f=edge_files;d=f.data;path=f.folder/'necessary-edges-holdout.json';r=f.promote.read(path)
    r['states']=r['states'][:63];r.update(samples=63,attempts=63)
    r['coverage']['strata']['deep']=dict(requested=63,evaluated=63,retained=63)
    r['sampler_config']['samples']=63
    r['sampler_inputs_hash']=d.necessary._digest(dict(
        physical_inputs=r['physical_inputs_start'],config=r['sampler_config'],
        builder_version='1.0',source_sha256=r['sampler_source_sha256']))
    f.write(path,r)
    with pytest.raises(ValueError,match='requires64'):
        d.necessary.load_edge_populations(f.promote,f.folder,d.raw,d.template)


def _rehash_edge(f,record):
    record['sampler_inputs_hash']=f.data.necessary._digest(dict(
        physical_inputs=record['physical_inputs_start'],config=record['sampler_config'],
        builder_version=record['sampler_builder_version'],source_sha256=record['sampler_source_sha256']))


@pytest.fixture
def v3_edge_files(edge_files):
    f=edge_files;path=f.folder/'necessary-edges-train.json'
    prior=f.promote.read(path)
    base=f.folder/'base.py';base.write_text('# fixed base\n')
    prior.update(seed=54933,sampler_builder_version='2.0',sampler_base_source=f.promote.pin(base))
    prior['sampler_config'].update(seed=54933,sampler_base_source=f.promote.pin(base))
    _rehash_edge(f,prior)
    prior_path=f.folder/'prior-training.json';f.write(prior_path,prior)
    source=f.folder/'sampler_v3.py';source.write_text('# independent v3 design\n')
    shard=deepcopy(prior)
    shard.update(seed=54935,sampler_builder_version='3.0',sampler_source=f.promote.pin(source),
        sampler_source_sha256=f.promote.pin(source)['sha256'],prior_training=[],lattice_shards=[],
        sampler_dependencies=[f.promote.pin(base)])
    shard['states']=[]
    for index in range(800):
        state=deepcopy(prior['states'][0]);state['state_id']=f'lattice-{index}'
        state['fixed_point']['x_m']=-9.+index*.01
        shard['states'].append(state)
    shard.update(attempts=800,samples=800)
    shard['coverage']['strata']['deep']=dict(requested=800,evaluated=800,retained=800)
    shard['sampler_config'].update(seed=54935,samples=800,attempts=800,
        phase='train',
        sampler_stage='lattice-shard',training_seeds=[54935],new_lattice_attempts=800,
        shard_index=0,shard_count=1,requests_sha256='fixed-design',
        prior_training=[],lattice_shards=[],sampler_dependencies=[f.promote.pin(base)],
        forbidden_failed_holdout_seeds=[54932,54934])
    _rehash_edge(f,shard)
    shard_path=f.folder/'lattice.json';f.write(shard_path,shard)
    merged=deepcopy(shard)
    merged.update(states=prior['states']+shard['states'],samples=801,attempts=801,
        prior_training=[f.promote.pin(prior_path)],lattice_shards=[f.promote.pin(shard_path)])
    merged['coverage']['strata']['deep']=dict(requested=801,evaluated=801,retained=801)
    merged['sampler_config'].update(samples=801,attempts=801,sampler_stage='merged-training',
        training_seeds=[54933,54935],prior_training=merged['prior_training'],
        lattice_shards=merged['lattice_shards'],prior_training_attempts=1,prior_training_samples=1,
        prior_training_states_sha256=f.data.necessary._digest(prior['states']))
    _rehash_edge(f,merged);f.write(path,merged)
    return SimpleNamespace(**vars(f),prior_path=prior_path,source=source,shard_path=shard_path)


def test_v3_reuses_unchanged_training_once_and_preserves_ancestral_seeds(v3_edge_files):
    f=v3_edge_files;d=f.data
    result=d.necessary.load_edge_populations(f.promote,f.folder,d.raw,d.template)
    training=result['training'][0]
    assert training['samples']==801
    assert training['_edge_training_seeds']==[54933,54935]
    assert len(result['training'])==2  # merged training plus frozen review


@pytest.mark.parametrize('mutation,match',[
    ('holdout','identity|role|training'),('epoch','epoch'),('calibration','calibration'),
    ('source','checksum'),('prior-source','checksum'),('prefix','unchanged|prefix'),('seeds','seed'),
    ('shard','shard|lattice')])
def test_v3_training_lineage_rejects_wrong_phase_stale_or_rewritten_ancestry(
        v3_edge_files,mutation,match):
    f=v3_edge_files;d=f.data;path=f.folder/'necessary-edges-train.json'
    merged=f.promote.read(path)
    prior=f.promote.read(f.prior_path)
    if mutation=='source':
        f.source.write_text('# changed source\n')
    elif mutation=='prior-source':
        (f.folder/'sampler.py').write_text('# changed ancestral producer\n')
    elif mutation in ('holdout','epoch','calibration'):
        if mutation=='holdout': prior.update(phase='holdout',role='independent-edge-holdout')
        elif mutation=='epoch':prior['physical_inputs_end']={'epoch':2}
        else: prior['calibration']['family_calibration_cache_key']='stale'
        f.write(f.prior_path,prior)
        merged['prior_training']=[f.promote.pin(f.prior_path)]
        merged['sampler_config']['prior_training']=merged['prior_training']
    elif mutation=='prefix':merged['states'][0]['fixed_point']['x_m']=1.
    elif mutation=='seeds':merged['sampler_config']['training_seeds']=[54935]
    else:merged['states'][-1]['affine_gm_m']=.1
    _rehash_edge(f,merged);f.write(path,merged)
    with pytest.raises(ValueError,match=match):
        d.necessary.load_edge_populations(f.promote,f.folder,d.raw,d.template)


def _diagnostic_gate():
    path=ROOT/'experiments/linear-model/epoch_requalification/production-1028c/verify.py'
    spec=importlib.util.spec_from_file_location('claude_residual_gate_test',path)
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    return module.load_gate('seawater', 'residual')


@pytest.mark.parametrize('name,admitted,rejected',[
    ('claude-face-pilot-artifacts',8,0),('claude-face-budget-pilot-artifacts',4,4)])
def test_face_diagnostic_pilots_retain_all_observations_and_centroid_exclusions(name,admitted,rejected):
    gate=_diagnostic_gate();necessary,promote=_module('necessary'),_module('promote')
    here=ROOT/'experiments/linear-model/epoch_requalification/production-1028c/seawater'
    raw=promote.read(here/'ordinary/feeder_1700/raw.json')
    prepared=promote.read(here/name/'prepared.json')
    model=SimpleNamespace(station_x_m=np.asarray(prepared['station_x_m']),
        band=SimpleNamespace(**prepared['band']))
    normalized,pins,excluded=gate.pilot_diagnostics(necessary,promote,here/name,raw,model,
        expected_admitted=admitted,reference_path=here/'ordinary/feeder_1700/train-light.json')
    assert normalized['samples']==admitted
    assert len(normalized['list_row_samples'])==admitted
    assert len(excluded)==rejected
    assert len([p for p in pins if Path(p['path']).name.startswith('face-')])==8


@pytest.mark.parametrize('mutation,match',[
    ('epoch','epoch'),('source','checksum'),('state','individual state'),
    ('request','prespecified request'),('retained','retained count')])
def test_face_diagnostic_pilot_refuses_stale_or_inconsistent_capture(monkeypatch,mutation,match):
    gate=_diagnostic_gate();necessary,promote=_module('necessary'),_module('promote')
    here=ROOT/'experiments/linear-model/epoch_requalification/production-1028c/seawater'
    folder=here/'claude-face-budget-pilot-artifacts'
    raw=promote.read(here/'ordinary/feeder_1700/raw.json')
    prepared=promote.read(folder/'prepared.json')
    model=SimpleNamespace(station_x_m=np.asarray(prepared['station_x_m']),
        band=SimpleNamespace(**prepared['band']))
    original_read=promote.read
    def read(path):
        result=original_read(path)
        if path==folder/'summary.json':
            if mutation=='epoch':result['physical_inputs_end']={'stale':True}
            elif mutation=='source':result['sources']['pilot']['sha256']='0'*64
            elif mutation=='request':result['requests'][0]['trim_fraction']=.5
        if mutation=='state' and path==folder/'face-budget-pilot-00.json':
            result['state_id']='altered'
        return result
    monkeypatch.setattr(promote,'read',read)
    expected=3 if mutation=='retained' else 4
    if mutation=='retained':
        # Keep summary's count internally consistent to reach the states check.
        def retained_read(path):
            result=read(path)
            if path==folder/'summary.json': result['admitted']=3
            return result
        monkeypatch.setattr(promote,'read',retained_read)
    with pytest.raises(ValueError,match=match):
        gate.pilot_diagnostics(necessary,promote,folder,raw,model,
            expected_admitted=expected,reference_path=here/'ordinary/feeder_1700/train-light.json')
