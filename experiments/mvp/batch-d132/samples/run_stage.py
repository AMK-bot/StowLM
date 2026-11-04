"""Admit, reserve, run and reap one sample worker; no launch on failed admission."""
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[3]
RUNTIME = Path('/private/tmp/stowlm-d132-assembly-20261030')


def run(args):
    result = subprocess.run([sys.executable, *map(str, args)], cwd=ROOT, check=False)
    if result.returncode:
        raise RuntimeError(f'Controller prerequisite failed: {args}: {result.returncode}')


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--job', required=True)
    parser.add_argument('--sample', required=True)
    parser.add_argument('--output', required=True)
    parser.add_argument('--phase', required=True)
    parser.add_argument('--runtime', choices=['baseline', 'candidate'], default='candidate')
    parser.add_argument('--estimate-s', type=float, default=180)
    parser.add_argument('--limit', type=float, default=240)
    parser.add_argument('--append-val', action='store_true')
    parser.add_argument('extra', nargs=argparse.REMAINDER)
    args = parser.parse_args()
    os.environ['STOWAGE_VESSEL_SINGLEFLIGHT'] = '1'
    run([HERE / 'admission.py'])
    if not json.loads((HERE / 'admission.json').read_text())['fits']:
        ledger = (ROOT / 'experiments/fleet/ledger.md').read_text()
        if '| mvp-construct/D132-samples |' not in ledger:
            run([HERE / 'ledger.py', 'queue', '--job', args.job, '--estimate-s', args.estimate_s])
        print(json.dumps({'not_launched': True, 'reason': 'fresh_admission_does_not_fit'}))
        return 75
    run([HERE / 'ledger.py', 'reserve', '--job', args.job, '--estimate-s', args.estimate_s])
    process = HERE / ('process-' + args.job)
    command = [HERE / ('append_val.py' if args.append_val else 'worker.py'),
               '--runtime', RUNTIME / args.runtime, '--output', HERE / args.output]
    if not args.append_val:
        command += ['--sample', args.sample, '--phase', args.phase]
    command += args.extra[1:] if args.extra[:1] == ['--'] else args.extra
    try:
        run([HERE / 'owned.py', '--out', process, '--runtime', RUNTIME / args.runtime,
             '--reservation', HERE / f'ledger-{args.job}.json', '--limit', args.limit,
             '--ram-gb', '4', '--', *command])
    finally:
        record = process / 'process.json'
        if record.exists() and json.loads(record.read_text()).get('reaped'):
            run([HERE / 'ledger.py', 'finish', '--job', args.job, '--record', record])
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
