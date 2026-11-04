"""WARM LP row-family ablation on two explicitly development saved references.

This diagnostic reads prior plans. It never constructs a benchmark plan, counts
no pass, changes no constructor/checker, and certifies no physical infeasibility.
"""
from __future__ import annotations
import argparse
from datetime import datetime,timezone
import hashlib
import json
import os
from pathlib import Path
import sys
from time import monotonic
import warnings

HERE=Path(__file__).resolve().parent
ROOT=HERE.parents[5]
for p in (HERE/'engine_copy',HERE):sys.path.insert(0,str(p))
for key in ('OMP_NUM_THREADS','OPENBLAS_NUM_THREADS','VECLIB_MAXIMUM_THREADS','MKL_NUM_THREADS','NUMEXPR_NUM_THREADS','STOWAGE_IN_PORT_WORKERS'):
    os.environ[key]='1'
os.environ.setdefault('STOWAGE_VESSEL_CACHE_DIR',str(HERE/'results/vessel-cache'))
sys.dont_write_bytecode=True


def family(name):
    if name.endswith('/gm'):return 'gm'
    if '/heel_' in name:return 'heel'
    if '/tcg_' in name:return 'tcg'
    if '/draft_' in name:return 'draft'
    if '/trim_' in name:return 'trim'
    if '/mass_' in name:return 'mass'
    if '/buoyancy/' in name or '/longitudinal_equilibrium/' in name:return 'balances'
    return 'other'


def selected_first_reference(row):
    """Find the first outer-loop selected concrete plan by lifts and native GM."""
    trial=next(t for t in row['method_metadata']['trials'] if t.get('gm_actual') is not None)
    wanted_lifts=float(trial['lifts']);wanted_gm=float(trial['gm_actual'])
    options=[]
    for index,attempt in enumerate(row.get('attempts',[])):
        if 'plan' not in attempt:continue
        report=attempt.get('checker_report',{})
        gm=report.get('parts',{}).get('lashing',{}).get('metrics',{}).get('gm_m')
        lifts=attempt.get('diagnostic_lifts')
        if gm is not None and lifts is not None:
            if abs(float(gm)-wanted_gm)<1e-6 and abs(float(lifts)-wanted_lifts)<1e-6:
                options.append((index,attempt))
    if not options:raise ValueError('saved attempts do not identify the first selected reference uniquely by GM/lifts')
    index,attempt=options[0]
    areas=[]
    report=attempt.get('checker_report',{})
    for part_name,part in report.get('parts',{}).items():
        if 'stability' not in part_name:continue
        metrics={name:value for name,value in part.get('metrics',{}).items()
            if not name.startswith('condition:') and (name in ('gm_m','kg_m','list_rad','lcg_m','tcg_m') or 'area_' in name or 'containership_' in name)}
        margins={name:value for name,value in part.get('margins',{}).items() if 'area' in name or 'containership_' in name}
        if metrics or margins:areas.append(dict(part=part_name,metrics=metrics,margins=margins))
    breaches=[{key:v.get(key) for key in ('rule_id','measured','limit','unit','message')} for v in report.get('violations',[])
              if v.get('severity')=='hard' and ('second_check' in v.get('rule_id','') or 'righting' in v.get('rule_id',''))]
    return attempt['plan'],dict(attempt_index=index,trial_index=trial['attempt'],lifts=wanted_lifts,
                                native_departure_gm_m=wanted_gm,matching_attempts=len(options),
                                ballast_declared=attempt['plan'].get('ballast') is not None,
                                stored_native_area_metrics=areas,stored_area_breaches=breaches)


def run_one(size,gm_floor,mode,maxiter,tcg_halfwidth_m=None):
    import numpy as np
    from scipy.optimize import linprog,OptimizeWarning
    from stowage.model import Instance,MovePlan
    from alns import Problem
    from arrival import prepare,restow_options
    from arrival_model import Model
    from joint_loading import JointLoading
    from gap import Assignment,Settings
    from stow import block_layout
    from targets import derive
    seed={'small':331401024,'medium':331401048}[size]
    saved=HERE/'results/dev/joint-v1/layover'/f'train.layover.{size}.o30.s{seed}.json'
    row=json.loads(saved.read_text())
    reference,reference_meta=selected_first_reference(row)
    instance=Instance.model_validate_json((HERE/'cohort'/f'train.layover.{size}.o30.s{seed}.json').read_text())
    plan=MovePlan.model_validate(reference)
    joint=JointLoading(instance,plan,gm_floor=gm_floor,tank_mode=mode,tcg_halfwidth_m=tcg_halfwidth_m)
    branch=prepare(instance,dict(restow_options(instance))['all'],label='all')
    problem=Problem(branch.working);targets=derive(instance)
    model=Model(problem,branch,sight_draft_m=targets.departure_draught_m,sight_roof_margin_m=.15)
    settings=Settings(limit=.95,joint_loading=joint,lcg=None,tcg=None,vertical_max_tm=None,seed=0)
    solver=Assignment(model,targets.reference(instance.vessel.hull,gm=max(gm_floor,targets.arrival_gm_m)),settings,layout=block_layout(model))
    c,A,b,Aeq,beq=solver.build()
    bounds=np.c_[np.zeros(len(c)),np.full(len(c),np.inf)]
    bounds[:solver.n,1]=1.
    bounds[solver.n:solver.n+len(model.boxes),1]=0. # All modeled cargo required.
    solver._fixed_bounds(bounds[:,0],bounds[:,1])
    for k,(lo,hi) in enumerate(solver.joint_spec['bounds']):
        bounds[solver.joint_aux_start+k]=[lo,hi]
    names=solver.joint_spec['names'];offset=solver.joint_row_start
    result=dict(size=size,seed=seed,mode=mode,gm_floor=gm_floor,saved_result=str(saved),
        saved_sha256=hashlib.sha256(saved.read_bytes()).hexdigest(),reference=reference_meta,
        pairs=solver.n,boxes=len(model.boxes),fixed_pairs=len(solver.fixed_pairs),joint_rows=len(names),
        loading_metadata=joint.last_spec['metadata'],tests=[])
    dropped_families=(None,'gm','heel','draft','trim','mass','balances')+ (('tcg',) if tcg_halfwidth_m is not None else ())
    for dropped in dropped_families:
        removed=[offset+j for j,name in enumerate(names) if family(name)==dropped] if dropped else []
        keep=np.ones(len(b),bool);keep[removed]=False
        start=monotonic()
        with warnings.catch_warnings():
            warnings.simplefilter('ignore',OptimizeWarning)
            answer=linprog(c,A_ub=A[keep],b_ub=b[keep],A_eq=Aeq,b_eq=beq,bounds=bounds,method='highs',
                           options=dict(threads=1,maxiter=int(maxiter)))
        r=dict(dropped_family=dropped or 'none',dropped_rows=[names[j-offset] for j in removed],
               status=int(answer.status),message=answer.message,iterations=int(answer.nit),wall_s=monotonic()-start,
               all_boxes_required=True,physical_or_checker_certificate=False)
        if answer.x is not None:
            x=np.asarray(answer.x,float)
            r.update(objective=float(answer.fun),max_retained_row_violation=float(np.max(A[keep]@x-b[keep],initial=0.)),
                     max_assignment_equality_violation=float(np.max(abs(Aeq@x-beq),initial=0.)),
                     fractional_box_edges=int(np.sum((x[:solver.n]>1e-6)&(x[:solver.n]<1-1e-6))),
                     relaxed_tank_fractions=x[solver.joint_aux_start:solver.joint_aux_start+len(joint.tank_ids)].tolist(),
                     predicted_drafts_m=x[solver.joint_aux_start+len(joint.tank_ids):].reshape(3,2).tolist())
            violations=np.asarray(A@x-b).ravel()
            r['dropped_positive_violations']=[dict(row=names[j-offset],normalized_excess=float(violations[j])) for j in removed if violations[j]>1e-7]
        result['tests'].append(r)
        print(json.dumps(dict(size=size,dropped=r['dropped_family'],status=r['status'],s=round(r['wall_s'],3))),flush=True)
    return result


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--size',choices=('small','medium','both'),default='both')
    parser.add_argument('--gm-floor',type=float,default=1.5)
    parser.add_argument('--mode',choices=('half','pressed'),default='half')
    parser.add_argument('--maxiter',type=int,default=20000)
    parser.add_argument('--tcg-halfwidth-m',type=float,default=None)
    parser.add_argument('--out',type=Path,default=HERE/'results/warm-joint-row-families.json')
    args=parser.parse_args()
    marker=ROOT/'experiments/mvp/frontier/lab/c1/leader/BENCH-RUNNING'
    if marker.exists():raise SystemExit('Benchmark marker exists; root must allocate its sole CPU slot explicitly before this diagnostic')
    if not args.out.resolve().is_relative_to(HERE.resolve()):raise SystemExit('diagnostic output must remain inside this Cycle6 folder')
    start=monotonic()
    sources=('joint_lane_diagnose.py','joint_loading.py','ballast_loading.py','gap.py','arrival.py','arrival_model.py','targets.py','stow.py')
    out=dict(label='WARM DIAGNOSTIC: saved development reference, current affine loading LP, no cold solve or checker pass',
             created_utc=datetime.now(timezone.utc).isoformat(),pid=os.getpid(),load_start=list(os.getloadavg()),
             gm_floor=args.gm_floor,mode=args.mode,maxiter=args.maxiter,
             total_tcg_search_halfwidth_m=args.tcg_halfwidth_m,
             source_sha256={name:hashlib.sha256((HERE/name).read_bytes()).hexdigest() for name in sources},maps=[])
    for size in (('small','medium') if args.size=='both' else (args.size,)):
        out['maps'].append(run_one(size,args.gm_floor,args.mode,args.maxiter,args.tcg_halfwidth_m))
        args.out.parent.mkdir(parents=True,exist_ok=True)
        args.out.write_text(json.dumps(out,indent=2,sort_keys=True)+'\n')
    out.update(wall_s=monotonic()-start,load_end=list(os.getloadavg()))
    args.out.write_text(json.dumps(out,indent=2,sort_keys=True)+'\n')
    return 0


if __name__=='__main__':raise SystemExit(main())
