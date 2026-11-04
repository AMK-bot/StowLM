"""Detached, sequential one-core regression verification; no gold campaign."""
from __future__ import annotations
import argparse,datetime,json,os,resource,subprocess,sys,time
from pathlib import Path

HERE=Path(__file__).resolve().parent
ROOT=HERE.parents[2]


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--setting',choices=['off','on','regressions'])
    parser.add_argument('--regressions-only',action='store_true')
    args=parser.parse_args()
    if args.setting:
        import pytest
        from stowage.rules.catalogue import rule_freeze_v1
        paths=([str(path.relative_to(ROOT)) for path in sorted((ROOT/'tests/experiments').glob('test_gold*.py'))]
            if args.setting=='regressions' else ['tests/solver/test_exact_gold.py',
                'experiments/gold-plans/campaign/test_rule_binding.py'])
        with rule_freeze_v1(enabled=args.setting=='on'):
            return pytest.main([*paths,
                '-o','addopts=','--import-mode=importlib','-m','not slow and not gpu and not net',
                '-q','--tb=short',f'--basetemp=/private/tmp/w2-gold-fixes-{args.setting}',
                f'--junitxml={HERE}/gold-fixes-{args.setting}.xml'])
    env=os.environ.copy()
    env.update(UV_CACHE_DIR=str(ROOT/'.uv-cache'),OMP_NUM_THREADS='1',OPENBLAS_NUM_THREADS='1',
        VECLIB_MAXIMUM_THREADS='1',MKL_NUM_THREADS='1',NUMEXPR_NUM_THREADS='1')
    records=[]
    record_path=HERE/('gold-extra-verification.json' if args.regressions_only else 'gold-fixes-verification.json')
    for mode in (('regressions',) if args.regressions_only else ('off','on')):
        start=time.monotonic()
        command=[str(ROOT/'.venv/bin/python'),str(Path(__file__).resolve()),'--setting',mode]
        with (HERE/f'gold-fixes-{mode}.log').open('wb') as log:
            child=subprocess.Popen(command,cwd=ROOT,env=env,stdin=subprocess.DEVNULL,
                stdout=log,stderr=subprocess.STDOUT)
            (HERE/f'gold-fixes-{mode}.pid').write_text(str(child.pid)+'\n')
            record=dict(setting=mode,pid=child.pid,command=command,cores=1,gpu=False,
                started_at=datetime.datetime.now(datetime.timezone.utc).isoformat(),
                estimated_total_wall_s=180,estimated_peak_ram_gb=3,
                estimate_basis=('Existing gold regression suite; serial unit/persistence checks, bounded CPU fixtures, no campaign'
                    if args.regressions_only else '20 focused cases pass; bounded real checker <=10s per candidate; four checker calls across both settings plus serial unit/import overhead'))
            records.append(record);record_path.write_text(json.dumps(records,indent=2)+'\n')
            try:code=child.wait(timeout=180)
            except subprocess.TimeoutExpired:
                child.terminate()
                try:child.wait(timeout=10)
                except subprocess.TimeoutExpired:child.kill();child.wait()
                code=124
        record.update(exit_code=code,wall_seconds=time.monotonic()-start,
            max_direct_children_rss_bytes=resource.getrusage(resource.RUSAGE_CHILDREN).ru_maxrss)
        record_path.write_text(json.dumps(records,indent=2)+'\n')
        print(json.dumps(record),flush=True)
    return int(any(r['exit_code'] for r in records))


if __name__=='__main__':raise SystemExit(main())
