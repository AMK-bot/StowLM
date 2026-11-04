"""D131 standalone fresh 8.1 small30 baseline; explicit Metal-host invocation.

Run no training, reuse no historical cargo sequences, and create no smoke-pass
claim. The later generations controller retains its own current-code smoke gate.
This script publishes only after fresh masked greedy decoding and full checks.
"""
from __future__ import annotations

import argparse
from datetime import datetime,timezone
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import resource
import sys
import time
from types import SimpleNamespace


ROOT=Path(__file__).resolve().parents[3]
DEFAULT_OUT=ROOT/'experiments/mvp/exit/deployment-d131'
DEFAULT_PARENT=ROOT/'experiments/mvp/sft/stowlm-v1/adapter'
DEFAULT_LIST=ROOT/'experiments/mvp/valsplit/audit_small30.txt'


def _sha(path):return hashlib.sha256(Path(path).read_bytes()).hexdigest()
def _read(path):return json.loads(Path(path).read_text())
def _digest(value):return hashlib.sha256(json.dumps(value,sort_keys=True,separators=(',',':'),allow_nan=False).encode()).hexdigest()


def _cached_specification_matches(ready,expected):
    """Accept only an identical spec or the exact reviewed D131 ops receipt."""
    actual=ready.get('specification')
    if actual==expected:return True
    if not isinstance(actual,dict) or set(actual)!=set(expected)|{'decoded_source_pins'}:
        return False
    if any(actual.get(key)!=value for key,value in expected.items()):return False
    old=actual['decoded_source_pins']
    if old!=ready.get('decoded_source_pins'):return False
    receipt=ready.get('compatibility_receipt',{})
    activation=ROOT/'experiments/mvp/exit/patches/d129/D131-activation.json'
    if (receipt.get('activation_path')!=str(activation)
            or receipt.get('activation_sha256')!=_sha(activation)):
        return False
    path=ROOT/'experiments/mvp/exit/finalize_rebaseline_d131.py'
    spec=importlib.util.spec_from_file_location('_d131_cached_compatibility',path)
    helper=importlib.util.module_from_spec(spec);spec.loader.exec_module(helper)
    try:proof=helper.operational_compatibility(old,expected['current_sources'],_read(activation))
    except (RuntimeError,TypeError,KeyError,AttributeError):return False
    return all(receipt.get(key)==value for key,value in proof.items())


def _services():
    from llm.exit import resources
    from llm.exit.runner import evaluate,source_pins,adapter_identity,write_json
    from llm.exit.runtime import preflight_mlx_device,release_model
    from llm.exit.audit import frozen_audit_paths
    from llm.exit.replay import checker_epoch
    from llm.exit.outcomes import outcome_counts,checker_unavailable
    path=ROOT/'experiments/mvp/exit/deploy_when_ready.py'
    spec=importlib.util.spec_from_file_location('_d131_completion',path)
    deployment=importlib.util.module_from_spec(spec);spec.loader.exec_module(deployment)
    return dict(resources=resources,evaluate=evaluate,source_pins=source_pins,
        adapter_identity=adapter_identity,write_json=write_json,
        preflight_mlx_device=preflight_mlx_device,release_model=release_model,
        frozen_audit_paths=frozen_audit_paths,checker_epoch=checker_epoch,
        outcome_counts=outcome_counts,checker_unavailable=checker_unavailable,
        completion_ready=deployment.completion_ready)


def _rss_bytes():
    amount=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return int(amount if sys.platform=='darwin' else amount*1024)


def _validate_rows(rows,paths,services):
    if len(rows)!=30 or len(paths)!=30:
        raise RuntimeError('D131 baseline must cover all frozen 30 small audit maps')
    if {str(Path(row['instance_path']).resolve()) for row in rows}!={str(Path(p).resolve()) for p in paths}:
        raise RuntimeError('D131 baseline audit map coverage differs')
    if len({row['instance_id'] for row in rows})!=30:
        raise RuntimeError('D131 baseline contains duplicate instance identities')
    evaluations=0
    for row in rows:
        securing=row.get('generation_timings',{}).get('securing')
        if (not isinstance(securing,dict) or securing.get('bounds_available') is not True
                or securing.get('unavailable') or securing.get('fail_open')):
            raise RuntimeError('D131 securing unavailable/fail-open; baseline not published')
        evaluations+=securing.get('evaluations',0)
        if row.get('passed') is True:
            check=row.get('verification',{})
            if (services['checker_unavailable'](row) or check.get('report',{}).get('passed') is not True
                    or check.get('mask',{}).get('passed') is not True):
                raise RuntimeError('D131 passing row lacks current full checker/layered mask pass')
    if evaluations<=0:raise RuntimeError('D131 securing observed zero evaluations')
    return dict(evaluations=evaluations,unavailable=0,fail_open=0,bounds_available=True)


def run(args):
    for name in ('OMP_NUM_THREADS','OPENBLAS_NUM_THREADS','MKL_NUM_THREADS',
                 'VECLIB_MAXIMUM_THREADS','NUMEXPR_NUM_THREADS'):os.environ[name]='1'
    os.environ['STOWLM_MLX_CACHE_LIMIT_GB']='2'
    services=_services()
    out,parent=Path(args.out).resolve(),Path(args.parent).resolve()
    completion=parent.parent/'DONE.json'
    if not services['completion_ready'](parent,completion):
        raise RuntimeError('completed original 8.1 parent required before rebaseline')
    resources=services['resources'];resources.require_current_parent(parent)
    # Preflight never samples and fails before any queue/baseline publication.
    device=services['preflight_mlx_device']()
    work=out/'rebaseline-current';work.mkdir(parents=True,exist_ok=True)
    paths=services['frozen_audit_paths'](ROOT,Path(args.corpus),Path(args.audit_list),work)
    pins=services['source_pins']();identity=services['adapter_identity'](parent)
    code_ready=getattr(args,'code_ready',None)
    if code_ready is not None:
        if not Path(code_ready).is_file() or _read(code_ready).get('source_pins')!=pins:
            raise RuntimeError('D131 source-freeze marker missing or differs from loaded source epoch')
    recipe=dict(name='D131-prior-loop-baseline',greedy=True,temperature=0.,samples=1,
        batched=True,batch_size=4,max_tokens=20000,
        layers=['production','lookahead','securing'],backtracking=False,
        ballast='deterministic scoped V4',ballast_s=90.,checker_watchdog_s=300.,
        lns=False,solver_cargo_fallback=False,
        note='same greedy batched recipe as the original loop baseline, newly decoded under current code')
    binding=dict(adapter=identity,baseline_adapter=str(parent),checker_epoch=services['checker_epoch'](),
        maps=[dict(path=str(Path(p).resolve()),sha256=_sha(p)) for p in paths])
    specification=dict(parent=str(parent),parent_identity=identity,current_sources=pins,
        maps=binding['maps'],audit_list_sha256=_sha(args.audit_list),recipe=recipe)
    marker=out/'rebaseline-ready.json'
    if marker.exists():
        ready=_read(marker)
        payload={key:value for key,value in ready.items() if key!='ready_sha256'}
        if (ready.get('schema')!='exit-d131-rebaseline-v1' or ready.get('status')!='ready'
                or ready.get('ready_sha256')!=_digest(payload)
                or not _cached_specification_matches(ready,specification)):
            raise RuntimeError('D131 ready marker belongs to changed parent/source/maps/recipe')
        if len(ready.get('outputs',{}))!=4:
            raise RuntimeError('D131 published baseline output inventory missing')
        if any(_sha(path)!=digest for path,digest in ready.get('outputs',{}).items()):
            raise RuntimeError('D131 published baseline changed after readiness')
        return ready
    started=time.monotonic();original_owner=resources.OWNER
    resources.OWNER='mvp-exit/d131-baseline'
    try:
        # Measured old baseline: 698.7s, resident ~16GB with the 2GB cache cap.
        # APPROXIMATION: reserve 24GB and 1500s for one verifier + one GPU host
        # while CPU re-verification/gold continue under their own reservations.
        with resources.reservation(work,cores=args.workers+1,ram_gb=24+2*(args.workers-1),
                gpu=True,estimate_s=1500,load_policy='gpu_generation'):
            options=SimpleNamespace(out=out,corpus=Path(args.corpus),max_tokens=20000,
                audit_batch_size=4,workers=args.workers,ballast_s=90.,_loaded_source_pins=pins)
            services['write_json'](work/'specification.json',specification)
            rows=services['evaluate'](parent,paths,work,options)
            securing=_validate_rows(rows,paths,services)
            if (services['source_pins']()!=pins or services['adapter_identity'](parent)!=identity
                    or any(_sha(row['path'])!=row['sha256'] for row in binding['maps'])
                    or _sha(args.audit_list)!=specification['audit_list_sha256']):
                raise RuntimeError('D131 source/parent/frozen audit inputs changed; baseline not published')
            counts=services['outcome_counts'](rows)
            moves=[row['weighted_moves'] for row in rows if row.get('passed') is True
                   and not services['checker_unavailable'](row)]
            summary=dict(**counts,mean_weighted_moves=sum(moves)/len(moves) if moves else None,
                audit_s=time.monotonic()-started,peak_rss_bytes=_rss_bytes(),securing=securing,
                current_code_smoke_required_for_generation=True)
            # Binding/summary are individual atomic writes. Consumers must wait
            # for rebaseline-ready.json, published last and bound to both bytes.
            target=out/'audit-baseline';target.mkdir(parents=True,exist_ok=True)
            services['write_json'](target/'baseline-binding.json',binding)
            services['write_json'](target/'summary.json',rows)
            services['write_json'](target/'availability-summary.json',summary)
            services['write_json'](out/'baseline-timing.json',dict(audit_s=summary['audit_s'],
                measurement_kind='D131 fresh current-code GPU greedy decode + full verification',
                peak_rss_bytes=summary['peak_rss_bytes']))
            outputs=[target/'baseline-binding.json',target/'summary.json',
                     target/'availability-summary.json',out/'baseline-timing.json']
            ready=dict(schema='exit-d131-rebaseline-v1',status='ready',
                utc=datetime.now(timezone.utc).isoformat(),specification=specification,
                summary=summary,device=device,outputs={str(p):_sha(p) for p in outputs},
                training_performed=False,test_inputs_touched=False,
                generation_smoke_pass_claimed=False)
            ready['ready_sha256']=_digest(ready)
            services['write_json'](marker,ready)
            return ready
    finally:
        services['release_model']()
        resources.OWNER=original_owner


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out',type=Path,default=DEFAULT_OUT)
    parser.add_argument('--parent',type=Path,default=DEFAULT_PARENT)
    parser.add_argument('--corpus',type=Path,default=ROOT/'experiments/mvp/corpus')
    parser.add_argument('--audit-list',type=Path,default=DEFAULT_LIST)
    parser.add_argument('--workers',type=int,default=1,choices=(1,2))
    parser.add_argument('--code-ready',type=Path,
        default=ROOT/'experiments/mvp/exit/deployment-d129/D131-CODE.READY')
    args=parser.parse_args()
    ready=run(args)
    print(json.dumps(dict(status=ready['status'],summary=ready['summary'],marker=str(args.out/'rebaseline-ready.json'))),flush=True)


if __name__=='__main__':main()
