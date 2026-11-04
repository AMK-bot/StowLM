"""Aggregate measured small30 records across two exact operational-only changes.

This is a narrowly bound D131 compatibility finalizer, not a relaxed source
guard. It never decodes, checks, reconstructs plans, trains, or edits source or
the original audit files. No shipping plan is published, only measured reports.
"""
from __future__ import annotations

import argparse
from datetime import datetime,timezone
import hashlib
import importlib.util
import json
import math
from pathlib import Path
import time


ROOT=Path(__file__).resolve().parents[3]
OPERATIONAL=frozenset(('src/llm/exit/reverify.py','src/llm/exit/resources.py'))


class Pending(RuntimeError):
    pass


def read(path):return json.loads(Path(path).read_text())
def sha(path):return hashlib.sha256(Path(path).read_bytes()).hexdigest()
def digest(value):return hashlib.sha256(json.dumps(value,sort_keys=True,separators=(',',':'),allow_nan=False).encode()).hexdigest()


def _services():
    from llm.exit import resources
    from llm.exit.runner import source_pins,adapter_identity,write_json
    from llm.exit.replay import checker_epoch
    from llm.exit.outcomes import outcome_counts,checker_unavailable
    from llm.exit.identity import identity_guard
    from stowage.io import load_instance
    from stowage.model import CheckReport
    from stowage.checker import CHECKER_VERSION
    from stowage.frozen import require_clean
    path=ROOT/'experiments/mvp/exit/rebaseline_on_host.py'
    spec=importlib.util.spec_from_file_location('_d131_shared_validation',path)
    shared=importlib.util.module_from_spec(spec);spec.loader.exec_module(shared)
    return dict(resources=resources,source_pins=source_pins,adapter_identity=adapter_identity,
        write_json=write_json,checker_epoch=checker_epoch,outcome_counts=outcome_counts,
        checker_unavailable=checker_unavailable,identity_guard=identity_guard,
        load_instance=load_instance,CheckReport=CheckReport,CHECKER_VERSION=CHECKER_VERSION,
        require_clean=require_clean,validate_rows=shared._validate_rows)


def operational_compatibility(old,current,activation):
    changes={name for name in old.keys()|current.keys() if old.get(name)!=current.get(name)}
    if changes!=OPERATIONAL:
        raise RuntimeError('D131 compatibility requires exactly the two reviewed operational changes')
    if activation.get('authorization')!='D131' or set(activation.get('targets',{}))!=OPERATIONAL:
        raise RuntimeError('D131 operational activation inventory differs')
    for name in OPERATIONAL:
        binding=activation['targets'][name]
        if old.get(name)!=binding.get('old_sha256') or current.get(name)!=binding.get('new_sha256'):
            raise RuntimeError('D131 reviewed operational source hash mismatch: '+name)
    old_checker={name:value for name,value in old.items() if name.startswith('src/stowage/')}
    new_checker={name:value for name,value in current.items() if name.startswith('src/stowage/')}
    if not old_checker or old_checker!=new_checker:
        raise RuntimeError('D131 checker/physics epoch changed')
    return dict(changed_paths=sorted(changes),operational_targets=activation['targets'],
        checker_source_hashes_sha256=digest(old_checker),
        explanation='all measured; operational-only transition explicitly recorded; model/decoder/mask/checker/physics identical',
        shipping_plans_published=False,plans_reconstructed=False)


def validate_recipe(recipe):
    expected=dict(greedy=True,temperature=0.,samples=1,batched=True,batch_size=4,max_tokens=20000,
        layers=['production','lookahead','securing'],backtracking=False,
        ballast='deterministic scoped V4',ballast_s=90.,checker_watchdog_s=300.,
        lns=False,solver_cargo_fallback=False)
    if any(recipe.get(key)!=value for key,value in expected.items()):
        raise RuntimeError('D131 measured baseline recipe differs from prior greedy B4 audit')


def validate_pass(row,instance,parent_identity,services):
    if row.get('passed') is not True:return
    raw=row['verification']['report'];report=services['CheckReport'].model_validate(raw)
    services['require_clean'](instance,report,what='D131 measured audit report')
    parts={'moves','rules','voyage','stability','strength','visibility','lashing','constraints'}
    if (report.passed is not True or not parts.issubset(report.parts)
            or any(not part.ran or part.error for part in report.parts.values())
            or any(v.severity=='hard' for v in [*report.violations,
                *(v for part in report.parts.values() for v in part.violations)])
            or report.provenance.versions.get('checker')!=services['CHECKER_VERSION']
            or report.count is None or report.count.weighted_nonproductive!=row.get('weighted_moves')):
        raise RuntimeError('D131 measured pass lacks complete current checker evidence')
    parents=report.provenance.parents
    instance_hash=digest(instance.model_dump(mode='json'))
    bindings={p for p in parents if p.startswith('checker.instance.sha256:')}
    plans={p for p in parents if p.startswith('checker.plan.sha256:')}
    if (bindings!={'checker.instance.sha256:'+instance_hash} or len(plans)!=1
            or not all(len(p.removeprefix('checker.plan.sha256:'))==64 for p in plans)
            or 'adapter-weights-sha256:'+parent_identity['adapters.safetensors'] not in parents):
        raise RuntimeError('D131 report instance/plan/own-adapter binding differs')
    trace=row.get('generation_timings',{})
    if trace.get('status')!='complete' or not trace.get('tokens'):
        raise RuntimeError('D131 passing row lacks its complete own-model action trace')
    mask=row['verification'].get('mask',{})
    securing=mask.get('securing_metrics',{})
    if (mask.get('passed') is not True or mask.get('status')!='complete'
            or mask.get('actions_equal') is not True or mask.get('securing') is not True
            or mask.get('lookahead') is not True
            or mask.get('actions_completed')!=mask.get('actions_expected')
            or securing.get('bounds_available') is not True
            or securing.get('unavailable') or securing.get('fail_open')):
        raise RuntimeError('D131 passing row lacks complete layered mask/securing evidence')


def finalize(args):
    services=_services();source=Path(args.source_out).resolve();out=Path(args.out).resolve()
    if source==out:raise ValueError('D131 aggregation must preserve the original output directory')
    work=source/'rebaseline-current'
    try:
        lease=services['resources'].execution_lock()
        lease.__enter__()
    except RuntimeError as error:
        raise Pending('D131 measured GPU audit still owns the shared reservation') from error
    try:
        compute_path=work/'compute.json'
        if not compute_path.is_file():raise Pending('D131 GPU lease has not published measured completion timing')
        spec_path=work/'specification.json';spec=read(spec_path)
        old=spec['current_sources'];current=services['source_pins']()
        activation_path=Path(args.activation).resolve()
        compatibility=operational_compatibility(old,current,read(activation_path))
        validate_recipe(spec['recipe'])
        parent=Path(args.parent).resolve()
        if Path(spec['parent']).resolve()!=parent or services['adapter_identity'](parent)!=spec['parent_identity']:
            raise RuntimeError('D131 measured baseline adapter differs from original 8.1')
        listing=Path(args.audit_list).resolve()
        frozen=[str((ROOT/line.strip()).resolve()) for line in listing.read_text().splitlines()
                if line.strip() and not line.lstrip().startswith('#')]
        maps=spec['maps'];paths=[Path(row['path']).resolve() for row in maps]
        if (len(paths)!=30 or len(set(paths))!=30 or [str(p) for p in paths]!=frozen
                or sha(listing)!=spec['audit_list_sha256']):
            raise RuntimeError('D131 baseline frozen audit identities differ')
        epoch=hashlib.sha256(json.dumps(old,sort_keys=True).encode()).hexdigest()[:16]
        directory=work/f'epoch-{epoch}';identity_path=directory/'identity.json'
        identity=read(identity_path)
        if (identity.get('sources')!=old or identity.get('adapter')!=spec['parent_identity']
                or identity.get('maps')!=[[str(p),row['sha256']] for p,row in zip(paths,maps)]
                or identity.get('max_tokens')!=20000 or identity.get('batch_size')!=4):
            raise RuntimeError('D131 audit record identity differs from measured specification')
        records=[directory/f'{i:03d}.json' for i in range(30)]
        if not all(path.is_file() for path in records):raise Pending('D131 audit has fewer than 30 completed records')
        compute=read(compute_path);wall=compute.get('actual_wall_s')
        if (not isinstance(wall,(int,float)) or not math.isfinite(wall) or wall<=0
                or compute.get('estimate',{}).get('gpu') is not True):
            raise RuntimeError('D131 audit lease lacks measured GPU wall timing')
        inputs=[spec_path,identity_path,compute_path,activation_path,listing,*paths,*records]
        inventory={str(path):sha(path) for path in inputs}
        rows=[read(path) for path in records]
        guard=services['identity_guard'](Path(args.corpus))
        for index,(row,path,mapping) in enumerate(zip(rows,paths,maps)):
            if sha(path)!=mapping['sha256'] or Path(row['instance_path']).resolve()!=path:
                raise RuntimeError('D131 audit map hash/row identity changed')
            instance=services['load_instance'](path)
            guard.require_allowed(instance,role='audit',input_path=path)
            if row['instance_id']!=instance.id:raise RuntimeError('D131 row instance ID differs')
            validate_pass(row,instance,spec['parent_identity'],services)
        securing=services['validate_rows'](rows,paths,services)
        if services['source_pins']()!=current or any(sha(path)!=value for path,value in inventory.items()):
            raise RuntimeError('D131 inputs/source changed during finalization')
        counts=services['outcome_counts'](rows)
        moves=[row['weighted_moves'] for row in rows if row.get('passed') is True]
        summary=dict(**counts,mean_weighted_moves=sum(moves)/len(moves) if moves else None,
            audit_s=wall,peak_rss_bytes=compute.get('process_peak_rss_bytes'),securing=securing,
            current_code_smoke_required_for_generation=True,
            measurement_note=compatibility['explanation'])
        binding=dict(adapter=spec['parent_identity'],baseline_adapter=str(parent),
            checker_epoch=services['checker_epoch'](),maps=maps)
        published_spec={**spec,'current_sources':current,'decoded_source_pins':old}
        compatibility.update(activation_path=str(activation_path),activation_sha256=sha(activation_path),
            measured_inputs=inventory,checker_epoch=binding['checker_epoch'])
        target=out/'audit-baseline';target.mkdir(parents=True,exist_ok=True)
        writer=services['write_json']
        writer(target/'summary.json',rows);writer(target/'baseline-binding.json',binding)
        writer(target/'availability-summary.json',summary)
        writer(out/'baseline-timing.json',dict(audit_s=wall,peak_rss_bytes=summary['peak_rss_bytes'],
            measurement_kind='D131 measured GPU decode/check lease; operational-only transition explicitly recorded'))
        outputs=[target/'summary.json',target/'baseline-binding.json',target/'availability-summary.json',out/'baseline-timing.json']
        ready=dict(schema='exit-d131-rebaseline-v1',status='ready',utc=datetime.now(timezone.utc).isoformat(),
            specification=published_spec,decoded_source_pins=old,compatibility_receipt=compatibility,
            summary=summary,device=dict(measured_gpu_lease=True),outputs={str(path):sha(path) for path in outputs},
            training_performed=False,test_inputs_touched=False,generation_smoke_pass_claimed=False)
        ready['ready_sha256']=digest(ready)
        writer(out/'rebaseline-ready.json',ready)
        return ready
    finally:
        lease.__exit__(None,None,None)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source-out',type=Path,default=ROOT/'experiments/mvp/exit/deployment-d129')
    parser.add_argument('--out',type=Path,default=ROOT/'experiments/mvp/exit/deployment-d131')
    parser.add_argument('--parent',type=Path,default=ROOT/'experiments/mvp/sft/stowlm-v1/adapter')
    parser.add_argument('--activation',type=Path,default=ROOT/'experiments/mvp/exit/patches/d129/D131-activation.json')
    parser.add_argument('--audit-list',type=Path,default=ROOT/'experiments/mvp/valsplit/audit_small30.txt')
    parser.add_argument('--corpus',type=Path,default=ROOT/'experiments/mvp/corpus')
    parser.add_argument('--wait',action='store_true')
    parser.add_argument('--timeout-seconds',type=float,default=7200)
    args=parser.parse_args();deadline=time.monotonic()+args.timeout_seconds
    while True:
        try:ready=finalize(args);break
        except Pending as error:
            if not args.wait or time.monotonic()>=deadline:raise
            print(json.dumps(dict(status='waiting',reason=str(error))),flush=True);time.sleep(20)
    print(json.dumps(dict(status='ready',summary=ready['summary'],marker=str(args.out/'rebaseline-ready.json'))),flush=True)


if __name__=='__main__':main()
