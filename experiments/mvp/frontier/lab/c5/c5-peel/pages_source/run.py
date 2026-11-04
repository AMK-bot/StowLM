"""Standalone cold ladder measurements and fresh-process checker re-derivation."""
from pathlib import Path
import sys,os,json,argparse,hashlib,time
HERE=Path(__file__).resolve().parent
ROOT=HERE.parents[5]
sys.path.insert(0,str(HERE))
sys.dont_write_bytecode=True
for name in ('OMP_NUM_THREADS','OPENBLAS_NUM_THREADS','VECLIB_MAXIMUM_THREADS','STOWAGE_IN_PORT_WORKERS'):
    os.environ[name]='1'
from runtime import load_engine
load_engine()
from stowage.io import load_instance
from stowage.checker import check
from stowage.model import MovePlan
from tiers import analyze_report
from method import solve, normal

def write(path,data):
    path.parent.mkdir(parents=True,exist_ok=True)
    path.write_text(json.dumps(data,indent=2,default=str)+'\n')

def main():
    p=argparse.ArgumentParser()
    p.add_argument('--list',type=Path)
    p.add_argument('--out',type=Path,required=True)
    p.add_argument('--verify',type=Path)
    p.add_argument('--normal',action='store_true')
    p.add_argument('--quick-paired',action='store_true')
    args=p.parse_args()
    args.out.parent.mkdir(parents=True,exist_ok=True)
    (args.out.parent/(args.out.name+'.pid')).write_text(str(os.getpid())+'\n')
    if args.verify:
        row=json.loads(args.verify.read_text())
        instance=load_instance(ROOT/row['input'])
        plan=MovePlan.model_validate(row['plan'])
        report=check(instance,plan); analyzed=analyze_report(report)
        previous=row['checker_report']
        same=(analyzed['level']==row['level'] and analyzed['broken_rule_ids']==
              sorted(r['rule_id'] for r in row['rules_broken']))
        # Compare full immutable violation evidence, including measured and limit.
        same=same and report.model_dump(mode='json')['violations']==previous['violations']
        write(args.out,dict(input=row['input'],level=analyzed['level'],matching=same,
                            report=report.model_dump(mode='json')))
        if not same: raise SystemExit('Fresh checker evidence mismatch')
        return
    for line in args.list.read_text().splitlines():
        if not line.strip() or line.startswith('#'): continue
        if 'valsplit' in line or 'testv3' in line: raise ValueError('Sealed path')
        instance=load_instance(ROOT/line)
        print('START',instance.id,'pid',os.getpid(),flush=True)
        started=time.monotonic()
        if args.quick_paired:
            reference,record=normal(instance)
            # Pass the actual ordinary-engine result through the control gate
            # once, isolating preservation from wall-budget run variability.
            result, row=solve(instance,normal_call=lambda *a,**k:(reference,record))
            equal=(reference is None and result is None) or (reference is not None and
                result is not None and reference.plan.model_dump(mode='json')==result.plan.model_dump(mode='json'))
            row.update(reference_plan=reference.plan.model_dump(mode='json') if reference else None,
                       L0_plan_unchanged=equal if reference is not None else None,
                       reference_passed=reference is not None,
                       comparison='same-run normal-engine result through ladder L0 gate',
                       L0_result_identity=(result is reference) if reference is not None else None)
        elif args.normal:
            result,record=normal(instance)
            row=dict(passed=result is not None,engine=record,
                     plan=result.plan.model_dump(mode='json') if result else None)
        else:
            result,row=solve(instance)
        row.update(input=line,instance_id=instance.id,wall_s=time.monotonic()-started,
                   peel_contract='D242',seed=0,source_manifest_sha256=hashlib.sha256((HERE/'frozen-code-hashes.json').read_bytes()).hexdigest(),
                   input_sha256=hashlib.sha256((ROOT/line).read_bytes()).hexdigest())
        write(args.out/(instance.id+'.json'),row)
        print(instance.id,row.get('level'),row.get('status'),round(row['wall_s'],2),flush=True)
if __name__=='__main__': main()
