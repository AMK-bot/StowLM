"""Frozen hero shortlist -> Spark brief -> saved-plan fresh audit (one worker)."""
from __future__ import annotations
import argparse
from concurrent.futures import ProcessPoolExecutor
from datetime import datetime, timezone
from decimal import Decimal
import hashlib
import json
import multiprocessing as mp
import os
from pathlib import Path
import time

HERE=Path(__file__).resolve().parent
ROOT=HERE.parents[3]
RUN=HERE/'runs'
PURPOSE='reader-hero-v5-3.2'


def digest(path):return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def write(path,value):
    path.parent.mkdir(parents=True,exist_ok=True)
    tmp=path.with_suffix(f'.{os.getpid()}.tmp');tmp.write_text(json.dumps(value,indent=2)+'\n');tmp.replace(path)


def guard_input(path):
    path=Path(path)
    parts=(ROOT/path).resolve().relative_to(ROOT).parts
    if any(p in parts for p in ('test','test-v3','test_v3')) or not any(p in parts for p in ('val','train')):
        raise ValueError('Only validation/train map inputs are allowed')
    return path


def verify():
    frozen=json.loads((HERE/'manifest.json').read_text())
    if digest(HERE/'CRITERIA.json')!=frozen['criteria_sha256']:raise ValueError('Hero criteria changed')
    for path,sha in frozen['sources'].items():
        if digest(ROOT/path)!=sha:raise ValueError('Hero dependency changed: '+path)
    for case in frozen['cases']:
        guard_input(case['input'])
        for field,sha in (('input','input_sha256'),('baseline_file','baseline_sha256')):
            if digest(ROOT/case[field])!=case[sha]:raise ValueError('Frozen hero input/baseline changed')
        after=case.get('preferred_after')
        if after and digest(ROOT/after['plan_file'])!=after['plan_record_sha256']:
            raise ValueError('Saved constrained plan changed')
    return frozen


def prepare():
    from stowage.io import load_instance
    from experiments.mvp.reader.pipeline import SparkParser
    from experiments.mvp.reader.brief_planner import VoyageBrief,read_brief
    frozen=verify();parser=None
    for case in frozen['cases']:
        target=RUN/'parser'/f"{case['case_id']}.json"
        if target.exists():continue
        if parser is None:parser=SparkParser(purpose=PURPOSE,allocation_usd=Decimal('1'))
        instance=load_instance(ROOT/guard_input(case['input']))
        started=time.perf_counter()
        brief,_=read_brief(instance,VoyageBrief('One port per hatch.',(case['sentence'],)),parser=parser)
        write(target,dict(case_id=case['case_id'],brief=brief,parse_wall_s=time.perf_counter()-started))
        print(json.dumps(dict(event='parsed',case_id=case['case_id'],statuses=[s['status'] for s in brief['sentences']])),flush=True)


def admitted():
    from experiments.mvp.hybrid.admission import reading
    while True:
        value=reading()
        if value['allowed']:return value
        print(json.dumps(dict(event='waiting_admission',pid=os.getpid(),evidence=value)),flush=True)
        time.sleep(15)


def audit_case(case):
    """New interpreter: full-check before/after, intended semantics and mask."""
    verify()
    target=RUN/'audits'/f"{case['case_id']}.json"
    if target.exists():return case['case_id'],'cached'
    parse_path=RUN/'parser'/f"{case['case_id']}.json"
    parsed_record=json.loads(parse_path.read_text())
    brief=parsed_record['brief']
    row=dict(case_id=case['case_id'],sentence=case['sentence'],input=case['input'],
             fresh_process=True,audit_pid=os.getpid(),passed=False,binding=False,repaired=False,
             parser_record_sha256=digest(parse_path),parse_wall_s=parsed_record['parse_wall_s'])
    from experiments.mvp.reader.schema import canonicalize,validate_answer,context_from_instance
    voyages=[s for s in brief['sentences'] if s['role']=='voyage']
    row['parse_exact']=bool(len(voyages)==1 and voyages[0].get('parsed') is not None and
                            canonicalize(voyages[0]['parsed'])==canonicalize(case['expected']))
    if not row['parse_exact'] or not brief['port_blocked_start']:
        row.update(rejection='reader_mismatch_or_standing_preference_not_enabled',audit_wall_s=0.)
        write(target,row);return case['case_id'],False,row['rejection']
    row['admission']=admitted()
    claim=RUN/'claims'/f"{case['case_id']}.json";claim.parent.mkdir(parents=True,exist_ok=True)
    with claim.open('x') as handle:json.dump(dict(pid=os.getpid(),admission=row['admission']),handle)
    began=time.perf_counter()
    try:
        from stowage.io import load_instance
        from stowage.model import MovePlan
        from stowage.checker import check
        from stowage.moves import apply_plan
        from stowage.actions import get_action_space
        from stowage.feasibility import FeasibilityConfig,make_feasibility_engine
        from experiments.mvp.reader.pipeline import compile_constraint,intrinsic_violations
        from experiments.mvp.reader.constraints import wrap_engine
        from experiments.mvp.reader.hero.visual_delta import build_visual_delta
        instance=load_instance(ROOT/guard_input(case['input']))
        context=context_from_instance(instance)
        intended=validate_answer(json.dumps(case['expected']),context)
        answer=validate_answer(json.dumps(voyages[0]['parsed']),context)
        before_row=json.loads((ROOT/case['baseline_file']).read_text())
        before=MovePlan.model_validate(before_row['plan'])
        constrained,compilation=compile_constraint(instance,answer,workers=1,seed=0)
        preferred=case.get('preferred_after')
        if preferred:
            source=json.loads((ROOT/preferred['plan_file']).read_text())
            after=MovePlan.model_validate(source['plan'])
            row.update(source_mode='reused_recorded_constrained_plan',new_replan_s=None,
                       after_record_sha256=digest(ROOT/preferred['plan_file']),
                       historical_worker_wall_s=source.get('wall_s'),
                       historical_planner_component_s=source.get('record',source.get('evidence',{}).get('planner',{})).get('reader_wall_s'))
        else:
            from experiments.mvp.reader.brief_planner import execute_brief
            started=time.perf_counter()
            result,details=execute_brief(instance,brief,[answer],search_s=float(before_row.get('search_s',60)),
                                         finalize_s=300.,constructor_s=120.,seed=0,workers=1)
            row.update(source_mode='new_instruction_aware_replan',new_replan_s=time.perf_counter()-started,planner=details)
            if result is None:
                row['rejection']='no_checked_plan';raise RuntimeError('No checked plan')
            after=result.plan
        before_check=check(instance,before)
        after_check=check(instance,after)
        replay=apply_plan(instance,after)
        before_errors=intrinsic_violations(instance,intended,before)
        intended_errors=intrinsic_violations(instance,intended,after)
        parsed_errors=intrinsic_violations(instance,answer,after)
        compiled_errors=constrained.violations(after)
        space=get_action_space('boxslot')
        engine=wrap_engine(make_feasibility_engine(instance,space,config=FeasibilityConfig(extra={'global_screen':0})),constrained)
        mask_legal=True;checked_actions=0
        for action in space.from_plan(instance,after):
            if not engine.is_legal(action):mask_legal=False;break
            engine.push(action);checked_actions+=1
        positions={p.container_id:p.slot for p in replay.final_state.ship.placements}
        visual=build_visual_delta(instance,before,after,intended)
        before_cost=float(before_check.count.weighted_nonproductive);after_cost=float(after_check.count.weighted_nonproductive)
        row.update(before_checker_passed=bool(before_check.passed),after_checker_passed=bool(after_check.passed),
                   before_instruction_violations=before_errors,intended_violations=intended_errors,
                   parsed_violations=parsed_errors,compiled_violations=compiled_errors,
                   before_moves=before_cost,after_moves=after_cost,move_delta=after_cost-before_cost,
                   before_cost_reproduced=before_cost==before_row['weighted'],
                   mask_legal=mask_legal,mask_complete=mask_legal and engine.is_complete(),mask_actions_checked=checked_actions,
                   replay_legal=bool(replay.legal),all_loaded=set(instance.load_list)<=positions.keys(),
                   visual_delta=visual,compilation=compilation,
                   checker_violations=[v.rule_id for v in after_check.violations])
        row['passed']=bool(before_check.passed and after_check.passed and replay.legal and row['before_cost_reproduced']
                           and mask_legal and engine.is_complete() and row['all_loaded']
                           and not intended_errors and not parsed_errors and not compiled_errors)
        row['binding']=bool(row['passed'] and before_errors and visual['visible_binding_candidate'])
        if not row['binding']:row['rejection']='mandatory_binding_or_fresh_gate_failed'
        asset=RUN/'plans'/f"{case['case_id']}.json"
        write(asset,dict(case_id=case['case_id'],input=case['input'],input_sha256=case['input_sha256'],
                        sentence=case['sentence'],parsed=voyages[0]['parsed'],before_plan=json.loads(before.model_dump_json()),
                        after_plan=json.loads(after.model_dump_json()),source_mode=row['source_mode']))
        row.update(asset_file=str(asset.relative_to(ROOT)),asset_sha256=digest(asset))
    except Exception as error:
        row.update(passed=False,binding=False,error_type=type(error).__name__)
    row['audit_wall_s']=time.perf_counter()-began
    row['new_work_wall_s']=row['parse_wall_s']+row['audit_wall_s']
    write(target,row)
    return case['case_id'],row['passed'],row['binding'],row.get('before_moves'),row.get('after_moves'),round(row['audit_wall_s'],1),row.get('error_type')


def run():
    frozen=verify()
    if any(not (RUN/'parser'/f"{c['case_id']}.json").exists() for c in frozen['cases']):
        raise ValueError('All fixed shortlisted sentences must be read first')
    write(RUN/'controller.json',dict(pid=os.getpid(),workers=1,native_workers=1,
          started_utc=datetime.now(timezone.utc).isoformat(),cohorts_untouched=True))
    for case in frozen['cases']:
        if (RUN/'audits'/f"{case['case_id']}.json").exists():continue
        if (RUN/'claims'/f"{case['case_id']}.json").exists():raise RuntimeError('Unreturned attempt, no automatic retry')
        with ProcessPoolExecutor(1,mp_context=mp.get_context('spawn'),max_tasks_per_child=1) as pool:
            print(json.dumps(dict(event='audited',result=pool.submit(audit_case,case).result())),flush=True)
        from experiments.mvp.reader.hero.report import build
        build()
    from experiments.mvp.reader.hero.report import build
    summary=build();write(RUN/'completion.json',dict(status='complete',summary=summary))


if __name__=='__main__':
    cli=argparse.ArgumentParser(description=__doc__);cli.add_argument('mode',choices=('prepare','run'));args=cli.parse_args()
    prepare() if args.mode=='prepare' else run()
