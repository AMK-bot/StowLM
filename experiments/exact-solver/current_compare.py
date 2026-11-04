"""Fresh paired exact/scalable measurements on frozen train/validation inputs.

Selection precedes generation and every outcome. Both planners see the exact
same saved bytes and public feasibility configuration. Search, finalization,
checking and replay times are separate. C2-off is an explicit diagnostic model;
a full-checker witness remains a physical reference, never production G1 gold.

The independent tiny oracle supplies a physical relaxation bound plus an actual
checker witness. Equality certifies weighted lifts only. An optimal witness with
no overrides shows an optimum exists without them; an override in one witness
never establishes that every optimum needs it. No identical public-v2 domains
are compared to answer the override question.
"""
from __future__ import annotations

import argparse
from dataclasses import asdict, fields
from hashlib import sha256
import json
import os
from pathlib import Path
import resource
import time

for name in ('OPENBLAS_NUM_THREADS','OMP_NUM_THREADS','VECLIB_MAXIMUM_THREADS','MKL_NUM_THREADS','NUMEXPR_NUM_THREADS'):
    os.environ[name]='1'

SCOPE=Path(__file__).resolve().parent
PROJECT=SCOPE.parents[1]


def save(path, value):
    path.parent.mkdir(parents=True,exist_ok=True)
    temporary=path.with_suffix(path.suffix+'.partial')
    temporary.write_text(json.dumps(value,sort_keys=True,indent=2,allow_nan=False)+'\n')
    temporary.replace(path)


def digest(path):return sha256(path.read_bytes()).hexdigest()


def fingerprint(*, generation=False):
    value=sha256()
    paths=[*PROJECT.joinpath('src/stowage').rglob('*.py'),*PROJECT.joinpath('src/eval').glob('splits.py'),
           *PROJECT.joinpath('params').rglob('*.toml'),*PROJECT.joinpath('src/stowage/rules').rglob('*.toml'),
           *PROJECT.joinpath('src/stowage/rules/imdg/data').rglob('*.json')]
    if not generation:paths.append(Path(__file__).resolve())
    for path in sorted(set(paths)):
        if generation and PROJECT.joinpath('src/stowage/solver') in path.parents:continue
        value.update(str(path.relative_to(PROJECT)).encode());value.update(path.read_bytes())
    return value.hexdigest()



def input_physics(entry,audit):
    physics=audit.get('generator_audit',{}).get('physics')
    if physics:return physics
    if 'source_shard' in entry:
        source=entry['source_shard'];path=Path(source['root'])/source['relative']
        original=json.loads(path.with_suffix('.audit.json').read_text())
        return original.get('physics',{})
    return {}


def physics_source_comparison(entry,audit):
    physics=input_physics(entry,audit)
    prefixes=('src/stowage/vessel/','src/stowage/model/','src/stowage/checker/',
              'src/stowage/rules/','src/stowage/feasibility/','src/stowage/moves/',
              'src/stowage/actions/','params/fleet/','params/ports/')
    selected={path:value for path,value in physics.get('sources',{}).items()
              if path.startswith(prefixes) or path in ('src/stowage/conventions.py','src/stowage/params.py')}
    changed=[path for path,value in selected.items() if digest(PROJECT/path)!=value]
    return dict(original_physics_sha256=physics.get('sha256'),checked_source_count=len(selected),
                changed_physical_sources=changed,physical_source_match=not changed if selected else None)

def select(args):
    from eval.splits import InstanceSpec,canonical_spec,spec_key,split_of
    rows=[]
    canonical_candidates=None
    available={}
    if args.canonical_v3:
        from stowage.generate.instances_v3 import scenario_specs,physics_metadata
        from stowage.generate.instances_v2 import split_metadata
        canonical_candidates=scenario_specs(profiles=['tiny','small'],generators=['random_yard'],c2_screened=False)
        physics=physics_metadata()
        manifest_path=args.canonical_v3/'manifest.json'
        if manifest_path.exists():
            source_manifest=json.loads(manifest_path.read_text())
            for group,data in source_manifest['groups'].items():
                for shard in data['shards']:
                    if shard.get('split') in ('train','val'):
                        available[shard['spec_key']]={'root':str(args.canonical_v3),'relative':str(Path(group)/shard['instance']),
                            'sha256':shard['sha256'],'manifest_sha256':digest(manifest_path)}
    for rung,quotas in [('tiny',{'train':args.tiny_count}),
                        ('small',{'train':args.small_train,'val':args.small_val})]:
        remaining=dict(quotas)
        if canonical_candidates is not None:
            for generation_spec in canonical_candidates:
                if generation_spec['profile']!=rung:continue
                meta=split_metadata(generation_spec);split=meta['split']
                if split not in remaining or not remaining[split]:continue
                generation_spec={**generation_spec,'physics_sha256':physics['sha256']}
                entry=dict(rung=rung,spec=meta['split_spec'],spec_key=meta['spec_key'],split=split,generator_spec=generation_spec)
                if entry['spec_key'] in available and not args.fresh_canonical:entry['source_shard']=available[entry['spec_key']]
                rows.append(entry);remaining[split]-=1
                if not any(remaining.values()):break
            if any(remaining.values()):raise ValueError(f'not enough canonical train/validation specifications: {remaining}')
            continue
        seed=args.seed_start+(100000 if rung=='small' else 0)
        while any(remaining.values()):
            spec=InstanceSpec(generator=args.generator,rung=rung,slice=rung,vessel=args.vessel,
                port=args.port,seed=seed,blocking_ratio=args.control if args.generator=='random_yard' else None,
                noise=args.control if args.generator=='simulated' else None)
            seed+=1;split=split_of(spec)
            if split not in remaining or not remaining[split]:continue
            rows.append(dict(rung=rung,spec=canonical_spec(spec),spec_key=spec_key(spec),split=split))
            remaining[split]-=1
    manifest=dict(format='exact-scalable-fresh-v1',selection_policy='split before generation; no outcome selection',
                  requested=dict(tiny_train=args.tiny_count,small_train=args.small_train,small_val=args.small_val),
                  entries=rows,created_unix_s=time.time())
    if args.selection.exists():raise FileExistsError('selection already exists; use a new path to change the frozen cohort')
    save(args.selection,manifest)
    print(json.dumps(manifest,sort_keys=True))


def entries(args):
    from eval.splits import spec_key,split_of
    manifest=json.loads(args.selection.read_text())
    assert manifest['format']=='exact-scalable-fresh-v1'
    rows=[]
    for entry in manifest['entries']:
        if entry['split'] not in ('train','val'):raise ValueError('test specifications forbidden')
        assert split_of(entry['spec'])==entry['split'] and spec_key(entry['spec'])==entry['spec_key']
        if args.rung and entry['rung']!=args.rung:continue
        rows.append(entry)
    rows=[entry for index,entry in enumerate(rows) if index%args.shard_count==args.shard_index]
    return rows[args.offset:args.offset+args.count]


def paths(args,entry):
    path=args.directory/'instances'/entry['rung']/(entry['spec_key']+'.json')
    return path,path.with_suffix('.audit.json')


def generate(args):
    from dataclasses import asdict
    from stowage.io import save_instance
    from stowage.frozen import require_clean
    from eval.splits import spec_from_instance,spec_key
    current=fingerprint(generation=True)
    for entry in entries(args):
        path,audit_path=paths(args,entry)
        if path.exists() or audit_path.exists():
            load(args,entry,require_current=True)
            print(json.dumps(dict(spec_key=entry['spec_key'],status='resumed')),flush=True);continue
        spec=entry['spec'];start=time.monotonic()
        if 'generator_spec' in entry:
            from stowage.generate.instances_v3 import generate as generate_v3,physics_metadata
            if entry['generator_spec']['physics_sha256']!=physics_metadata()['sha256']:raise ValueError('selected v3 physics changed')
            if 'source_shard' in entry:
                from stowage.model import Instance
                src=entry['source_shard'];payload=(Path(src['root'])/src['relative']).read_bytes()
                assert sha256(payload).hexdigest()==src['sha256']
                instance=Instance.model_validate_json(payload);generator_audit={'copied_manifest_shard':src}
                require_clean(instance,what='selected canonical v3 shard')
                assert spec_key(spec_from_instance(instance))==entry['spec_key']
            else:instance,generator_audit=generate_v3(entry['generator_spec'])
            path.parent.mkdir(parents=True,exist_ok=True);save_instance(instance,path)
            save(audit_path,dict(entry=entry,sha256=digest(path),generation_source_sha256=current,
                generation_s=time.monotonic()-start,n_load=len(instance.load_list),vessel_cells=len(instance.vessel.cells),
                generator_audit=generator_audit))
            print(json.dumps(dict(spec_key=entry['spec_key'],status='generated_v3',n_load=len(instance.load_list),generation_s=time.monotonic()-start)),flush=True)
            continue
        common=dict(seed=spec['seed'],vessel_name=spec['vessel'],port_name=spec['port'],max_attempts=args.max_attempts)
        if spec['generator']=='random_yard':
            from stowage.generate.random_yard import generate_random_instance,RandomGenerationReport
            report=RandomGenerationReport()
            instance=generate_random_instance(entry['rung'],blocking_ratio=spec['blocking_ratio'],report=report,**common)
        else:
            from stowage.generate.instances import generate_simulated_instance,GenerationReport
            report=GenerationReport()
            instance=generate_simulated_instance(entry['rung'],noise=spec['noise'],report=report,**common)
        require_clean(instance,what='fresh exact/scalable paired input')
        assert spec_key(spec_from_instance(instance))==entry['spec_key']
        hull=instance.vessel.hull
        if hull is None or not hull.stations or not hull.hull_offsets_ref:raise ValueError('full hull and strength data required')
        path.parent.mkdir(parents=True,exist_ok=True);save_instance(instance,path)
        save(audit_path,dict(entry=entry,sha256=digest(path),generation_source_sha256=current,
            generation_s=time.monotonic()-start,n_load=len(instance.load_list),vessel_cells=len(instance.vessel.cells),
            generator_audit=asdict(report)))
        print(json.dumps(dict(spec_key=entry['spec_key'],status='generated',n_load=len(instance.load_list),generation_s=time.monotonic()-start)),flush=True)


def load(args,entry,*,require_current=False):
    from stowage.model import Instance
    from stowage.frozen import require_clean
    from eval.splits import spec_from_instance,spec_key
    if entry['split'] not in ('train','val'):raise ValueError('test input forbidden')
    path,audit_path=paths(args,entry)
    audit=json.loads(audit_path.read_text())
    assert audit['entry']==entry
    if require_current:
        if 'generator_spec' in entry:
            comparison=physics_source_comparison(entry,audit)
            assert comparison['physical_source_match'] is True, f'input physical model differs: {comparison}'
        else:assert audit['generation_source_sha256']==fingerprint(generation=True),'input source is stale: regenerate into a new directory'
    payload=path.read_bytes();assert sha256(payload).hexdigest()==audit['sha256'],'input checksum mismatch'
    instance=Instance.model_validate_json(payload);require_clean(instance,what='paired input')
    assert spec_key(spec_from_instance(instance))==entry['spec_key']
    return instance,audit


def _scalable(instance,seed,limit,c2_off):
    from stowage.feasibility import FeasibilityConfig
    from stowage.solver.scalable import ScalableConfig,ScalableSolver
    config=FeasibilityConfig(extra={'global_screen':0}) if c2_off else None
    return ScalableSolver(ScalableConfig(feasibility_config=config)).plan(instance,seed=seed,time_limit_s=limit)


def _validate(instance,result,c2_off,ballast_limit,deadline):
    from stowage.actions import get_action_space
    from stowage.feasibility import make_feasibility_engine,FeasibilityConfig
    from stowage.moves import apply_plan
    from stowage.planner import finalize
    from stowage.checker import check
    from stowage.solver.exact_deadline import publish_checkpoint
    started=time.monotonic()
    replay=apply_plan(instance,result.plan)
    aboard={p.container_id for p in replay.final_state.ship.placements}
    shut_out=sorted(set(instance.load_list)-aboard)
    complete=(replay.legal and not shut_out and not(set(instance.discharge_list)&aboard)
              and not replay.final_state.yard.quay_buffer and not replay.final_state.ship.covers_off)
    row=dict(physical_legal=replay.legal,completed=bool(complete),shut_out=len(shut_out),
             lifts=replay.count.model_dump(mode='json'),checker_passed=None,configured_replay_passed=False,
             production_g1_verified=False,tertiary_certified=False,timing_s={'physical_replay':time.monotonic()-started})
    publish_checkpoint(row)
    if not complete:return row
    # A physically complete timed-out incumbent can be judged, without changing
    # the recorded original planner status or calling a partial plan complete.
    candidate=result.model_copy(update={'status':'ok'})
    phase=time.monotonic()
    candidate=finalize(instance,candidate,time_limit_s=max(1e-9,min(ballast_limit,deadline-phase)))
    row.update(finalize_status=candidate.status)
    row['timing_s']['finalize']=time.monotonic()-phase
    phase=time.monotonic();report=check(instance,candidate.plan)
    row['timing_s']['checker']=time.monotonic()-phase
    row.update(checker_passed=report.passed,hard_violations=sum(v.severity=='hard' for v in report.violations),
        violation_ids=[v.rule_id for v in report.violations if v.severity=='hard'],shut_out=len(report.shut_out),
        objective=report.count.weighted_nonproductive if report.count else None,
        margins={name:part.margins for name,part in report.parts.items()})
    if report.passed:row['checked_plan']=candidate.plan
    publish_checkpoint(row)
    if not report.passed:return row
    phase=time.monotonic();space=get_action_space()
    config=FeasibilityConfig(extra={'global_screen':0}) if c2_off else None
    try:
        engine=make_feasibility_engine(instance,space,config=config) if config else make_feasibility_engine(instance,space)
        actions=space.from_plan(instance,candidate.plan)
        for action in actions:engine.push(action)
    except ValueError as error:
        row['configured_replay_error']=str(error)
        row['timing_s']['configured_replay']=time.monotonic()-phase
        return row
    row.update(configured_replay_passed=engine.is_complete(),production_g1_verified=engine.is_complete() and not c2_off,
               configured_replay_actions=len(actions))
    row['timing_s']['configured_replay']=time.monotonic()-phase
    return row


def run(args):
    from stowage.solver.exact import solve_exact
    from stowage.solver.exact_deadline import run_with_deadline
    from stowage.feasibility import FeasibilityConfig
    from stowage.model import PlannerResult
    from stowage.bounds import lower_bound
    from stowage.versions import component_versions
    source=fingerprint();versions=component_versions()
    if versions['checker']!='1.3':raise ValueError('current comparison requires checker 1.3')
    output=args.directory/'results.jsonl'
    done={json.loads(line)['run_key'] for line in output.read_text().splitlines()} if output.exists() else set()
    for entry in entries(args):
        instance,audit=load(args,entry,require_current=not args.allow_frozen_input)
        bound=lower_bound(instance).weighted
        for planner in args.planners:
            config_key=dict(input=audit['sha256'],source=source,planner=planner,search_s=args.time_limit,
                            validation_s=args.validation_limit,c2_off=args.c2_off,seed=args.solver_seed,return_grace_s=args.return_grace)
            key=sha256(json.dumps(config_key,sort_keys=True).encode()).hexdigest()
            if key in done:continue
            started=time.monotonic();phase_source_before=fingerprint();versions_before=component_versions()
            print(json.dumps({'phase':'planner_start','planner':planner,'spec_key':entry['spec_key'],'pid':os.getpid(),'time_limit_s':args.time_limit}),flush=True)
            usage=resource.getrusage(resource.RUSAGE_CHILDREN)
            cpu_start=usage.ru_utime+usage.ru_stime
            result=None;search_timeout=False
            if planner=='exact':
                config=FeasibilityConfig(extra={'global_screen':0}) if args.c2_off else None
                exact=solve_exact(instance,time_limit_s=args.time_limit,max_nodes=args.max_nodes,num_workers=1,
                                  seed=args.solver_seed,feasibility_config=config)
                search_timeout=bool(exact.metrics.get('watchdog_timeout'))
                result=PlannerResult(status='timeout' if exact.status=='TIME_LIMIT' else 'ok' if exact.plan is not None else 'infeasible' if exact.status=='INFEASIBLE' else 'timeout',plan=exact.plan,
                    planner='solver.exact',planner_version='paired',seed=args.solver_seed,metrics=exact.metrics)
                certificate=dict(status=exact.status,objective=exact.objective,lower_bound=exact.lower_bound,gap=exact.gap,
                                 expanded_nodes=exact.expanded_nodes)
            else:
                outcome=run_with_deadline(_scalable,(instance,args.solver_seed,args.time_limit,args.c2_off),
                                          deadline=time.monotonic()+args.time_limit+args.return_grace)
                search_timeout=outcome.timed_out;result=outcome.value if not outcome.timed_out else None
                certificate=None
            searched=time.monotonic();search_source_after=fingerprint();search_versions_after=component_versions()
            print(json.dumps({'phase':'planner_complete','planner':planner,'spec_key':entry['spec_key'],'search_s':searched-started,'has_plan':result is not None and result.plan is not None}),flush=True)
            validation=None;validation_timeout=False
            if result is not None and result.plan is not None:
                deadline=time.monotonic()+args.validation_limit
                outcome=run_with_deadline(_validate,(instance,result,args.c2_off,args.ballast_limit,deadline),deadline=deadline)
                validation_timeout=outcome.timed_out
                validation=outcome.checkpoint if outcome.timed_out else outcome.value
            finished=time.monotonic();usage=resource.getrusage(resource.RUSAGE_CHILDREN)
            from stowage.generate.instances_v3 import physics_metadata
            current_physics=physics_metadata(c2_screened=False)['sha256']
            input_physics=audit.get('generator_audit',{}).get('physics',{}).get('sha256')
            if input_physics is None and 'source_shard' in entry:
                root=Path(entry['source_shard']['root']);manifest=json.loads((root/'manifest.json').read_text())
                for group in manifest['groups'].values():
                    for shard in group['shards']:
                        if shard.get('sha256')==entry['source_shard']['sha256']:input_physics=shard.get('physics_sha256')
            physics_comparison=physics_source_comparison(entry,audit)
            row=dict(input_physics_source_comparison=physics_comparison,runtime_source_before=phase_source_before,search_source_after=search_source_after,runtime_source_after=fingerprint(),
                versions_before=versions_before,search_versions_after=search_versions_after,versions_after=component_versions(),
                input_physics_sha256=input_physics,current_physics_sha256=current_physics,input_physics_current_match=input_physics==current_physics,
                timing_context='shared machine contended; not an idle-machine benchmark',run_key=key,entry=entry,input_sha256=audit['sha256'],generation_source_sha256=audit['generation_source_sha256'],
                source_sha256=source,component_versions=versions,planner=planner,config=config_key,n_load=len(instance.load_list),
                search_timeout=search_timeout,search_overrun_s=max(0.,searched-started-args.time_limit),search_finished_within_budget=searched-started<=args.time_limit,validation_timeout=validation_timeout,
                planner_status=result.status if result else 'hard_timeout',solver_metrics=result.metrics if result else {},
                exact_certificate=certificate,independent_lower_bound=bound,validation=validation,
                timing_s=dict(search=searched-started,validation=finished-searched,total=finished-started),
                children_cpu_s=None,children_cpu_delta_raw_s=usage.ru_utime+usage.ru_stime-cpu_start,
                cpu_attribution='unavailable per planner: bounded watchdog cleanup can defer child accounting',
                cumulative_reaped_children_peak_rss_bytes=(usage.ru_maxrss if os.uname().sysname=='Darwin' else usage.ru_maxrss*1024) or None)
            if validation and validation.get('checked_plan') is not None:
                plan=validation.pop('checked_plan');folder=args.directory/'checked-reference-plans';folder.mkdir(exist_ok=True)
                target=folder/(key+'.json');target.write_text(plan.model_dump_json(indent=2)+'\n')
                row['checked_reference_plan']=str(target);row['checked_reference_plan_sha256']=digest(target)
            value=validation.get('objective') if validation and validation.get('checker_passed') else None
            row['counting_bound_gap']=(0. if value==bound==0 else (value-bound)/value if value else None)
            with output.open('a') as stream:stream.write(json.dumps(row,sort_keys=True,allow_nan=False)+'\n')
            print(json.dumps(row,sort_keys=True,allow_nan=False),flush=True)


def oracle(args):
    from stowage.solver.exact_oracle import checker_brute_force_certificate
    from stowage.actions import get_action_space
    from stowage.io import load_plan
    output=args.directory/'oracle-results.jsonl';space=get_action_space()
    results=[json.loads(line) for line in (args.directory/'results.jsonl').read_text().splitlines()] if (args.directory/'results.jsonl').exists() else []
    for entry in entries(args):
        if entry['rung']!='tiny':raise ValueError('independent brute-force restricted to tiny')
        instance,audit=load(args,entry,require_current=not args.allow_frozen_input)
        matches=[r for r in results if r['entry']==entry and r.get('checked_reference_plan')]
        incumbent=load_plan(matches[-1]['checked_reference_plan']) if matches else None
        value=checker_brute_force_certificate(instance,incumbent=incumbent,time_limit_s=args.oracle_limit,max_nodes=args.max_nodes)
        row={f.name:getattr(value,f.name) for f in fields(value) if f.name not in ('plan','report')}
        row.update(entry=entry,input_sha256=audit['sha256'],source_sha256=fingerprint(),
                   scope='independent_full_physical_reference_not_public_mask',oracle_limit_s=args.oracle_limit)
        if value.plan:
            actions=space.from_plan(instance,value.plan)
            overrides=[space.encode(a) for a in actions if type(a).__name__ in ('Relocate','RestowOff')
                       or (type(a).__name__=='Discharge' and getattr(a,'to',None) is not None)]
            from stowage.actions.expander import Expander
            ex=Expander(instance);ship_shifts=[]
            for action in actions:
                if type(action).__name__=='Place' and ex.where.get(action.box,('',))[0]=='ship':ship_shifts.append(space.encode(action))
                ex.step(action)
            row['canonical_overrides']=overrides
            row['ordinary_ship_shifts_excluded_by_public_v2']=ship_shifts
            exact_roundtrip=space.expand(instance,actions).moves==value.plan.moves
            row['checked_physical_moves_reproduced_exactly']=exact_roundtrip
            row['override_necessity']=('an optimum exists without explicit overrides or ship shifts; identical checked moves'
                if value.status=='OPTIMAL' and not overrides and not ship_shifts and exact_roundtrip else
                'unresolved; one witness or an engine-domain gap cannot prove necessity')
        else:row['override_necessity']='unresolved; no checker witness'
        with output.open('a') as stream:stream.write(json.dumps(row,sort_keys=True,allow_nan=False)+'\n')
        print(json.dumps(row,sort_keys=True,allow_nan=False),flush=True)



def summarize(args):
    path=args.directory/'results.jsonl'
    rows=[json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []
    cohorts={}
    for row in rows:
        group=(row['entry']['spec_key'],row['source_sha256'],row['config']['c2_off'],row['config']['search_s'])
        cohorts.setdefault(group,{})[row['planner']]=row
    pairs=[]
    for key,pair in cohorts.items():
        item=dict(spec_key=key[0],source_sha256=key[1],c2_off=key[2],search_limit_s=key[3],point_gap=None,lift_difference=None)
        exact=pair.get('exact');scalable=pair.get('scalable')
        valid=bool(exact and scalable and exact['exact_certificate']['status']=='OPTIMAL'
            and all((r.get('validation') or {}).get('checker_passed') is True
                    and (r.get('validation') or {}).get('configured_replay_passed') is True for r in pair.values()))
        unchanged=all('runtime_source_before' in r and r['runtime_source_before']==r.get('runtime_source_after') for r in pair.values())
        if valid and unchanged:
            e=exact['validation']['objective'];s=scalable['validation']['objective']
            item.update(exact_lifts=e,scalable_lifts=s,lift_difference=s-e,point_gap=(s-e)/s if s else 0.)
            if s<e:raise AssertionError(f'scalable checker/model witness beats proved exact optimum: {item}')
        else:item['reason']='requires exact OPTIMAL and both full-checker/configured-mask passes in an unchanged source epoch'
        pairs.append(item)
    summary=dict(rows=len(rows),paired_comparisons=pairs,
        counts={planner:dict(attempted=sum(r['planner']==planner for r in rows),
            checker_passed=sum(r['planner']==planner and (r.get('validation') or {}).get('checker_passed') is True for r in rows),
            exact_optimal=sum(r['planner']==planner and bool(r.get('exact_certificate')) and r['exact_certificate']['status']=='OPTIMAL' for r in rows)) for planner in ('exact','scalable')})
    save(args.directory/'summary.json',summary);print(json.dumps(summary,sort_keys=True))


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('command',choices=('select','generate','run','oracle','summarize'))
    p.add_argument('--directory',type=Path,default=SCOPE/'current-v3')
    p.add_argument('--selection',type=Path,default=None)
    p.add_argument('--canonical-v3',type=Path,default=None)
    p.add_argument('--fresh-canonical',action='store_true',help='regenerate selected v3 specifications under the pinned current epoch')
    p.add_argument('--seed-start',type=int,default=4600000)
    p.add_argument('--tiny-count',type=int,default=3)
    p.add_argument('--small-train',type=int,default=3)
    p.add_argument('--small-val',type=int,default=3)
    p.add_argument('--generator',choices=('random_yard','simulated'),default='random_yard')
    p.add_argument('--vessel',default='feeder_500');p.add_argument('--port',default='laem_chabang_like')
    p.add_argument('--control',type=float,default=.3)
    p.add_argument('--rung',choices=('tiny','small'),default=None)
    p.add_argument('--offset',type=int,default=0);p.add_argument('--count',type=int,default=1)
    p.add_argument('--shard-index',type=int,default=0);p.add_argument('--shard-count',type=int,default=1)
    p.add_argument('--max-attempts',type=int,default=1000)
    p.add_argument('--planners',nargs='+',choices=('exact','scalable'),default=['exact','scalable'])
    p.add_argument('--time-limit',type=float,default=120.)
    p.add_argument('--validation-limit',type=float,default=60.)
    p.add_argument('--return-grace',type=float,default=2.,help='scalable serialization grace outside its stated search budget')
    p.add_argument('--ballast-limit',type=float,default=30.)
    p.add_argument('--oracle-limit',type=float,default=10.)
    p.add_argument('--max-nodes',type=int,default=100000)
    p.add_argument('--solver-seed',type=int,default=0)
    p.add_argument('--c2-off',action='store_true')
    p.add_argument('--allow-frozen-input',action='store_true',help='explicitly measure saved historical generator bytes')
    args=p.parse_args();args.selection=args.selection or args.directory/'selection.json'
    args.directory.mkdir(parents=True,exist_ok=True)
    if args.shard_count<1 or not 0<=args.shard_index<args.shard_count:raise ValueError('invalid deterministic worker shard')
    if min(args.count,args.max_nodes,args.max_attempts)<1 or min(args.tiny_count,args.small_train,args.small_val)<0 or args.offset<0:raise ValueError('positive counts and nonnegative offset required')
    dict(select=select,generate=generate,run=run,oracle=oracle,summarize=summarize)[args.command](args)


if __name__=='__main__':main()
