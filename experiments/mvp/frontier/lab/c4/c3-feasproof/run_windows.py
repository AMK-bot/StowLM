"""Serial C4 joint-window sweep; numerical infeasibility never proves a map.

Run only while holding the executive's CPU token. This process starts no
workers and performs no full-checker calls; controls.json supplies separately
fresh full-check evidence. All output paths stay inside this C4 directory.
"""
from __future__ import annotations

import argparse
from collections import defaultdict
from datetime import datetime, timezone
import hashlib
import importlib.util
import json
import math
import os
from pathlib import Path
import re
import sys
from time import monotonic

HERE=Path(__file__).resolve().parent
ROOT=HERE.parents[5]
LAB=HERE.parents[1]
for key in ('OMP_NUM_THREADS','OPENBLAS_NUM_THREADS','VECLIB_MAXIMUM_THREADS'):
    os.environ[key]='1'
os.environ['PYTHONDONTWRITEBYTECODE']='1'
os.environ['STOWAGE_VESSEL_CACHE_DIR']=str(HERE/'cache')
sys.dont_write_bytecode=True
sys.path.insert(0,str(ROOT/'src'))
sys.path.insert(0,str(HERE))

import audit
from joint_model import (build_joint_model, certified_cargo_bounds,
    apply_cargo_bound_windows, export_windows, project_plan)
from stowage.io import load_instance
from stowage.model import MovePlan
from stowage.frozen import active_stubs
from stowage.rules.catalogue import rule_freeze_v1_enabled


def local_output(path):
    path=Path(path)
    if not path.is_absolute():path=ROOT/path
    path=path.resolve()
    if not path.is_relative_to(HERE):
        raise ValueError('Output must remain inside the C4 c3-feasproof directory')
    return path


def atomic_json(path,value):
    path=local_output(path)
    path.parent.mkdir(parents=True,exist_ok=True)
    temporary=path.with_suffix(path.suffix+'.tmp')
    temporary.write_text(json.dumps(value,indent=2,sort_keys=True,allow_nan=False)+'\n')
    temporary.replace(path)


def safe_json(value):
    """Nonfinite diagnostics are explicit strings, never changed plan scalars."""
    if isinstance(value,float) and not math.isfinite(value):
        return 'nan' if math.isnan(value) else ('+inf' if value>0 else '-inf')
    if isinstance(value,dict):return {str(k):safe_json(v) for k,v in value.items()}
    if isinstance(value,(list,tuple)):return [safe_json(v) for v in value]
    return value


def load_physics():
    path=HERE/'_legacy/physics.py'
    spec=importlib.util.spec_from_file_location('_c4_windows_pinned_physics',path)
    module=importlib.util.module_from_spec(spec)
    sys.modules[spec.name]=module
    spec.loader.exec_module(module)
    return module


def load_rod_bound():
    path=HERE/'_legacy/rod_bound.py'
    spec=importlib.util.spec_from_file_location('_c4_windows_pinned_rod',path)
    module=importlib.util.module_from_spec(spec)
    sys.modules[spec.name]=module
    spec.loader.exec_module(module)
    return module


def selected_inputs(controls,which='all'):
    medium=[line.strip() for line in (LAB/'sets/medium12.txt').read_text().splitlines()
            if line.strip() and not line.lstrip().startswith('#')]
    controls_inputs=sorted({row['input'] for row in controls
                           if '/f60-' in row['input'] or '/f70-' in row['input']})
    inputs=medium if which=='medium12' else controls_inputs if which=='controls' else medium+controls_inputs
    return list(dict.fromkeys(inputs)),set(medium),set(controls_inputs)


def independent_support_audit(support):
    """Use audit.py's separate exact implementation on every retained dual."""
    checked={}
    for name,record in support['directions'].items():
        if not record.get('certificate_verified'):
            checked[name]=dict(verified=False,reason='no completed LP support certificate')
            continue
        fresh=audit.audit_support_certificate(support['matrix'],record['certificate'])
        recorded=record['verification']
        if (fresh['exact_numerator']!=recorded['exact_numerator']
                or fresh['exact_denominator']!=recorded['exact_denominator']):
            raise ValueError('Independent exact support bound disagreement: '+name)
        checked[name]=fresh
    return dict(verified=all(r['verified'] for r in checked.values()),directions=checked,
                physical_proof=False,scope='serialized packing matrix only')


def compact_witness(result):
    witness=result.get('witness')
    if witness is None:return None
    fields=('phases','tank_masses_kg','deck_count_by_bay','deck_mass_by_bay',
            'cargo_vertical_moment_lower_kg_m','max_row_violation','scope')
    return {key:witness[key] for key in fields if key in witness}


def source_manifest():
    paths=[Path(__file__),HERE/'joint_model.py',HERE/'audit.py',HERE/'_legacy/physics.py',HERE/'_legacy/rod_bound.py']
    return {str(p.resolve().relative_to(ROOT)):audit.digest(p) for p in paths}


def summarize(rows,registry,observations,started,inputs,medium,control_inputs):
    accepted=[r for r in observations.values() if r.get('accepted')]
    projections=[r for row in rows.values() for r in row.get('control_projections',[])]
    supported=sum(row.get('outer_solve',{}).get('has_witness',False) for row in rows.values())
    unknown=sum(row.get('verdict')=='unknown' for row in rows.values())
    legal=sum(row.get('verdict')=='feasible_with_legal_witness' for row in rows.values())
    existing_calls=len(observations)
    summary=dict(maps_requested=len(inputs),maps_complete=len(rows),medium12_maps=len(medium),
        preselected_passing_f60_f70_input_maps=len(control_inputs),outer_witnesses=supported,
        unknown_maps=unknown,known_legal_maps=legal,whole_map_infeasibility_proofs=0,
        model_stage_errors=sum(row.get('model_stage_error',False) for row in rows.values()),
        controls_registered=len(registry),existing_fresh_full_checker_calls=existing_calls,
        existing_fresh_full_checker_passes=len(accepted),runner_full_checker_calls=0,
        controls_projected=len(projections),controls_projection_passes=sum(r.get('projected',False) for r in projections),
        window_containment_passes=sum(r.get('window_audit',{}).get('contained',False) for r in projections),
        wall_s=monotonic()-started,
        gate='existing ordinary full checks establish legal controls; LP witnesses establish no legal pass',
        report_text=(f"Class/tier transportation with shared tanks covers all three D57 phases. "
            f"{len(rows)}/{len(inputs)} maps processed; {supported} outer witnesses, {unknown} unknown maps, "
            f"zero infeasibility proofs. Existing controls passed {len(accepted)}/{existing_calls} fresh full checks; "
            f"this runner made none. {sum(r.get('projected',False) for r in projections)}/{len(projections)} exact plans "
            f"project into the joint rows. Finite-angle area remains a separate envelope obligation. "
            f"Keep the algebraic support certificates and phase windows; no physical infeasibility claim."))
    return summary


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out',default=str(HERE/'windows.json'))
    parser.add_argument('--controls',default=str(HERE/'controls.json'))
    parser.add_argument('--skip-support',action='store_true',help='Pilot: omit the four moment LPs/certificates')
    parser.add_argument('--lp-seconds',type=float,default=5.)
    parser.add_argument('--support-seconds',type=float,default=3.,help='Per moment direction; four directions/map')
    parser.add_argument('--set',choices=('all','medium12','controls'),default='all')
    parser.add_argument('--limit',type=int,help='Optional pilot prefix; not a full cohort result')
    args=parser.parse_args()
    if args.lp_seconds<=0 or args.support_seconds<=0:
        raise ValueError('Positive LP allowances required')
    if active_stubs():
        raise ValueError('Active frozen stubs are unsupported; no ordinary-rule claim can be made')
    out=local_output(args.out)
    observed_raw=json.loads(Path(args.controls).read_text())
    if not observed_raw.get('complete'):
        raise ValueError('controls.json is incomplete')
    observations={r['control_id']:r for r in observed_raw['controls']}
    registry=audit.positive_controls()
    groups=defaultdict(list)
    for control in registry:
        groups[control['input']].append(control)
    inputs,medium,control_inputs=selected_inputs(registry,args.set)
    if args.limit is not None:
        if args.limit<=0:raise ValueError('Positive pilot limit required')
        inputs=inputs[:args.limit]
    physics=load_physics(); rod=load_rod_bound()
    manifest=source_manifest()
    rows={}; started=monotonic()
    metadata=dict(started_utc=datetime.now(timezone.utc).isoformat(),pid=os.getpid(),
        one_cpu_process=True,numerical_threads=1,rule_freeze_v1=rule_freeze_v1_enabled(),
        benchmark_marker_present=(LAB/'c1/leader/BENCH-RUNNING').exists(),
        controls_sha256=audit.digest(args.controls),experimental_sources=manifest,
        controls_scope='preselected passing development f60/f70 plans and warm D185 witness',
        support_enabled=not args.skip_support,lp_seconds=args.lp_seconds,
        support_seconds_per_direction=args.support_seconds,physical_infeasibility_claims=False)
    def persist(complete=False):
        summary=summarize(rows,registry,observations,started,inputs,medium,control_inputs)
        atomic_json(out,safe_json(dict(metadata=metadata,maps=rows,summary=summary,complete=complete)))
    persist()
    for path in inputs:
        stage=monotonic(); identifier=Path(path).stem
        known_accepted=[c['control_id'] for c in groups[path]
                        if observations.get(c['control_id'],{}).get('accepted')]
        row=dict(input=path,verdict='unknown',physical_proof=False,full_checker_calls=0,
                 complete=False,existing_full_checker_passed_controls=known_accepted,
                 scope='whole-map outward relaxation; no finite-angle physical contradiction')
        try:
            raw,sha=audit.load_raw(path)
            instance=load_instance(ROOT/path); identifier=instance.id
            row.update(instance_id=instance.id,input_sha256=sha,n_boxes=len(instance.load_list),
                       cohort='medium12' if path in medium else 'preselected passing f60/f70')
            built_at=monotonic()
            model=build_joint_model(instance,physics_module=physics,gm_lo=0.)
            rod.attach_rod_rows(model,instance,gm_lo=0.)
            row["securing_bound"]=model.metadata["rod"]
            row['build_s']=monotonic()-built_at
            row['variables']=model.n_variables;row['constraints']=len(model.rows)
            if not args.skip_support:
                support=certified_cargo_bounds(model,seconds_per_lp=args.support_seconds)
                support['independent_audit']=independent_support_audit(support)
                certpath=out.parent/'support_certificates'/(re.sub(r'[^A-Za-z0-9_.-]','_',instance.id)+'.json')
                atomic_json(certpath,safe_json(dict(instance_id=instance.id,input_sha256=sha,
                    experimental_sources=manifest,**support)))
                row['support_certificates']=dict(path=str(certpath.relative_to(ROOT)),
                    sha256=audit.digest(certpath),wall_s=support['wall_s'],cargo_bounds=support['cargo_bounds'],
                    independent_audit=support['independent_audit'])
                apply_cargo_bound_windows(model,instance,support)
            window=export_windows(model)
            result=model.solve(time_limit=args.lp_seconds)
            row['outer_solve']=dict(status=int(result.status),message=result.message,
                wall_s=result['wall_s'],has_witness=result.x is not None,
                interpretation='numerical status of the outer LP; never a physical infeasibility proof')
            row.update(window)
            row['guidance_targets']=compact_witness(result) or {}
            if result.x is not None:
                row['guidance_targets']['necessary_rod_ratios']=rod.evaluate_rod_rows(model,result.x)
            row['raw_mass_window_audit']=audit.audit_raw_mass_windows(raw,window)
            row['control_projections']=[]
            accepted_on_map=[]
            for control in groups[path]:
                observation=observations.get(control['control_id'])
                if observation is None:
                    raise ValueError('Missing fresh observation for control '+control['control_id'])
                if control['input_sha256']!=sha or observation['input_sha256']!=sha:
                    raise ValueError('Control input identity mismatch')
                projection=project_plan(model,instance,MovePlan.model_validate(control['plan']),
                                        phase_metrics=observation['phases'])
                projection.pop('variable_values',None)
                projection.update(control_id=control['control_id'],
                    full_checker_passed=bool(observation.get('accepted')),
                    window_audit=audit.audit_window_containment(window,observation))
                row['control_projections'].append(projection)
                if observation.get('accepted'):
                    accepted_on_map.append(control['control_id'])
            row['existing_full_checker_passed_controls']=accepted_on_map
            if accepted_on_map:
                row['verdict']='feasible_with_legal_witness'
                row['legal_witness_source']='fresh ordinary full checks retained in controls.json; not a new constructor result'
        except Exception as error:
            row.update(error_type=type(error).__name__,error=str(error),
                verdict='feasible_with_legal_witness' if known_accepted else 'unknown',
                model_stage_error=True,physical_proof=False)
        row['physical_verdict']=row['verdict']
        row.update(complete=True,wall_s=monotonic()-stage)
        rows[identifier]=row
        persist()
        print(json.dumps(dict(instance_id=identifier,verdict=row['verdict'],wall_s=row['wall_s'],
            outer_status=row.get('outer_solve',{}).get('status'),error=row.get('error')),allow_nan=False),flush=True)
    metadata['experimental_sources_unchanged']=manifest==source_manifest()
    metadata['native_sources']=audit.native_sources()
    native_differences={}
    for control_id,observation in observations.items():
        differences=[name for name,sha in observation.get('native_source_sha256',{}).items()
                     if name in metadata['native_sources'] and metadata['native_sources'][name]!=sha]
        if differences:native_differences[control_id]=differences
    metadata['native_control_source_differences']=native_differences
    metadata['finished_utc']=datetime.now(timezone.utc).isoformat()
    persist(complete=True)


if __name__=='__main__':
    main()
