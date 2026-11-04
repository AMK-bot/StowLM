"""Checkpointed exact evaluation of frozen canonical train/validation shards.

The release pins manifest bytes. Filter metadata by split BEFORE opening instance
files; each selected shard must match its manifest SHA-256 before schema loading.
Test instances are never opened. These are canonical inputs, not selected by
solver outcome. Finalization and the full checker are timed separately, and a
weighted-lift OPTIMAL status never implies D11 tertiary-margin optimality.
"""
from __future__ import annotations

import argparse
from collections import defaultdict
from hashlib import sha256
import json
import resource
from pathlib import Path
from time import perf_counter

EXPERIMENT=Path(__file__).resolve().parent


def eligible_rows(root: Path, *, generators, profiles, splits):
    release=json.loads((root/'release.json').read_text())
    manifest_bytes=(root/'manifest.json').read_bytes()
    assert sha256(manifest_bytes).hexdigest()==release['manifest_sha256'], 'release/manifest digest mismatch'
    manifest=json.loads(manifest_bytes)
    assert manifest['split_status']=='assigned'
    assert set(splits)<={'train','val'}, 'test access is forbidden'
    answer=[]
    for group,metadata in sorted(manifest['groups'].items()):
        generator,profile=group.split('/')
        if generator not in generators or profile not in profiles:continue
        for row in sorted(metadata['shards'],key=lambda r:r['seed']):
            if row['split'] not in splits:continue
            relative=Path(group)/row['instance']
            assert not relative.is_absolute() and '..' not in relative.parts
            answer.append((relative,row))
    return release,answer


def load_selected(root,relative,row):
    assert row['split'] in ('train','val')
    payload=(root/relative).read_bytes()
    assert sha256(payload).hexdigest()==row['sha256'],f'bad instance digest: {relative}'
    from stowage.model import Instance
    from stowage.frozen import require_clean
    from eval.splits import canonical_spec,spec_from_instance
    instance=Instance.model_validate_json(payload)
    require_clean(instance,what='exact canonical input')
    assert canonical_spec(spec_from_instance(instance))==row['split_spec']
    return instance


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root',type=Path,default=Path('experiments/instances-v2'))
    parser.add_argument('--generators',nargs='+',choices=('random_yard','simulated_yard'),default=['random_yard'])
    parser.add_argument('--profiles',nargs='+',default=['tiny'])
    parser.add_argument('--splits',nargs='+',choices=('train','val'),default=['train','val'])
    parser.add_argument('--offset',type=int,default=0)
    parser.add_argument('--limit',type=int,default=1)
    parser.add_argument('--time-limit',type=float,default=10.)
    parser.add_argument('--max-nodes',type=int,default=100000)
    parser.add_argument('--prewarm',action='store_true')
    parser.add_argument('--oracle',action='store_true')
    parser.add_argument('--oracle-time-limit',type=float,default=5.)
    parser.add_argument('--outer-cap',type=float,default=None)
    parser.add_argument('--out',type=Path,default=EXPERIMENT/'canonical-pilot.jsonl')
    parser.add_argument('--summary',type=Path,default=EXPERIMENT/'canonical-summary.json')
    args=parser.parse_args()
    release,rows=eligible_rows(args.root,generators=args.generators,profiles=args.profiles,splits=args.splits)
    from stowage.solver.exact import solve_exact
    from stowage.bounds import lower_bound
    from stowage.checker import check
    from stowage.planner import finalize
    from stowage.model import PlannerResult
    source={str(p.name):sha256(p.read_bytes()).hexdigest() for p in Path('src/stowage/solver').glob('exact*.py')}
    selected=rows[args.offset:args.offset+args.limit]
    done={json.loads(line).get('run_key') for line in args.out.read_text().splitlines()} if args.out.exists() else set()
    for relative,metadata in selected:
        run_key=sha256(json.dumps(dict(spec_key=metadata['spec_key'],source=source,
            manifest=release['manifest_sha256'],time_limit_s=args.time_limit,
            max_nodes=args.max_nodes,prewarm=args.prewarm,oracle=args.oracle,
            oracle_time_limit_s=args.oracle_time_limit),sort_keys=True).encode()).hexdigest()
        if run_key in done:continue
        instance=load_selected(args.root,relative,metadata)
        started=perf_counter()
        cpu_started=resource.getrusage(resource.RUSAGE_SELF).ru_utime+resource.getrusage(resource.RUSAGE_SELF).ru_stime
        print(json.dumps({'phase':'start','instance':str(relative),'split':metadata['split']}),flush=True)
        setup_diagnostics={}
        setup_dead=None
        if args.prewarm:
            from stowage.actions import get_action_space
            from stowage.feasibility import make_feasibility_engine
            prewarm=make_feasibility_engine(instance,get_action_space())
            setup_dead=prewarm.dead_end()
            if setup_dead is not None:
                setup_diagnostics={'dead_end_reason':setup_dead.reason,
                                   'rule_ids':list(setup_dead.rule_ids)}
        setup_done=perf_counter()
        print(json.dumps({'phase':'setup_complete','instance':str(relative),
                          'setup_s':setup_done-started,'diagnostics':setup_diagnostics},default=str),flush=True)
        if setup_dead is not None and 'checker.missing_vessel_data' in setup_dead.rule_ids:
            from types import SimpleNamespace
            result=SimpleNamespace(status='MODEL_UNAVAILABLE',plan=None,objective=None,
                lower_bound=lower_bound(instance).weighted,gap=None,expanded_nodes=0,
                metrics={'model_unavailable':True,'model_unavailable_reason':setup_dead.reason,
                         'optimality_scope':'engine_model_weighted_lifts','tertiary_certified':False,
                         'shippable':False,'checker_passed':False,'num_workers':1,
                         'algorithm':'astar_branch_and_bound','termination':'missing_model'})
        else:
            result=solve_exact(instance,time_limit_s=args.time_limit,max_nodes=args.max_nodes,
                               num_workers=1,check_physics=True)
        solved=perf_counter()
        candidate=PlannerResult(status='ok' if result.plan is not None else 'timeout',
                    plan=result.plan,planner='solver.exact',planner_version='canonical-evaluation',
                    seed=instance.seed,metrics=result.metrics,provenance=instance.provenance)
        candidate=finalize(instance,candidate,time_limit_s=args.time_limit)
        finalized=perf_counter()
        report=check(instance,candidate.plan) if candidate.plan is not None else None
        checked=perf_counter()
        oracle=None
        if args.oracle:
            assert relative.parts[1]=='tiny','independent brute-force is limited to tiny'
            from stowage.solver.exact_oracle import checker_brute_force_certificate
            certificate=checker_brute_force_certificate(instance,incumbent=candidate.plan,
                time_limit_s=args.oracle_time_limit,max_nodes=args.max_nodes)
            from dataclasses import fields
            oracle={f.name:getattr(certificate,f.name) for f in fields(certificate) if f.name not in ('plan','report')}
        finished=perf_counter()
        lb=lower_bound(instance).weighted
        objective=report.count.weighted_nonproductive if report is not None and report.count is not None else result.objective
        row=dict(run_key=run_key,spec_key=metadata['spec_key'],instance=str(relative),sha256=metadata['sha256'],
                 split=metadata['split'],profile=relative.parts[1],generator=relative.parts[0],
                 manifest_sha256=release['manifest_sha256'],content_sha256=release['content_sha256'],
                 solver_source_sha256=source,n_load=len(instance.load_list),status=result.status,
                 objective=objective,lower_bound=lb,solver_lower_bound=result.lower_bound,
                 gap=result.gap,counting_bound_gap=(0. if objective==0 else (objective-lb)/objective) if objective is not None else None,
                 expanded_nodes=result.expanded_nodes,cpu_s=resource.getrusage(resource.RUSAGE_SELF).ru_utime+resource.getrusage(resource.RUSAGE_SELF).ru_stime-cpu_started,peak_rss_mb=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss/1024**2,prewarm=args.prewarm,setup_s=setup_done-started,setup_diagnostics=setup_diagnostics,solve_s=solved-setup_done,finalize_s=finalized-solved,
                 check_s=checked-finalized,oracle_s=finished-checked,oracle=oracle,total_s=finished-started,time_limit_s=args.time_limit,outer_cap_s=args.outer_cap,
                 finalize_status=candidate.status,checker_passed=report.passed if report else None,
                 lifts=report.count.model_dump() if report is not None and report.count is not None else None,
                 violations=[v.rule_id for v in report.violations if v.severity=='hard'] if report else [],
                 d11=dict(hard_rules_pass=report.passed if report else None,
                          shut_out=len(report.shut_out) if report else None,weighted_lifts=objective,
                          tertiary_certified=result.metrics.get('tertiary_certified',False)),metrics=result.metrics)
        if candidate.plan is not None:
            plans=EXPERIMENT/'canonical-plans'
            plans.mkdir(exist_ok=True)
            plan_path=plans/(metadata['spec_key']+'.json')
            plan_path.write_text(candidate.plan.model_dump_json(indent=2)+'\n')
            row['plan_path']=str(plan_path)
        if report:row['check_margins']={name:part.margins for name,part in report.parts.items()}
        args.out.parent.mkdir(parents=True,exist_ok=True)
        with args.out.open('a') as stream:stream.write(json.dumps(row,sort_keys=True)+'\n')
        print(json.dumps(row),flush=True)
    groups=defaultdict(list)
    if args.out.exists():
        for line in args.out.read_text().splitlines():
            row=json.loads(line);groups[row['generator']+'/'+row['profile']].append(row)
    summary={}
    for group,r in groups.items():
        summary[group]=dict(n=len(r),solved=sum(x['objective'] is not None for x in r),
                            optimal=sum(x['status']=='OPTIMAL' for x in r),
                            checker_passed=sum(x['checker_passed'] is True for x in r),
                            checker_attempted=sum(x['checker_passed'] is not None for x in r),
                            checker_pass_rate=(sum(x['checker_passed'] is True for x in r)/sum(x['checker_passed'] is not None for x in r)) if any(x['checker_passed'] is not None for x in r) else None,
                            mean_s=sum(x['total_s'] for x in r)/len(r),max_s=max(x['total_s'] for x in r))
    args.summary.write_text(json.dumps(summary,indent=2,sort_keys=True)+'\n')


if __name__=='__main__':main()
