"""Promotion glue must preserve signed/station evidence and reject partial work."""
from copy import deepcopy
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest


@pytest.fixture
def promote():
    path=Path(__file__).resolve().parents[2]/'experiments/linear-model/epoch_requalification/promote.py'
    spec=importlib.util.spec_from_file_location('epoch_promote_test',path)
    module=importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_station_envelopes_take_individual_maxima(promote):
    from stowage.linear.margin_policy import _zeros
    first,second=_zeros(2),_zeros(2)
    first['lower_unsafe']['sf_n']=[5.,1.]
    second['lower_unsafe']['sf_n']=[2.,8.]
    first['upper_unsafe']['sf_n']=[1.,7.]
    second['upper_unsafe']['sf_n']=[4.,3.]
    result=promote.merge_envelopes([first,second],2)
    assert result['lower_unsafe']['sf_n']==[5.,8.]
    assert result['upper_unsafe']['sf_n']==[4.,7.]
    assert first['lower_unsafe']['sf_n']==[5.,1.]
    second['lower_unsafe']['sf_n']=[float('nan'),8.]
    with pytest.raises(ValueError,match='invalid'):
        promote.merge_envelopes([first,second],2)


def test_observation_rejects_stale_or_uncrosschecked_epoch(promote):
    diagnostics={name:name for name in ('family_calibration_cache_key',
        'response_calibration_identity','reference_identity','family_domain')}
    raw=dict(physical_inputs={'reference':'current'},diagnostics=diagnostics)
    record=dict(ship='feeder_500',level='light',phase='holdout',samples=64,seed=9,
        diagnostics=dict(samples=64,seed=9),physical_inputs_unchanged=True,
        physical_inputs_start=raw['physical_inputs'],physical_inputs_end=raw['physical_inputs'],
        public_crosscheck_completed=True,calibration=deepcopy(diagnostics))
    promote.validate_observation(record,raw,'feeder_500','light','holdout')
    stale=deepcopy(record)
    stale['physical_inputs_end']['reference']='old'
    with pytest.raises(ValueError,match='physical inputs'):
        promote.validate_observation(stale,raw,'feeder_500','light','holdout')
    record['public_crosscheck_completed']=False
    with pytest.raises(ValueError,match='incomplete'):
        promote.validate_observation(record,raw,'feeder_500','light','holdout')


def test_table_separates_unsafe_gm_from_conservatism(promote):
    from stowage.linear.margin_policy import _zeros
    signed=_zeros(2)
    signed['lower_unsafe']['gm_m']=.001
    signed['upper_unsafe']['gm_m']=2.
    signed['lower_conservatism']={'gm_m':2.}
    signed['p99']=deepcopy(signed)
    signed['p99']['lower_conservatism']['gm_m']=1.8
    record=dict(samples=64,seed=8,band={},diagnostics=dict(signed_errors=signed))
    result=promote.table_row('feeder_1700','full',record)
    assert result['gm_m']['unsafe_max']==.001
    assert result['gm_m']['conservatism_max']==2.
    assert result['gm_m']['conservatism_p99']==1.8
    assert 'draught_m' in result
    rendered=promote.markdown([result])
    assert 'GM safe gap max m' in rendered
    assert 'GM safe gap p99 m' in rendered
    assert 'Draught unsafe m' in rendered


def test_apply_refuses_partial_fleet_and_reports_every_ship(promote,tmp_path,monkeypatch):
    bank=tmp_path/'bank.json'
    bank.write_text(json.dumps(dict(format=1,entries={'historical':{'keep':True}})))
    before=bank.read_bytes()
    def incomplete(ship,*args,**kwargs):
        return None,[ship+' missing qualification'],[]
    monkeypatch.setattr(promote,'build_ship',incomplete)
    monkeypatch.setattr(promote.sys,'argv',['promote','--input',str(tmp_path),
        '--bank',str(bank),'--apply'])
    assert promote.main()==1
    assert bank.read_bytes()==before
    report=json.loads((tmp_path/'promotion/report.json').read_text())
    assert set(report['failures'])==set(promote.SHIPS)
    assert report['applied'] is False
    assert report['passed'] is False


def test_apply_rechecks_application_identity_after_final_live_builds(promote,tmp_path,monkeypatch):
    from stowage.linear import margin_policy as policy
    state={'application':'measured-application'}
    monkeypatch.setattr(policy,'policy_application_identity',lambda:state['application'])
    monkeypatch.setattr(policy,'_evidence',lambda pins:None)
    def candidate(ship,folder,**kwargs):
        if kwargs.get('live'):
            state['application']='changed-application'
        return (ship,dict(policy_application_identity=state['application'],
                           evidence=[],boundary_list={'evidence':[]})),[],[{}, {}, {}]
    monkeypatch.setattr(promote,'build_ship',candidate)
    monkeypatch.setattr(promote,'markdown',lambda rows:'synthetic promoter transaction\n')
    bank=tmp_path/'bank.json'
    promote.write(bank,dict(format=1,entries={'historical':{'keep':True}}))
    before=bank.read_bytes()
    monkeypatch.setattr(promote.sys,'argv',['promote','--input',str(tmp_path),
        '--bank',str(bank),'--apply'])
    assert promote.main()==1
    assert bank.read_bytes()==before
    report=promote.read(tmp_path/'promotion/report.json')
    assert not report['applied']
    assert 'application' in report['apply_rejected']


@pytest.fixture
def measured_epoch(promote,tmp_path,monkeypatch):
    from stowage.linear.margin_policy import _zeros
    import stowage.linear.cache as cache
    import stowage.linear.family_calibration as calibration
    template=SimpleNamespace(station_x_m=np.array([0.,10.]),water_density_kg_m3=1025.,
        max_list_rad=.03,band=SimpleNamespace(max_abs_heel_rad=.03),gm_min_m=.15)
    monkeypatch.setattr(cache,'load_model',lambda key:template)
    monkeypatch.setattr(calibration,'_validated_errors',lambda *args:None)
    monkeypatch.setattr(calibration,'_validate_calibration_certificate',lambda *args:None)
    envelope=_zeros(2)
    for side in promote.SIDES:
        for metric in envelope[side]:
            envelope[side][metric]=[10.,10.] if metric in ('sf_n','bm_nm') else .01
    signed=deepcopy(envelope)
    signed['lower_conservatism']={'gm_m':.4}
    signed['p99']=deepcopy(signed)
    reference=dict(qualified=True,method=1)
    inputs=dict(reference=reference)
    diag=dict(family_calibration_cache_key='key',response_calibration_identity='response',
        coefficient_family_cache_key='coeff',reference_identity=reference,family_domain={},
        signed_errors=signed,samples=192,calibration_certificate={})
    raw=dict(ship='feeder_500',key='key',physical_inputs=inputs,diagnostics=diag,errors={})
    promote.write(tmp_path/'raw.json',raw)
    for phase,seed in (('train',7),('holdout',8)):
        for level in promote.LEVELS:
            measured=deepcopy(signed)
            if phase=='holdout':
                measured['lower_unsafe']['sf_n']=[9.,10.9]
            record=dict(ship='feeder_500',phase=phase,level=level,samples=128,seed=seed,
                band=dict(max_abs_heel_rad=.03),physical_inputs_unchanged=True,
                physical_inputs_start=inputs,physical_inputs_end=inputs,calibration=diag,
                diagnostics=dict(samples=128,seed=seed,signed_errors=measured),
                station_x_m=[0.,10.],public_crosscheck_completed=True,
                public_checker_comparison=dict(affine_solver_unsafe=envelope),
                reference_transfer=_zeros(2))
            promote.write(tmp_path/f'{phase}-{level}.json',record)
    evidence=tmp_path/'boundary-evidence.json'
    evidence.write_text('explicit synthetic test evidence')
    boundary=dict(format=1,kind='boundary-moment-kgm',response_identity='response',
        reference_identity=reference,domain={},station_x_m=[0.,10.],water_density_kg_m3=1025.,
        limit_rad=.03,gm_min_m=.15,measured_unsafe_kgm={s:0. for s in promote.SIDES},
        reference_transfer_kgm={s:0. for s in promote.SIDES},evidence=[promote.pin(evidence)],
        qualification=dict(independent_holdout_passed=True,branch_monotonicity_checked=True,
            calibration_seed=1,holdout_seed=2,calibration_samples={s:1 for s in promote.SIDES},
            holdout_samples={s:1 for s in promote.SIDES},max_holdout_excess_kgm={s:0. for s in promote.SIDES}))
    from stowage.linear import boundary_calibration,margin_policy
    boundary['builder_version']=boundary_calibration.BOUNDARY_CALIBRATION_VERSION
    boundary['builder_inputs_hash']=margin_policy._boundary_builder_inputs_hash(boundary)
    boundary['evidence'].append(promote.pin(boundary_calibration.__file__))
    promote.write(tmp_path/'boundary-certificate.json',boundary)
    promote.write(tmp_path/'equivalence.json',dict(passed=True,fresh_numeric_equal=True,
        physical_inputs=inputs,response_identity='response'))
    return tmp_path


def test_complete_epoch_keeps_station_reserves_and_holdout_evidence(promote,measured_epoch):
    entry,issues,rows=promote.build_ship('feeder_500',measured_epoch,live=False)
    assert issues==[]
    assert len(rows)==3
    assert entry[0]=='response'
    from stowage.linear.margin_policy import policy_application_identity
    assert entry[1]['policy_application_identity']==policy_application_identity()
    assert entry[1]['physical_unsafe']['lower_unsafe']['sf_n']==[10.,10.]
    assert entry[1]['holdout_qualification']['holdout_unsafe']['lower_unsafe']['sf_n']==[9.,10.9]


def test_one_strength_station_breach_refuses_qualification(promote,measured_epoch):
    path=measured_epoch/'holdout-light.json'
    record=json.loads(path.read_text())
    record['diagnostics']['signed_errors']['lower_unsafe']['sf_n']=[9.,11.01]
    promote.write(path,record)
    _,issues,rows=promote.build_ship('feeder_500',measured_epoch,live=False)
    assert len(rows)==3
    assert any('lower_unsafe/sf_n@10' in issue for issue in issues)


def test_retained_failure_enriches_only_calibration_and_new_seed_qualifies(promote,measured_epoch):
    failed=promote.read(measured_epoch/'holdout-light.json')
    failed['seed']=failed['diagnostics']['seed']=9
    failed['diagnostics']['signed_errors']['lower_unsafe']['sf_n']=[9.,12.]
    promote.write(measured_epoch/'enrichment-light.json',failed)
    before=(measured_epoch/'enrichment-light.json').read_bytes()
    raw=promote.read(measured_epoch/'raw.json')
    raw['diagnostics']['seed']=6
    promote.write(measured_epoch/'raw.json',raw)
    entry,issues,_=promote.build_ship('feeder_500',measured_epoch,live=False)
    assert issues==[]
    qualification=entry[1]['holdout_qualification']
    assert qualification['calibration_seeds']==[6,7,9]
    assert qualification['calibration_seed']==7
    assert qualification['holdout_seed']==8
    assert qualification['calibration_samples']==4*128
    assert entry[1]['physical_unsafe']['lower_unsafe']['sf_n']==[10.,12.]
    assert (measured_epoch/'enrichment-light.json').read_bytes()==before
    failed['seed']=failed['diagnostics']['seed']=8
    promote.write(measured_epoch/'enrichment-light.json',failed)
    _,issues,_=promote.build_ship('feeder_500',measured_epoch,live=False)
    assert any('overlaps calibration enrichment' in issue for issue in issues)


def test_multiple_enrichment_populations_count_once_and_duplicate_refuses(promote,measured_epoch):
    first=promote.read(measured_epoch/'holdout-light.json')
    first['seed']=first['diagnostics']['seed']=9
    first['diagnostics']['signed_errors']['lower_unsafe']['sf_n']=[12.,10.]
    second=deepcopy(first)
    second['seed']=second['diagnostics']['seed']=10
    second['diagnostics']['signed_errors']['lower_unsafe']['sf_n']=[10.,13.]
    promote.write(measured_epoch/'enrichment-light.json',first)
    promote.write(measured_epoch/'enrichment-2-light.json',second)
    entry,issues,_=promote.build_ship('feeder_500',measured_epoch,live=False)
    assert issues==[]
    assert entry[1]['physical_unsafe']['lower_unsafe']['sf_n']==[12.,13.]
    qualification=entry[1]['holdout_qualification']
    assert qualification['calibration_seeds']==[7,9,10]
    assert qualification['calibration_samples']==5*128
    assert entry[1]['calibration_enrichment']['samples']==2*128
    promote.write(measured_epoch/'enrichment-copy-light.json',first)
    _,issues,_=promote.build_ship('feeder_500',measured_epoch,live=False)
    assert any('duplicate enrichment seed/case population' in issue for issue in issues)


def test_targeted_calibration_retains_truthful_phase_and_distinct_role(promote,measured_epoch):
    record=promote.read(measured_epoch/'train-light.json')
    record['seed']=record['diagnostics']['seed']=11
    record['sampling_kind']='targeted calibration: minimum ballast at signed trim frontiers'
    record['independent_holdout']=False
    record['diagnostics']['signed_errors']['upper_unsafe']['trim_m']=.04
    promote.write(measured_epoch/'targeted-frontier-light.json',record)
    entry,issues,_=promote.build_ship('feeder_500',measured_epoch,live=False)
    assert issues==[]
    assert entry[1]['targeted_calibration']['seeds']==[11]
    assert entry[1]['holdout_qualification']['calibration_seeds']==[7,11]
    assert entry[1]['physical_unsafe']['upper_unsafe']['trim_m']==.04
    assert any(pin.get('role')=='targeted frontier calibration' for pin in entry[1]['evidence'])
    record['phase']='holdout'
    promote.write(measured_epoch/'targeted-frontier-light.json',record)
    _,issues,_=promote.build_ship('feeder_500',measured_epoch,live=False)
    assert any('targeted/targeted-frontier-light.json' in issue for issue in issues)


def test_targeted_d30_identity_binds_archived_source_and_exact_config(promote,tmp_path,monkeypatch):
    monkeypatch.setattr(promote,'HERE',tmp_path)
    source='TARGETED_CALIBRATION_VERSION="1.0"\n'
    import hashlib
    source_sha=hashlib.sha256(source.encode()).hexdigest()
    archive=tmp_path/f'targeted-sampler-{source_sha[:12]}.py'
    archive.write_text(source)
    config=dict(ship='feeder_800',level='light',seed=3823,samples=256)
    record=dict(**config,physical_inputs_start={'current':'physics'},
        sampler_builder_version='1.0',sampler_source_sha256=source_sha,sampler_config=config)
    inputs=dict(physical_inputs=record['physical_inputs_start'],config=config,
        builder_version='1.0',source_sha256=source_sha)
    record['sampler_inputs_hash']=hashlib.sha256(json.dumps(inputs,
        sort_keys=True,separators=(',',':')).encode()).hexdigest()
    evidence=promote.validate_sampler_identity(record)
    assert evidence['sha256']==source_sha
    record['sampler_inputs_hash']='wrong'
    with pytest.raises(ValueError,match='inputs hash'):
        promote.validate_sampler_identity(record)
    record['sampler_inputs_hash']=hashlib.sha256(json.dumps(inputs,
        sort_keys=True,separators=(',',':')).encode()).hexdigest()
    archive.write_text('changed source')
    with pytest.raises(ValueError,match='archived source'):
        promote.validate_sampler_identity(record)


@pytest.fixture
def reference_evidence(promote,tmp_path,monkeypatch):
    monkeypatch.setattr(promote,'ROOT',tmp_path)
    source=tmp_path/'error_statistics.py'
    source.write_text("L5_TOLERANCES = {'gm_m': (.05,.01)}\n")
    manifest=tmp_path/'manifest.json'
    manifest.write_text('{}')
    certificate=tmp_path/'tank.qualified.json'
    certificate.write_text('{}')
    fractions=dict(gz=.1,area=.09,gm=.016128865447977224)
    folder=tmp_path/'qualification/feeder_800'
    folder.mkdir(parents=True)
    loaded=dict(complete=True,all_passed=True,compared_conditions=8,
        cases=[dict(passed=True) for _ in range(8)],
        empirical_error_budget=dict(combined=fractions),manifest='manifest.json')
    promote.write(folder/'liquid-loaded.json',loaded)
    for name in ('kn-evidence.json','liquid-component.json'):
        promote.write(folder/name,{})
    report=tmp_path/'final_report.json'
    promote.write(report,dict(qualification=dict(feeder_800=dict(
        combined_l5_fraction=fractions,loaded_cases=8))))
    raw=dict(diagnostics=dict(reference_inputs=dict(files={str(path):promote.sha(path)
        for path in (manifest,certificate)})))
    return report,source,raw,certificate


def test_reference_floor_covers_known_combined_gm_error(promote,reference_evidence):
    report,source,raw,_=reference_evidence
    allowance=promote.reference_gm_floor('feeder_800',raw,report_path=report,l5_source=source)
    assert allowance['gm_m']==pytest.approx(.0008064432723988612)
    assert allowance['loaded_cases']==8
    assert allowance['absolute_branch_limit_m']==5.
    assert len(allowance['evidence'])>=6
    raw['diagnostics']['reference_inputs']['files']={
        str(Path(name).relative_to(promote.ROOT)):digest for name,digest in
        raw['diagnostics']['reference_inputs']['files'].items()}
    relative=promote.reference_gm_floor('feeder_800',raw,report_path=report,l5_source=source)
    assert relative['gm_m']==allowance['gm_m']


def test_relative_gm_branch_cannot_cross_loading_minimum(promote,reference_evidence):
    report,source,raw,_=reference_evidence
    allowance=promote.reference_gm_floor('feeder_800',raw,report_path=report,l5_source=source)
    # Worst allowed fraction .2 is stricter than these actual measurements.
    gm=np.array([5.,6.,10.,100.])
    error=.2*.01*gm
    assert np.all(gm-error>.15)
    assert np.all(-gm+error<.15)
    assert allowance['relative_branch_minimum_positive_gm_m']>4.99
    with pytest.raises(ValueError,match='relative GM error branch'):
        promote.reference_gm_floor('feeder_800',raw,report_path=report,l5_source=source,gm_min_m=5.)


def test_reference_floor_rejects_unqualified_or_stale_evidence(promote,reference_evidence):
    report,source,raw,certificate=reference_evidence
    certificate.write_text('{"changed":true}')
    with pytest.raises(ValueError,match='certificate changed'):
        promote.reference_gm_floor('feeder_800',raw,report_path=report,l5_source=source)
    raw['diagnostics']['reference_inputs']['files'][str(certificate)]=promote.sha(certificate)
    content=promote.read(report)
    content['qualification']['feeder_800']['combined_l5_fraction']['area']=.201
    promote.write(report,content)
    with pytest.raises(ValueError,match='qualified cases'):
        promote.reference_gm_floor('feeder_800',raw,report_path=report,l5_source=source)


def test_apply_refuses_small_pilot_populations(promote,measured_epoch,monkeypatch):
    for phase in ('train','holdout'):
        for level in promote.LEVELS:
            path=measured_epoch/f'{phase}-{level}.json'
            record=promote.read(path)
            record['samples']=record['diagnostics']['samples']=4
            promote.write(path,record)
    original=promote.build_ship
    def offline(ship,folder,**kwargs):
        # Exercise the production sample-count gate without production geometry.
        kwargs.pop('reference_error_report',None)
        kwargs['live']=False
        return original(ship,measured_epoch,**kwargs)
    monkeypatch.setattr(promote,'build_ship',offline)
    bank=measured_epoch/'bank.json'
    promote.write(bank,dict(format=1,entries={'historical':{'preserve':True}}))
    before=bank.read_bytes()
    monkeypatch.setattr(promote.sys,'argv',['promote','--input',str(measured_epoch),
        '--bank',str(bank),'--apply'])
    assert promote.main()==1
    assert bank.read_bytes()==before
    report=promote.read(measured_epoch/'promotion/report.json')
    issues=report['failures']['feeder_500']
    assert sum('production promotion requires 128 samples' in issue for issue in issues)==3
    assert sum('production promotion requires 64 samples' in issue for issue in issues)==3
    assert sum('production promotion requires 64 calibration_samples' in issue for issue in issues)==2
    assert sum('production promotion requires 64 holdout_samples' in issue for issue in issues)==2
    assert not report['applied']
