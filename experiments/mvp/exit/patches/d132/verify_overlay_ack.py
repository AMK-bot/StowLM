"""CPU-only isolated D134 protocol probe; copies code, never loads model weights."""
from pathlib import Path
import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
from time import perf_counter

STAGE=Path(__file__).resolve().parent
ROOT=next(p for p in STAGE.parents if (p/'pyproject.toml').is_file())


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--record',type=Path,help='existing checker-verified tiny TRAIN winner, no reconstruction')
    parser.add_argument('--output',type=Path,help='save measured probe evidence under the patch directory')
    args=parser.parse_args()
    if args.record is not None:
        record=json.loads(args.record.read_text())
        if (record.get('report',{}).get('passed') is not True
                or not Path(record['instance_path']).resolve().is_relative_to(ROOT/'experiments/mvp/corpus/train/tiny')):
            raise ValueError('physics probe requires an existing full-pass tiny TRAIN winner')
    started=perf_counter()
    with tempfile.TemporaryDirectory(prefix='exit-d134-ack-') as directory:
        root=Path(directory)
        shutil.copytree(ROOT/'src',root/'src',ignore=shutil.ignore_patterns('__pycache__'))
        shutil.copytree(ROOT/'params',root/'params')
        shutil.copy2(ROOT/'pyproject.toml',root/'pyproject.toml')
        for name in ('runner','audit_runtime','final_recipe','progress','outcome_epoch','selection','checker_watchdog'):
            shutil.copy2(STAGE/(name+'.py'),root/'src/llm/exit'/(name+'.py'))
        for name in ('config.json','generation_config.json','tokenizer.json','tokenizer_config.json','model.safetensors.index.json'):
            dest=root/'models/glimmer-30b-4bit'/name;dest.parent.mkdir(parents=True,exist_ok=True)
            shutil.copy2(ROOT/'models/glimmer-30b-4bit'/name,dest)
        for name in ('data/raw/kcs/kcs.fix','experiments/mvp/gold_tools/driver.py'):
            dest=root/name;dest.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(ROOT/name,dest)
        if args.record is not None:
            # Private APFS clone: reuse qualified cache contents without sharing
            # writes with the live run. Neither downloaded nor executable data.
            for name in ('vessels','stability'):
                destination=root/'experiments/cache'/name;destination.parent.mkdir(parents=True,exist_ok=True)
                subprocess.run(['cp','-cR',str(ROOT/'experiments/cache'/name),str(destination)],check=True)
            shutil.copy2(args.record,root/'checked-winner.json')
        (root/'ack_fixture.py').write_text('''def quick_good(instance, plan):
    from stowage.model import CheckReport, PartReport
    parts={name:PartReport(name=name,ran=True) for name in
        ('moves','rules','voyage','stability','strength','visibility','lashing','constraints')}
    return CheckReport(passed=True,parts=parts)
''')
        (root/'probe.py').write_text('''import json,sys
from llm.exit.outcome_epoch import process_snapshot
from llm.exit.checker_watchdog import run_full_check
from ack_fixture import quick_good
expected=process_snapshot()
result=run_full_check(None,None,timeout_s=45,checker=quick_good,expected_outcome_epoch=expected)
ack=result.epoch_ack or {}
print(json.dumps(dict(available=result.available,reason=result.reason,epoch_ack_error=result.epoch_ack_error,
    pins=len(expected),pre_matches=ack.get('loaded_pre')==expected,post_matches=ack.get('loaded_post')==expected,
    imports=len(ack.get('imports',{})),resource_reads=len(ack.get('resources',{})),wall_s=result.wall_s,
    mlx_loaded=any(k=='mlx' or k.startswith('mlx.') for k in sys.modules))))
assert result.available and ack['loaded_pre']==expected and ack['loaded_post']==expected
''')
        if args.record is not None:
            (root/'probe.py').write_text('''import json,sys,resource
from pathlib import Path
from llm.exit.outcome_epoch import process_snapshot
from llm.exit.checker_watchdog import run_full_check
from stowage.io import load_instance
from stowage.model import MovePlan,CheckReport
from stowage.checker import report_matches_inputs
from stowage.frozen import require_clean
row=json.loads(Path('checked-winner.json').read_text())
instance=load_instance(row['instance_path']);plan=MovePlan.model_validate(row['plan'])
old_report=CheckReport.model_validate(row['report'])
require_clean(instance,plan,old_report,what='saved tiny physics ACK probe')
assert old_report.passed and report_matches_inputs(old_report,instance,plan)
expected=process_snapshot()
result=run_full_check(instance,plan,timeout_s=120,expected_outcome_epoch=expected)
ack=result.epoch_ack or {}
passed=bool(result.available and result.report and result.report.passed)
bound=bool(result.report and report_matches_inputs(result.report,instance,plan))
child_rss=resource.getrusage(resource.RUSAGE_CHILDREN).ru_maxrss
if sys.platform!='darwin':child_rss*=1024
print(json.dumps(dict(available=result.available,full_checker_passed=passed,input_binding_verified=bound,
    reason=result.reason,epoch_ack_error=result.epoch_ack_error,instance_path=row['instance_path'],
    pins=len(expected),pre_matches=ack.get('loaded_pre')==expected,post_matches=ack.get('loaded_post')==expected,
    nonce_acknowledged=bool(ack.get('request_id')),imports=len(ack.get('imports',{})),
    resource_reads=len(ack.get('resources',{})),wall_s=result.wall_s,checker_s=result.checker_s,
    weighted_moves=result.report.count.weighted_nonproductive if passed else None,
    child_peak_rss_bytes=int(child_rss),mlx_loaded=any(k=='mlx' or k.startswith('mlx.') for k in sys.modules))))
assert passed and bound and ack['loaded_pre']==expected and ack['loaded_post']==expected
''')
        env=dict(os.environ,PYTHONPATH=str(root/'src'),OMP_NUM_THREADS='1',OPENBLAS_NUM_THREADS='1',
            MKL_NUM_THREADS='1',VECLIB_MAXIMUM_THREADS='1',NUMEXPR_NUM_THREADS='1')
        process=subprocess.run([sys.executable,str(root/'probe.py')],cwd=root,env=env,
            text=True,capture_output=True,timeout=150 if args.record else 60)
        print(process.stdout.strip())
        if process.returncode:print(process.stderr.strip())
        evidence=dict(probe_wall_s=perf_counter()-started,returncode=process.returncode,gpu=False,
            stdout=process.stdout.strip(),stderr=process.stderr.strip(),
            saved_record=str(args.record.resolve()) if args.record else None)
        if args.output:
            if not args.output.resolve().is_relative_to(STAGE):raise ValueError('probe output must stay inside patch directory')
            args.output.write_text(json.dumps(evidence,indent=2)+'\n')
        print(json.dumps({key:value for key,value in evidence.items() if key not in ('stdout','stderr')}))
        return process.returncode


if __name__=='__main__':raise SystemExit(main())
