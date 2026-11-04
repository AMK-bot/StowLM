"""Guard completion conservation, unchanged tiers, provenance and pairing."""
from argparse import Namespace
import hashlib
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace
import sys
from unittest.mock import patch
import pytest

HERE=Path(__file__).resolve().parent


def load(name):
    spec=importlib.util.spec_from_file_location('_c6_test_'+name,HERE/(name+'.py'))
    module=importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_development_selection_was_predeclared_and_inputs_unchanged():
    dev=json.loads((HERE/'DEV.json').read_text())
    assert dev['seeds']==[331401000+12*c+i for c in range(6) for i in range(2)]
    manifest=json.loads((HERE/'cohort/MANIFEST.json').read_text())
    assert len(manifest['maps'])==72
    assert {r['seed'] for r in manifest['maps']}==set(range(331401000,331401072))
    for record in manifest['maps']:
        assert hashlib.sha256((HERE/'cohort'/Path(record['path']).name).read_bytes()).hexdigest()==record['sha256']


def test_leader_completion_and_policy_are_byte_identical():
    leader=HERE.parents[1]/'c5/leader'
    for name in ('completion.py','tiers.toml'):
        assert (HERE/name).read_bytes()==(leader/name).read_bytes()


def report(rule=None, severity='hard'):
    violations=[] if rule is None else [dict(rule_id=rule,severity=severity,measured=1.2,limit=1.,unit='ratio')]
    return dict(passed=not any(v['severity']=='hard' for v in violations),
                count=dict(weighted_nonproductive=9),parts={'x':dict(ran=True,error=None)},
                shut_out=[],violations=violations)


def test_unknown_hard_rule_is_unpeelable_and_soft_is_not_peeled():
    tiers=load('tiers')
    assert tiers.analyze_report(report('unknown.rule'))['level'] is None
    assert tiers.analyze_report(report('unknown.rule','soft'))['level']==0
    analysis=tiers.analyze_report(report('iacs.member_irs.container_securing'))
    assert analysis['level']==4
    assert analysis['broken_rules'][0]['occurrences'][0]['breach']==pytest.approx(.2)
    assert analysis['broken_rules'][0]['occurrences'][0]['breach_unit']=='ratio'


def test_completion_never_promotes_partial_and_no_plan_is_unavailable():
    completion=load('completion')
    rows={'a':dict(completed=None,partial=True,boxes_left_ashore=['box']),
          'b':dict(completed=dict(level=4)),
          'c':dict(passed=True)}
    result=completion.table(rows)
    assert result['counts']['T1']==1
    assert result['counts']['T5']==1
    assert result['not_completed']==1


def test_replay_requires_retained_cargo_and_discharge_absence():
    runtime=load('runner')
    instance=SimpleNamespace(load_list=['new'],discharge_list=['due'],
        onboard=SimpleNamespace(placements=[SimpleNamespace(container_id=x) for x in ('through','due')]))
    def replay(boxes):
        return SimpleNamespace(legal=True,final_state=SimpleNamespace(ship=SimpleNamespace(
            placements=[SimpleNamespace(container_id=x) for x in boxes])))
    assert runtime.complete_replay(instance,replay(['new','through']))
    assert not runtime.complete_replay(instance,replay(['new']))
    assert not runtime.complete_replay(instance,replay(['new','through','due']))


def test_tier_ranking_never_peels_to_save_lifts():
    runtime=load('runner');tiers=load('tiers')
    full=dict(completed=dict(level=0),tier_analysis=tiers.analyze_report(report()))
    relaxed=dict(completed=dict(level=4),tier_analysis=tiers.analyze_report(report('iacs.member_irs.container_securing')))
    relaxed['tier_analysis']['lifts']=0
    assert runtime.candidate_rank(full)<runtime.candidate_rank(relaxed)


def test_paired_lifts_only_mutual_full_passes():
    reporter=load('report')
    records=[dict(path=f'{name}.json',size='small',occupancy_target=.3) for name in ('a','b')]
    def row(identity,arm,passed,lifts):
        return dict(instance_id=identity,method=arm,passed=passed,lifts=lifts,wall_s=1,
            replay_complete=True,parts=dict(restow_now=2,restow_later=3),
            aboard_restows=dict(now_lifts=1,future_lifts=None))
    rows=[row('a','engine',True,20),row('a','layover',True,10),
          row('b','engine',False,None),row('b','layover',True,100)]
    group=reporter.aggregate(rows,records,['engine','layover'])['groups'][2]
    assert group['pairs']['engine']==dict(maps=1,baseline_lifts=20,method_lifts=10)
    assert group['methods']['layover']['mean_lifts']==55
    assert group['methods']['layover']['mean_arrival_later'] is None


def test_work_and_profile_participate_in_cache_identity():
    runtime=load('runner')
    def args(**changes):
        return Namespace(**(dict(budget_s=60,search_s=None,engine_extra_s=None,finalize_s=60,
            tries=2,seed=0,work_config='{}',finishing_config='{}',method_config='{}',profile_sha256='a')|changes))
    assert runtime.cache_config(args())!=runtime.cache_config(args(method_config='{"rounds":2}'))
    assert runtime.cache_config(args())!=runtime.cache_config(args(profile_sha256='b'))


def test_arrival_lp_provenance_pins_tiny_pages_and_frozen_engine():
    runtime=load('runner')
    sources=runtime.provenance('layover')
    prefix=str(HERE.relative_to(runtime.ROOT))+'/'
    assert prefix+'pages_copy/method.py' in sources
    assert prefix+'pages_copy/PROFILE.json' in sources
    assert prefix+'engine_copy/planner.py' in sources
    assert prefix+'engine_copy/engine/planner.py' in sources


def test_observed_recipes_are_checked_only_until_independent_t5_pass():
    runtime=load('runner')
    raw,seeded,rescored=object(),object(),object()
    context=dict(_candidate_plans={('mixed','keys'):seeded},observed_plans=[raw])
    ranked=[(7,0,SimpleNamespace(plan=rescored))]
    assert runtime.diagnostic_plans(context,ranked,2,passed=False)==[seeded,raw,rescored]
    assert runtime.diagnostic_plans(context,ranked,2,passed=True)==[]


def test_fresh_pages_keeps_original_profile_separate_from_lp_work():
    wrapper=load('pages_method')
    context=dict(work_config={'work_bounded':True,'max_lp_calls':40},
                 finishing_config={'max_evaluations':12},observed_plans=[])
    inner=wrapper._context(context)
    original=json.loads((HERE/'pages_copy/PROFILE.json').read_text())
    assert inner['work_config']==original['work_config']
    assert inner['finishing_config']==original['finishing_config']
    assert context['work_config']=={'work_bounded':True,'max_lp_calls':40}
    assert inner['observed_plans'] is context['observed_plans']
    assert context['effective_page_profile']['finalize_s']==60


def test_fresh_pages_preserves_best_checked_failure_without_extra_checks():
    spec=importlib.util.spec_from_file_location('_c6_test_pagefinisher',HERE/'pages_copy/finishing.py')
    finisher=importlib.util.module_from_spec(spec);spec.loader.exec_module(finisher)
    first,second=object(),object()
    def checked(rule):
        payload=report(rule)
        part=SimpleNamespace(violations=[SimpleNamespace(severity='hard')],ran=True,error=None)
        return SimpleNamespace(passed=False,parts={'stability':part},model_dump=lambda **kwargs:payload)
    initial,local=checked('iscode.loading.draft'),checked('iscode.loading.trim')
    context=dict(finishing_config={'max_evaluations':28})
    fake=SimpleNamespace(optimize=lambda *a,**kw:(second,{'calls':28}))
    with patch.object(finisher,'inherited_plan',return_value=first), \
            patch.object(finisher,'_check',side_effect=[(initial,{}),(local,{})]) as checker, \
            patch.dict(sys.modules,{'ballast_local':fake}):
        plan,metadata=finisher.finish(SimpleNamespace(),first,context=context)
    assert plan is second
    assert metadata['passed'] is False and metadata['selected_level']==4
    assert checker.call_count==2
    assert context['observed_plans']==[first,second]


def test_development_split_uses_predeclared_seeds_and_exact_completion_logic():
    reporter,completion=load('report'),load('completion')
    records=[dict(path='a.json',seed=331401000),dict(path='b.json',seed=331401002)]
    rows=[dict(instance_id='a',method='layover',completed=dict(level=4)),
          dict(instance_id='b',method='layover',completed=None,partial=True)]
    split=reporter.subset_completions(rows,['layover'],[331401000],records,completion)
    dev=split['DEV12/Cold arrival LP']
    train=split['Remaining TRAIN60/Cold arrival LP']
    assert dev['maps']==1 and dev['counts']['T1']==1
    assert train['maps']==1 and train['not_completed']==1


def test_new_lp_finishes_higher_tier_before_cheaper_stow_but_pages_keeps_lifts():
    runtime=load('runner')
    def scored(identity):
        move=SimpleNamespace(model_dump_json=lambda:identity)
        return SimpleNamespace(plan=SimpleNamespace(moves=[move]))
    unsafe=(1,0,scored('unsafe'))
    safer=(100,1,scored('safer'))
    context={'_recipe_checks':{('unsafe',):report('iscode.loading.draft'),
                               ('safer',):report('iscode.loading.trim')}}
    assert sorted([unsafe,safer],key=lambda entry:runtime.finishing_rank(entry,arm='layover',context=context))[0] is safer
    assert sorted([unsafe,safer],key=lambda entry:runtime.finishing_rank(entry,arm='pages',context=context))[0] is unsafe
    unknown=(0,2,scored('not-checked'))
    assert runtime.finishing_rank(safer,arm='layover',context=context)<runtime.finishing_rank(unknown,arm='layover',context=context)


def test_non_t5_replay_diagnostics_exclude_passes_and_partial_keep_unknown_restows():
    reporter=load('report')
    base=dict(size='small',occupancy=.3,method='layover',replay_complete=True,
              passed=False,completed=dict(level=4),diagnostic_lifts=30,wall_s=100,
              aboard_restows=dict(now_lifts=4,future_lifts=6))
    rows=[base,base|dict(diagnostic_lifts=50,aboard_restows=dict(now_lifts=8,future_lifts=None)),
          base|dict(passed=True,diagnostic_lifts=1),
          base|dict(replay_complete=False,completed=None,partial=True,diagnostic_lifts=0)]
    group=next(row for row in reporter.replay_diagnostics(rows,[],['layover'])
               if row['size']=='small' and row['occupancy']==.3)
    assert group['maps']==2 and group['cost_maps']==2
    assert group['completion_levels']['4']==2
    assert group['mean_diagnostic_lifts']==40
    assert group['mean_arrival_now']==6
    assert group['mean_arrival_later'] is None


def test_report_distinguishes_unavailable_comparator_replay_from_zero_complete():
    reporter=load('report')
    records=[dict(path='a.json',size='small',occupancy_target=.3)]
    row=dict(instance_id='a',method='engine',size='small',occupancy=.3,
             replay_complete=None,passed=False,wall_s=10)
    group=reporter.aggregate([row],records,['engine'])['groups'][2]['methods']['engine']
    assert group['replay_complete']==0
    assert group['replay_unavailable']==1


def test_new_lp_audits_selected_cached_originals_only_when_finishing_does_not_pass():
    runtime=load('runner')
    def plan(identity):
        return SimpleNamespace(moves=[SimpleNamespace(model_dump_json=lambda:identity)],
                               model_dump=lambda **kwargs:{'identity':identity})
    raw,recipe,other=plan('raw'),plan('recipe'),plan('other')
    ranked=[(1,0,SimpleNamespace(plan=raw)),(2,1,SimpleNamespace(plan=raw)),
            (3,2,SimpleNamespace(plan=other))]
    context=dict(_observed_dominated_by_candidates=True,
                 _recipes={('raw',):recipe,('other',):other},observed_plans=[raw,recipe,other])
    assert runtime.diagnostic_plans(context,ranked,2,passed=False)==[recipe]
    assert runtime.diagnostic_plans(context,ranked,2,passed=True)==[]
    assert runtime.diagnostic_plans(context,ranked,2,passed=False,arm='pages')==[raw,recipe,other,raw,raw]


def test_archived_pages_validate_frozen_bytes_inputs_native_sources_and_old_work(tmp_path):
    reporter=load('report')
    input_file=tmp_path/'a.json';input_file.write_text('{}')
    digest=hashlib.sha256(input_file.read_bytes()).hexdigest()
    record=dict(path='original/a.json',sha256=digest,seed=331401024,size='small',occupancy_target=.3)
    native={'src/stowage/example.py':'native','params/example.toml':'params'}
    page_profile=json.loads((HERE/'pages_copy/PROFILE.json').read_text())
    config=dict(budget_s=60.,work_config=page_profile['work_config'],
        finishing_config=page_profile['finishing_config'],**page_profile['native_settings'])
    row=dict(instance_id='a',input='original/a.json',input_sha256=digest,
             source_sha256=native,complete=True,method='layover',size='small',occupancy=.3,
             passed=False,replay_complete=True,config=config)
    path=tmp_path/'archived.json';path.write_text(json.dumps(row))
    frozen=dict(input_sha256={str(record['seed']):digest},archived_pages=dict(
        rows_sha256={'a.json':hashlib.sha256(path.read_bytes()).hexdigest()},native_source_sha256=native))
    runtime=SimpleNamespace(provenance=lambda arm:native,input_path=lambda item:input_file)
    result=reporter.validate_archived_pages({'a.json':row},{'a.json':path},[record],frozen,runtime)
    assert result['failed_tier_reports']=='unavailable'
    assert result['native_checker_and_params_match'] is True
    with pytest.raises(ValueError,match='native checker/parameters differ'):
        reporter.validate_archived_pages({'a.json':row|dict(source_sha256={})},{'a.json':path},[record],frozen,runtime)
    path.write_text('{}')
    with pytest.raises(ValueError,match='changed since freeze'):
        reporter.validate_archived_pages({'a.json':row},{'a.json':path},[record],frozen,runtime)


def test_profile_launcher_rejects_solver_overrides_including_equals_and_abbreviations():
    launcher=load('run')
    launcher.validate_selectors(['--methods','frontier09','engine','layover','--out=results/primary',
                                 '--map-seed','331401024','--shard-index=1','--shard-count','2','--dev'])
    for arguments in (['--finalize-s','999'],['--seed=4'],['--work-config={}'],
                      ['--method-config','{}'],['--budget-s=1'],['--profile-sha256','forged'],
                      ['--finalize','999'],['--worker']):
        with pytest.raises(ValueError,match='cannot be overridden'):
            launcher.validate_selectors(arguments)


def test_three_requested_fresh_arms_use_each_order_twice_and_four_arm_case_is_preserved():
    runtime=load('runner')
    fresh=['frontier09','engine','layover']
    for start in range(331401000,331401072,12):
        orders=[tuple(runtime.arm_order(dict(seed=seed),fresh)) for seed in range(start,start+12)]
        assert len(set(orders))==6
        assert all(orders.count(order)==2 for order in orders)
    for index in range(12):
        offset=index % 4
        old=list(runtime.ARMS[offset:]+runtime.ARMS[:offset])
        assert runtime.arm_order(dict(seed=331401000+index))==old
        assert runtime.arm_order(dict(seed=331401000+index),list(runtime.ARMS))==old


def test_pause_marker_defers_only_new_development_invocations(tmp_path):
    launcher=load('run');marker=tmp_path/'PAUSE-DEV'
    assert not launcher.development_deferred(['--dev','--map-seed','331401024'],marker)
    marker.write_text('New KG/CP profile pending; active workers finish normally.')
    assert launcher.development_deferred(['--dev','--map-seed','331401024'],marker)
    assert not launcher.development_deferred(['--methods','engine','--out','results/primary'],marker)


def test_all_arms_pin_current_profile_launcher_source():
    runtime=load('runner')
    key=str((HERE/'run.py').relative_to(runtime.ROOT))
    digest=hashlib.sha256((HERE/'run.py').read_bytes()).hexdigest()
    for arm in runtime.ARMS:
        assert runtime.provenance(arm)[key]==digest
