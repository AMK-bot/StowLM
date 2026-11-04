"""D185 train-only diagnostics; single CPU, no source mutations.

Census reproduces current deterministic side-cover rule on original study train
maps. Diagnose probes a stored raw plan at inherited arrival fills, measures
cargo transverse moment, and can run the V4 worker directly to preserve telemetry.
Direct V4 runs use the same acceptance/full checker and no subprocess; parent
study production runs retain their normal hard-watchdog wrappers.
"""
from __future__ import annotations
import argparse
import json
import os
from pathlib import Path
import sys
import time

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[3]
for name in ('OMP_NUM_THREADS','OPENBLAS_NUM_THREADS','MKL_NUM_THREADS','VECLIB_MAXIMUM_THREADS','NUMEXPR_NUM_THREADS','HIGHS_THREADS'):
    os.environ[name] = '1'
os.environ['STOWAGE_IN_PORT_WORKERS'] = '1'
sys.path[:0] = [str(ROOT), str(ROOT/'src'), str(HERE.parent/'headroom')]
from experiments.mvp.strategy.headroom import run as headroom
from experiments.mvp.strategy.headroom.layout import BlockModel
from experiments.mvp.strategy.blockrule.rule import port_blocking_layout
from stowage.io import load_instance
from stowage.model import PlannerResult
from stowage.rules.catalogue import rule_freeze_v1

OUT = HERE/'diagnosis'

def save(path, value):
    headroom.atomic_json(path, value)

def jobs():
    value = headroom.read_json(HERE.parent/'headroom/runs/study/run.json')
    return [row for row in value['maps'] if '/small/' in row['input']]

def census(args):
    selected = jobs()[:args.limit]
    save(OUT/'owned.json', {'pid':os.getpid(), 'single_cpu':True, 'command':sys.argv})
    save(OUT/'census_manifest.json', {'maps':selected, 'input_source':'D175 study train allowlist', 'warning':'Not original reviewer identities: current v1 rule reproduction.'})
    for job in selected:
        folder=OUT/'census'/job['map_id']; folder.mkdir(parents=True,exist_ok=True)
        instance=load_instance(ROOT/headroom.allowed_input(job['input']))
        assert headroom.digest(ROOT/job['input']) == job['input_sha256']
        model=BlockModel.from_instance(instance)
        layout=port_blocking_layout(instance, model)
        with rule_freeze_v1(enabled=False):
            row=headroom.evaluate(instance, model, layout, 'rule15', 'diagnostic_rule', folder,
                {'small_budget':args.budget,'medium_budget':args.budget,'planner_seed':0})
        print(job['map_id'], row['status'], row.get('raw_moves'), round(row['eval_seconds'],2), flush=True)
    rows=[{'map_id':job['map_id'], **headroom.read_json(OUT/'census'/job['map_id']/'evals/rule15.json')} for job in selected]
    save(OUT/'census_summary.json',rows)

def diagnose(args):
    job=next(row for row in jobs() if row['map_id']==args.map_id)
    instance=load_instance(ROOT/headroom.allowed_input(job['input']))
    folder=OUT/'census'/job['map_id']
    row=headroom.read_json(folder/'evals/rule15.json')
    raw=PlannerResult.model_validate(headroom.read_json(row['result_path']))
    assert raw.plan is not None
    from stowage.moves import apply_plan
    from stowage.solver.ballast_v4_seeds import trim_seed_recipes
    from stowage.checker import check
    from stowage.model import BallastCondition
    from stowage.conventions import cells_of_slot
    from stowage.solver.ballast_v4 import finish
    replay=apply_plan(instance,raw.plan)
    boxes={box.id:box for box in instance.containers}
    cells={cell.id:cell for cell in instance.vessel.cells}
    cargo=[]
    for placement in replay.final_state.ship.placements:
        touched=[cells[cid] for cid in cells_of_slot(placement.slot)]
        box=boxes[placement.container_id]
        cargo.append({'cid':box.id,'pod':str(box.pod),'mass_kg':box.gross_mass_kg,'tcg_m':sum(c.tcg_m for c in touched)/len(touched),'slot':placement.slot})
    record={'map_id':args.map_id,'input':job['input'],'budget_s':args.budget,'cargo':cargo,
            'cargo_transverse_moment_kg_m':sum(c['mass_kg']*c['tcg_m'] for c in cargo),
            'cargo_mass_kg':sum(c['mass_kg'] for c in cargo)}
    inherited=trim_seed_recipes(instance)[0]
    candidate=raw.plan.model_copy(update={'ballast':BallastCondition(tank_masses_kg=inherited.tank_masses_kg,source='finisher')})
    started=time.monotonic()
    with rule_freeze_v1(enabled=False):
        report=check(instance,candidate)
    save(folder/'inherited_full_report.json',report.model_dump(mode='json'))
    record['inherited_check']={'passed':report.passed,'rules':sorted({v.rule_id for v in report.violations}), 'seconds':time.monotonic()-started,
                             'stability':{name:part.model_dump(mode='json') for name,part in report.parts.items() if 'stability' in name}}
    save(folder/'diagnostic.json',record)
    if args.counterheel:
        # Proposal only: upright table midpoint y approximates a balancing
        # fill; unchanged full checker decides every resulting condition.
        moment=record['cargo_transverse_moment_kg_m']
        tanks=[tank for tank in instance.vessel.tanks if tank.adjustable and tank.id.startswith('HEEL')]
        opposing=[tank for tank in tanks if tank.table[len(tank.table)//2].tcg_m*moment<0]
        counter_base=trim_seed_recipes(instance)[1]
        masses=dict(counter_base.tank_masses_kg)
        if opposing:
            tank=max(opposing,key=lambda t:abs(t.table[len(t.table)//2].tcg_m)*t.capacity_m3*t.content_density_kg_m3)
            y=tank.table[len(tank.table)//2].tcg_m
            masses[tank.id]=min(tank.capacity_m3*tank.content_density_kg_m3,max(0.,-moment/y))
        balanced=raw.plan.model_copy(update={'ballast':BallastCondition(tank_masses_kg=masses,source='finisher')})
        started=time.monotonic()
        with rule_freeze_v1(enabled=False):
            balanced_report=check(instance,balanced)
        save(folder/'counterheel_full_report.json',balanced_report.model_dump(mode='json'))
        save(folder/'counterheel_plan.json',raw.model_copy(update={'plan':balanced}).model_dump(mode='json'))
        record['counterheel_check']={'passed':balanced_report.passed,'rules':sorted({v.rule_id for v in balanced_report.violations}), 'seconds':time.monotonic()-started,'tank_masses_kg':masses,'base_recipe':counter_base.label,
                                  'stability':{name:part.model_dump(mode='json') for name,part in balanced_report.parts.items() if 'stability' in name}}
        save(folder/'diagnostic.json',record)
    if args.finish:
        # Direct call retains stage/negative-key telemetry on bounded timeout;
        # this is one process, no child. Exact contexts enforce their own clock.
        with rule_freeze_v1(enabled=False):
            result=finish(instance,raw.plan.model_copy(update={'ballast':None}),time_limit_s=args.budget,seed=0)
        save(folder/f'v4-direct-{int(args.budget)}.json',result.model_dump(mode='json'))
        record['finish']={'status':result.status,'cause':result.not_found_cause,'seconds':result.wall_time_s,'diagnostics':result.diagnostics}
        if result.status=='ok' and result.plan is not None:
            with rule_freeze_v1(enabled=False):
                final_report=check(instance,result.plan)
            save(folder/f'v4-direct-{int(args.budget)}-full-report.json',final_report.model_dump(mode='json'))
            record['finish']['independent_full_checker_passed']=final_report.passed
            record['finish']['rules']=sorted({v.rule_id for v in final_report.violations})
        save(folder/'diagnostic.json',record)
    print(json.dumps({key:value for key,value in record.items() if key!='cargo'},default=str),flush=True)

def neutral(args):
    job=next(row for row in jobs() if row['map_id']==args.map_id)
    instance=load_instance(ROOT/headroom.allowed_input(job['input']))
    folder=OUT/'census'/job['map_id']
    row=headroom.read_json(folder/'evals/rule15.json')
    raw=PlannerResult.model_validate(headroom.read_json(row['result_path']))
    record=headroom.read_json(folder/'diagnostic.json')
    from stowage.solver.ballast_v4_seeds import trim_seed_recipes
    from stowage.model import BallastCondition
    from stowage.checker import check
    tanks={tank.id:tank for tank in instance.vessel.tanks if tank.adjustable}
    base=trim_seed_recipes(instance)[1]
    masses={tid:base.tank_masses_kg.get(tid,0.) for tid in tanks}
    remaining=record['cargo_transverse_moment_kg_m']
    transfers=[]
    for _ in range(len(tanks)):
        candidates=[]
        for did,donor in tanks.items():
            drow=donor.table[len(donor.table)//2]
            if drow.tcg_m*remaining<=0 or masses[did]<=0:continue
            for rid,receiver in tanks.items():
                rrow=receiver.table[len(receiver.table)//2]
                spare=receiver.capacity_m3*receiver.content_density_kg_m3-masses[rid]
                if rrow.tcg_m*remaining>=0 or spare<=0:continue
                lever=drow.tcg_m-rrow.tcg_m
                amount=min(masses[did],spare,abs(remaining/lever))
                if amount<=0:continue
                # Minimal longitudinal disturbance first, then larger lever.
                candidates.append((abs(drow.lcg_m-rrow.lcg_m),-abs(lever),did,rid,amount,lever))
        if not candidates or abs(remaining)<1e-5:break
        _,_,did,rid,amount,lever=min(candidates)
        masses[did]-=amount;masses[rid]+=amount;remaining-=amount*lever
        transfers.append({'from':did,'to':rid,'mass_kg':amount,'lever_m':lever})
    candidate=raw.plan.model_copy(update={'ballast':BallastCondition(tank_masses_kg=masses,source='finisher')})
    started=time.monotonic()
    with rule_freeze_v1(enabled=False):report=check(instance,candidate)
    save(folder/'counterheel-neutral-full-report.json',report.model_dump(mode='json'))
    save(folder/'counterheel-neutral-plan.json',raw.model_copy(update={'plan':candidate}).model_dump(mode='json'))
    value={'passed':report.passed,'rules':sorted({v.rule_id for v in report.violations}),'seconds':time.monotonic()-started,
           'transfers':transfers,'remaining_upright_moment_kg_m':remaining,'base_recipe':base.label,'tank_masses_kg':masses,
           'stability':{name:part.model_dump(mode='json') for name,part in report.parts.items() if 'stability' in name}}
    record['counterheel_neutral_check']=value
    save(folder/'diagnostic.json',record)
    print(json.dumps({key:value for key,value in value.items() if key!='stability'}),flush=True)

def retry(args):
    job=next(row for row in jobs() if row['map_id']==args.map_id)
    instance=load_instance(ROOT/headroom.allowed_input(job['input']))
    folder=OUT/'census'/job['map_id']; folder.mkdir(parents=True,exist_ok=True)
    model=BlockModel.from_instance(instance); layout=port_blocking_layout(instance,model)
    with rule_freeze_v1(enabled=False):
        row=headroom.evaluate(instance,model,layout,f'rule{int(args.budget)}','diagnostic_retry',folder,
            {'small_budget':args.budget,'medium_budget':args.budget,'planner_seed':0})
    print(json.dumps(row),flush=True)

if __name__=='__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('stage',choices=['census','diagnose','retry','neutral'])
    parser.add_argument('--map-id')
    parser.add_argument('--budget',type=float,default=15)
    parser.add_argument('--limit',type=int,default=40)
    parser.add_argument('--finish',action='store_true')
    parser.add_argument('--counterheel',action='store_true')
    args=parser.parse_args(); OUT.mkdir(parents=True,exist_ok=True)
    with (OUT/'owned_processes.jsonl').open('a') as stream:
        stream.write(json.dumps({'pid':os.getpid(),'stage':args.stage,'map_id':args.map_id,'time':time.time(),'single_cpu':True})+'\n')
    globals()[args.stage](args)
