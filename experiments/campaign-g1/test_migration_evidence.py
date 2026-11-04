"""Synthetic boundary oracles; no numerical/production qualification is claimed."""
import hashlib
import importlib.util
import json
from pathlib import Path

import pytest


@pytest.fixture
def evidence():
    path=Path(__file__).with_name('migration_evidence.py')
    spec=importlib.util.spec_from_file_location('_migration_evidence_test',path)
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    return module


def frozen(evidence):
    return dict(epoch_id='epoch',source_sha256='synthetic-source',freeze_sha256='synthetic-freeze',
        component_epoch={key:key+'-synthetic' for key in evidence.COMPONENT_KEYS})


def save(path,value):
    path.parent.mkdir(parents=True,exist_ok=True)
    data=json.dumps(value,sort_keys=True).encode();path.write_bytes(data)
    return hashlib.sha256(data).hexdigest()


def codes(report):return {row['code'] for row in report['issues']}


def engine_observation(**values):
    """Engine-shaped serialisation, with gate-added verification and bindings."""
    return dict(verified=True,source='migrated_move_engine',status='applicable',
        ship_lifts_at_or_above_h=0,yard_lifts_at_or_above_h=0,
        rejected_crossing_lifts=0,yard_h_blocking_rehandles=0,binding_actions=0,**values)


def test_missing_postfreeze_evidence_has_specific_nonpassing_codes(evidence):
    report=evidence.evidence_report(frozen(evidence))
    assert not report['complete']
    assert {'M3_INSTANCE_INVENTORY_MISSING','M6_SOLVER_PLAN_INVENTORY_MISSING','M6_SOLVER_PLAN_INVENTORY_EMPTY','M8_D62_COUNTS_MISSING'}<=codes(report)
    pending=evidence.evidence_report(frozen(evidence),required=False)
    assert pending['status']=='pending_post_freeze' and not pending['complete']
    assert 'all_campaign_solver_plans_live_recheck' in pending['requirements']


def test_loaded_catalogue_object_is_the_component_identity(evidence,monkeypatch):
    import stowage.rules.catalogue as catalogue
    from dataclasses import replace
    loaded=catalogue.catalogue_snapshot()
    monkeypatch.setattr(catalogue,'catalogue_snapshot',lambda:replace(loaded,content_sha256='sha256:LOADED_OBJECT'))
    assert evidence.current_component_epoch()['catalogue_loaded_sha256']=='sha256:LOADED_OBJECT'
    with pytest.raises(ValueError,match='M5_COMPONENT_EPOCH_MISMATCH'):evidence.assert_component_epoch(frozen(evidence))


def test_review_stale_d62_observation_is_unknown(evidence):
    rows=[dict(input=dict(rung='tiny',sha256='input'),measurement=dict(d62_crossing=dict(
        verified=True,input_sha256='input',source='migrated_mask',binding_actions=9,
        epoch_id='STALE',source_sha256='WRONG')))]
    assert evidence.d62_coverage(rows,['tiny'])['tiny']['unknown_instances']==1


@pytest.mark.parametrize('field,value',[
    ('source','migrated_mask'),('status','not_applicable'),('status',None),
    ('ship_lifts_at_or_above_h',-1),('ship_lifts_at_or_above_h',True),
    ('yard_lifts_at_or_above_h',-1),('yard_lifts_at_or_above_h',False),
    ('rejected_crossing_lifts',-1),('rejected_crossing_lifts',True),
    ('yard_h_blocking_rehandles',-1),('yard_h_blocking_rehandles',False),
    ('ship_lifts_at_or_above_h',None),('yard_lifts_at_or_above_h',1.0),
    ('rejected_crossing_lifts','0'),('yard_h_blocking_rehandles',None),
    ('binding_actions',1),
])
def test_only_applicable_move_engine_observations_with_valid_counters_count(evidence,field,value):
    epoch=frozen(evidence)
    inp=dict(sha256='input',rung='tiny',ship='feeder_500',port='laem_chabang')
    observation=engine_observation(input_sha256='input',plan_sha256='plan',
        epoch_id='epoch',source_sha256='synthetic-source',component_epoch=epoch['component_epoch'],
        rung='tiny',port='laem_chabang',ship='feeder_500')
    observation[field]=value
    coverage=evidence.d62_coverage([dict(input=inp,plan_sha256='plan',measurement={'d62_crossing':observation})],
        ['tiny'],frozen=epoch,population=[inp])
    assert coverage['tiny']['unknown_instances']==1
    assert not coverage['tiny']['zero_binding_flag']


@pytest.fixture
def bundle(evidence,tmp_path,monkeypatch):
    """Use real checkpoint/inventory parsing; stub ONLY numerical component execution."""
    from stowage.model.fixtures import build_instance
    from stowage.model import MovePlan,CheckReport,PartReport
    from stowage.checker.full import _input_bindings
    import eval.splits
    import eval.splits_epoch
    freeze=frozen(evidence);components=freeze['component_epoch'];calls=[]
    monkeypatch.setattr(evidence,'current_component_epoch',lambda:components)
    monkeypatch.setattr(eval.splits,'load_inventory',lambda:object())
    monkeypatch.setattr(eval.splits_epoch,'validate_epoch_manifest',lambda *a,**kw:None)
    monkeypatch.setattr(evidence,'_instance_admission',lambda *a:None)
    monkeypatch.setattr(evidence,'_full_checked',lambda *a:True)
    instance=build_instance()
    plan=MovePlan(id='synthetic',instance_id=instance.id,call_index=instance.call_index,moves=[])
    def execute(instance,plan):
        calls.append(plan.id)
        report=CheckReport(passed=True,parts={'moves':PartReport(name='moves')},
            provenance={'parents':list(_input_bindings(instance,plan)),
                'versions':{'checker':components['checker_version'],'rule_catalogue':components['catalogue_loaded_sha256']}})
        return report,dict(verified=True,status='complete',global_screen='on',masked_actions=0,mask_version=components['mask_version'],
            d62_crossing=engine_observation())
    monkeypatch.setattr(evidence,'execute_plan',execute)
    root=tmp_path/'instances';source=root/'group/input.json';digest=save(source,instance.model_dump(mode='json'))
    spec=dict(rung='tiny',vessel='feeder_500',port='laem_chabang',generator='random_yard',seed=0)
    inp=dict(path=str(source.resolve()),sha256=digest,rung='tiny',vessel='feeder_500',shard={'split_spec':spec})
    instance_manifest=root/'manifest.json';save(instance_manifest,dict(epoch_id='epoch',freeze_manifest=freeze,
        bindings={'key':dict(instance='group/input.json',sha256=digest,spec=spec)}))
    campaign_root=tmp_path/'campaign';identity=dict(epoch_id='epoch',source_sha256=freeze['source_sha256'],
        freeze_sha256=freeze['freeze_sha256'],component_epoch=components,inputs=[inp])
    identity_path=campaign_root/'campaign-manifest.json';identity_sha=save(identity_path,identity)
    directory=campaign_root/'4.6/one';plan_path=directory/'returned-plan.json';save(plan_path,plan.model_dump(mode='json'))
    result_path=directory/'result.json';save(result_path,dict(attempt_id='attempt',stage='4.6',plan_returned=True))
    campaign=evidence._module('campaign');marker=directory/'done.json'
    campaign.write_done(marker,dict(campaign=identity,job=dict(input=inp)),result_path)
    jobs=campaign.discover_plans(campaign_root,[inp])
    replay_directory=campaign_root/'replay'/jobs[0]['id']
    finalized=replay_directory/'finalized-plan.json';save(finalized,plan.model_dump(mode='json'))
    replay_result=replay_directory/'result.json'
    save(replay_result,dict(attempt_id='replay-attempt',stage='replay',measurement={'finalized_status':'ok'}))
    campaign.write_done(replay_directory/'done.json',dict(campaign=identity,job=jobs[0]),replay_result)
    rows=campaign.migration_plan_records(campaign_root,jobs,identity)
    inventory=campaign_root/'5.4/solver-plans.json'
    value=dict(schema_version=2,epoch_id='epoch',source_sha256=freeze['source_sha256'],freeze_sha256=freeze['freeze_sha256'],
        component_epoch=components,campaign_identity=dict(path=str(identity_path),sha256=identity_sha),records=rows)
    save(inventory,value)
    observation=engine_observation(input_sha256=digest,plan_sha256=rows[0]['plan_sha256'],
        epoch_id='epoch',source_sha256=freeze['source_sha256'],component_epoch=components,rung='tiny',port='laem_chabang',ship='feeder_500')
    observed=[dict(input=inp,plan_sha256=rows[0]['plan_sha256'],measurement=dict(d62_crossing=observation))]
    summary=dict(epoch_id='epoch',solve_yield={'tiny':1},d62_crossing=evidence.d62_coverage(observed,['tiny'],frozen=freeze,population=[inp]),
        d62_zero_acknowledgements={'tiny|laem_chabang|feeder_500':dict(epoch_id='epoch',acknowledged_by='leader')})
    return dict(freeze=freeze,paths=dict(instance_manifest=instance_manifest,campaign_manifest=inventory,summary=summary),
        calls=calls,execute=execute,marker=marker,result=result_path,plan=finalized,original_plan=plan_path,input=source,inp=inp,inventory=value,identity=identity,identity_path=identity_path)


def run(evidence,bundle):return evidence.evidence_report(bundle['freeze'],**bundle['paths'])


def test_exact_complete_solver_inventory_executes_every_plan_once(evidence,bundle):
    report=run(evidence,bundle)
    assert report['complete'],report['issues']
    assert bundle['calls']==['synthetic']
    assert report['counts']['returned_solver_plans']==1
    assert report['counts']['live_rechecked_solver_plans']==1
    assert report['bindings'][str(bundle['paths']['campaign_manifest'].resolve())]


def test_review_forged_serialized_reports_never_prove_execution(evidence,bundle,monkeypatch):
    # Internally consistent serialized pass claims cannot substitute for execution.
    paths=bundle['paths'];paths.pop('campaign_manifest')
    fake=paths['instance_manifest'].parent/'forged.json'
    save(fake,dict(checker_ran=True,mask_ran=True,passed=True,verified=True))
    paths.update(gold_manifest=fake,recheck_manifest=fake)
    report=run(evidence,bundle)
    assert not report['complete'] and 'M6_SOLVER_PLAN_INVENTORY_MISSING' in codes(report)
    assert not bundle['calls']


def test_review_summary_aggregate_cannot_invent_population(evidence,bundle):
    bundle['paths']['summary']['d62_crossing']['tiny'].update(instances=999,observed_instances=999,binding_instances=999,zero_binding_flag=False)
    report=run(evidence,bundle)
    assert not report['complete'] and 'M8_D62_COUNTS_INVALID' in codes(report)
    assert report['d62_crossing']['tiny']['instances']==1


@pytest.mark.parametrize('mutation',['duplicate','missing','deleted','unreadable','unlisted_input','stale_epoch','stale_source','stale_components'])
def test_all_returned_plan_obligations_are_exact_and_bound(evidence,bundle,mutation):
    path=bundle['paths']['campaign_manifest'];inventory=bundle['inventory']
    if mutation=='duplicate':inventory['records']*=2
    elif mutation=='missing':inventory['records']=[]
    elif mutation=='deleted':bundle['plan'].unlink()
    elif mutation=='unreadable':bundle['plan'].write_text('not-json')
    elif mutation=='unlisted_input':inventory['records'][0]['input']['path']=str(bundle['input'].parent/'other.json')
    elif mutation=='stale_epoch':inventory['epoch_id']='old'
    elif mutation=='stale_source':inventory['source_sha256']='old'
    else:inventory['component_epoch']['checker_version']='old'
    save(path,inventory)
    report=run(evidence,bundle)
    assert not report['complete']
    assert 'M6_SOLVER_PLAN_DENOMINATOR' in codes(report) or 'M6_SOLVER_PLAN_RECHECK_FAILED' in codes(report)


def test_empty_solver_population_refuses(evidence,bundle):
    bundle['marker'].unlink();bundle['inventory']['records']=[]
    save(bundle['paths']['campaign_manifest'],bundle['inventory'])
    report=run(evidence,bundle)
    assert {'M6_SOLVER_PLAN_INVENTORY_EMPTY','M6_SOLVER_RUNG_EMPTY'}<=codes(report)


def test_missing_rung_returned_population_refuses(evidence,bundle):
    bundle['paths']['summary']['solve_yield']['full']=1
    assert 'M6_SOLVER_RUNG_EMPTY' in codes(run(evidence,bundle))


@pytest.mark.parametrize('failure',['checker','mask','unknown_observation','old_checker','incomplete_checker'])
def test_live_execution_not_serialized_verdict_decides(evidence,bundle,monkeypatch,failure):
    def execution(instance,plan):
        report,mask=bundle['execute'](instance,plan)
        if failure=='checker':report.passed=False
        elif failure=='mask':mask['masked_actions']=1
        elif failure=='unknown_observation':mask['d62_crossing']=None
        elif failure=='old_checker':report.provenance.versions['checker']='old'
        else:monkeypatch.setattr(evidence,'_full_checked',lambda *a:False)
        return report,mask
    monkeypatch.setattr(evidence,'execute_plan',execution)
    report=run(evidence,bundle)
    assert not report['complete']
    assert 'M8_D62_COUNTS_UNVERIFIED' in codes(report)


def test_zero_requires_explicit_leader_acknowledgement(evidence,bundle):
    bundle['paths']['summary']['d62_zero_acknowledgements']={}
    assert 'M8_D62_ZERO_ACK_REQUIRED' in codes(run(evidence,bundle))


@pytest.mark.parametrize('key,value',[('epoch_id','other'),('acknowledged_by','')])
def test_zero_acknowledgement_must_be_same_epoch_and_named(evidence,bundle,key,value):
    bundle['paths']['summary']['d62_zero_acknowledgements']['tiny|laem_chabang|feeder_500'][key]=value
    assert 'M8_D62_ZERO_ACK_REQUIRED' in codes(run(evidence,bundle))


def test_missing_observations_for_returned_inputs_stay_unknown(evidence):
    freeze=frozen(evidence)
    population=[dict(sha256='a',rung='tiny',ship='feeder_500',port='laem_chabang'),
                dict(sha256='b',rung='tiny',ship='feeder_800',port='penang')]
    coverage=evidence.d62_coverage([],['tiny'],frozen=freeze,population=population)
    assert coverage['tiny']['instances']==2 and coverage['tiny']['unknown_instances']==2
    assert len(coverage['tiny']['by_port_ship'])==2


@pytest.mark.parametrize('field,value',[('epoch_id','stale'),('source_sha256','wrong'),('input_sha256','other'),
    ('plan_sha256','other'),('rung','small'),('port','other'),('ship','feeder_800'),('component_epoch',{}),('verified',False)])
def test_crossing_observation_requires_every_exact_binding(evidence,bundle,field,value):
    inp=bundle['inp'];row=bundle['inventory']['records'][0]
    observed=engine_observation(input_sha256=inp['sha256'],
        plan_sha256=row['plan_sha256'],epoch_id='epoch',source_sha256='synthetic-source',component_epoch=bundle['freeze']['component_epoch'],
        rung='tiny',port='laem_chabang',ship='feeder_500')
    observed[field]=value
    coverage=evidence.d62_coverage([dict(input=inp,plan_sha256=row['plan_sha256'],measurement=dict(d62_crossing=observed))],
        ['tiny'],frozen=bundle['freeze'],population=[inp])
    assert coverage['tiny']['unknown_instances']==1


def test_shared_v2_provenance_guard_is_preserved(evidence,bundle,monkeypatch):
    import eval.splits_epoch
    def refuse(*a,**kw):raise ValueError('untrusted v2/source')
    monkeypatch.setattr(eval.splits_epoch,'validate_epoch_manifest',refuse)
    assert 'M3_INSTANCE_PROVENANCE_INVALID' in codes(run(evidence,bundle))


def test_plan_replaced_during_live_execution_cannot_publish(evidence,bundle,monkeypatch):
    def replace(instance,plan):
        result=bundle['execute'](instance,plan)
        bundle['plan'].write_text('{}')
        return result
    monkeypatch.setattr(evidence,'execute_plan',replace)
    report=run(evidence,bundle)
    assert not report['complete'] and 'M6_EVIDENCE_DRIFT' in codes(report)


@pytest.mark.parametrize('mutation',['deleted','unreadable'])
def test_missing_artifact_remains_in_replay_and_crossing_denominators(evidence,bundle,mutation):
    if mutation=='deleted':bundle['plan'].unlink()
    else:bundle['plan'].write_text('not-json')
    report=run(evidence,bundle)
    assert not report['complete']
    assert report['counts']['returned_solver_plans']==1
    assert report['d62_crossing']['tiny']['instances']==1
    assert report['d62_crossing']['tiny']['unknown_instances']==1


def test_approval_cannot_omit_a_selected_rung(evidence,bundle):
    bundle['paths']['summary']['solve_yield']={}
    assert 'M6_REQUIRED_RUNGS_MISSING' in codes(run(evidence,bundle))


@pytest.mark.parametrize('marker,value',[('diagnostic',True),('evidence_eligible',False),('official',False),('allow_drift',True),('tiny_run',True)])
def test_diagnostic_candidate_inventory_never_counts_as_evidence(evidence,bundle,marker,value):
    bundle['inventory'][marker]=value
    save(bundle['paths']['campaign_manifest'],bundle['inventory'])
    report=run(evidence,bundle)
    assert not report['complete'] and 'M6_SOLVER_PLAN_DENOMINATOR' in codes(report)
    assert not bundle['calls']


@pytest.mark.parametrize('observation_status',['applicable','not_applicable'])
def test_direct_execution_calls_public_checker_and_production_mask(evidence,monkeypatch,observation_status):
    from types import SimpleNamespace
    import stowage.checker
    import stowage.actions
    import stowage.feasibility
    import stowage.moves
    import llm.stowlm.format
    import llm.stowlm.mask
    calls=[];action=object()
    instance=SimpleNamespace(containers=[SimpleNamespace(id='box')]);plan=SimpleNamespace(moves=[object()]);report=object()
    monkeypatch.setattr(stowage.checker,'check',lambda i,p:(calls.append(('check',i,p)) or report))
    monkeypatch.setattr(stowage.actions,'get_action_space',lambda:SimpleNamespace(from_plan=lambda i,p:[action]))
    # Search diagnostics deliberately disagree: they must never become evidence.
    engine=SimpleNamespace(diagnostics={'global_screen':'on','d62_crossing':{'verified':True,'source':'migrated_mask','binding_actions':999}})
    monkeypatch.setattr(stowage.feasibility,'make_feasibility_engine',lambda i,s,config:(calls.append(('engine',config)) or engine))
    from stowage.moves import CrossingObservations
    metric=CrossingObservations(status=observation_status,
        yard_h_blocking_rehandles=2 if observation_status=='applicable' else 0)
    monkeypatch.setattr(stowage.moves,'apply_plan',lambda i,p:(calls.append(('move_observer',i,p)) or SimpleNamespace(legal=True,executed=1,d62_crossing=metric)))
    class Tokenizer:
        def __call__(self):return object()
        def cache_clear(self):calls.append(('tokenizer_fresh',))
    monkeypatch.setattr(llm.stowlm.format,'load_tokenizer',Tokenizer())
    monkeypatch.setattr(llm.stowlm.format,'ActionCodec',lambda *a:SimpleNamespace(encode_action=lambda a:[11],end_plan_tokens=[12]))
    class Decoder:
        def __init__(self,eng,codec,config):
            assert eng is engine;self.codec=codec;self.actions=[];self.status='running'
        def advance(self,token):
            calls.append(('mask_token',token))
            if token==11:self.actions.append(action)
            if token==12:self.status='complete'
    monkeypatch.setattr(llm.stowlm.mask,'MaskedDecoder',Decoder)
    returned,mask=evidence.execute_plan(instance,plan)
    count=2 if observation_status=='applicable' else 0
    assert returned is report and mask['d62_crossing']['binding_actions']==count
    assert mask['d62_crossing']['source']=='migrated_move_engine'
    assert mask['d62_crossing']['status']==observation_status
    assert mask['d62_crossing']['verified'] is (observation_status=='applicable')
    assert ('check',instance,plan) in calls and ('mask_token',11) in calls and ('mask_token',12) in calls
    assert ('move_observer',instance,plan) in calls
    metric.yard_h_blocking_rehandles=99
    assert mask['d62_crossing']['binding_actions']==count  # copied at execution, not a mutable model alias
    frozen_epoch=frozen(evidence)
    inp=dict(sha256='a',rung='tiny',ship='feeder_500',port='laem_chabang')
    observation=dict(mask['d62_crossing'],input_sha256='a',plan_sha256='p',
        epoch_id='epoch',source_sha256='synthetic-source',component_epoch=frozen_epoch['component_epoch'],
        rung='tiny',port='laem_chabang',ship='feeder_500')
    coverage=evidence.d62_coverage([dict(input=inp,plan_sha256='p',measurement={'d62_crossing':observation})],
        ['tiny'],frozen=frozen_epoch,population=[inp])
    assert coverage['tiny']['unknown_instances']==int(observation_status=='not_applicable')


def test_current_instance_admission_checks_resolved_weight_policy(evidence,monkeypatch):
    from types import SimpleNamespace
    import stowage.ports
    calls=[]
    monkeypatch.setattr(stowage.ports,'resolve_port_call_profile',lambda *a,**kw:object())
    def refused(instance,frozen):
        calls.append(instance)
        raise ValueError('instance overrides tolerance to 100 kg')
    monkeypatch.setattr(evidence,'_module',lambda name:SimpleNamespace(enforce_instance_weight_policy=refused))
    instance=SimpleNamespace(schema_version='1.9',port=SimpleNamespace(max_crane_height_tiers=8),rotation=SimpleNamespace(calls=[object()]))
    with pytest.raises(ValueError,match='tolerance'):evidence._instance_admission(instance,{})
    assert calls==[instance]


def test_d62_unsolved_attempts_are_censored_not_fake_unknown_returned_plans(evidence):
    freeze=frozen(evidence)
    attempted=[dict(sha256='a',rung='tiny',ship='feeder_500',port='laem_chabang'),
               dict(sha256='b',rung='tiny',ship='feeder_800',port='penang')]
    obs=engine_observation(input_sha256='a',plan_sha256='p',
        epoch_id='epoch',source_sha256='synthetic-source',component_epoch=freeze['component_epoch'],
        rung='tiny',port='laem_chabang',ship='feeder_500')
    report=evidence.d62_coverage([dict(input=attempted[0],plan_sha256='p',measurement={'d62_crossing':obs})],['tiny'],
        frozen=freeze,population=attempted[:1],attempted_population=attempted)
    tiny=report['tiny']
    assert tiny['instances']==1 and tiny['observed_instances']==1 and tiny['unknown_instances']==0
    assert tiny['attempted_instances']==2 and tiny['not_solved_instances']==1
    assert tiny['status']=='observed'
    censored=next(row for row in tiny['by_port_ship'] if row['port']=='penang')
    assert censored['status']=='not_observed_no_returned_plan' and censored['instances']==0
    assert not censored['zero_binding_flag'] and censored['not_solved_instances']==1


def test_unsolved_allocated_member_does_not_force_hundred_percent_solver_yield(evidence,bundle):
    original=json.loads(bundle['input'].read_text());original['id']='synthetic-unsolved'
    second=bundle['input'].with_name('unsolved.json');second_sha=save(second,original)
    inp=json.loads(json.dumps(bundle['inp']));inp.update(path=str(second),sha256=second_sha)
    inp['shard']['split_spec']['seed']=1
    instance_manifest=bundle['paths']['instance_manifest']
    allocation=json.loads(instance_manifest.read_text())
    allocation['bindings']['unsolved']=dict(instance='group/unsolved.json',sha256=second_sha,spec=inp['shard']['split_spec'])
    save(instance_manifest,allocation)
    bundle['identity']['inputs'].append(inp)
    identity_sha=save(bundle['identity_path'],bundle['identity'])
    for marker in (bundle['marker'],Path(bundle['inventory']['records'][0]['replay_checkpoint'])):
        checkpoint=json.loads(marker.read_text());checkpoint['identity']['campaign']=bundle['identity'];save(marker,checkpoint)
    bundle['inventory']['campaign_identity']['sha256']=identity_sha
    save(bundle['paths']['campaign_manifest'],bundle['inventory'])
    # The leader sees the exact attempted population and one returned input.
    summary=bundle['paths']['summary'];summary['solve_yield']['tiny']=0.5
    row=bundle['inventory']['records'][0]
    obs=engine_observation(
        input_sha256=bundle['inp']['sha256'],plan_sha256=row['plan_sha256'],epoch_id='epoch',
        source_sha256='synthetic-source',component_epoch=bundle['freeze']['component_epoch'],
        rung='tiny',port='laem_chabang',ship='feeder_500')
    summary['d62_crossing']=evidence.d62_coverage([dict(input=bundle['inp'],plan_sha256=row['plan_sha256'],measurement={'d62_crossing':obs})],
        ['tiny'],frozen=bundle['freeze'],population=[bundle['inp']],attempted_population=bundle['identity']['inputs'])
    report=run(evidence,bundle)
    assert report['complete'],report['issues']
    assert report['d62_crossing']['tiny']['instances']==1
    assert report['d62_crossing']['tiny']['attempted_instances']==2
    assert report['d62_crossing']['tiny']['not_solved_instances']==1
    assert report['d62_crossing']['tiny']['unknown_instances']==0
