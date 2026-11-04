"""D190 K1c: K1b's exact recipe with --port-rule, same 55 maps and judge, baseline block-rule. Runs beside the null arm on a shared GPU (leader, 31 Oct).
Detached, resumable, sealed. Usage: k1c_driver.py [workers=4] [shards=2]
"""
from __future__ import annotations

from concurrent.futures import ProcessPoolExecutor, ThreadPoolExecutor
from datetime import datetime
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys

HERE = Path(__file__).resolve().parent
OUT = HERE/'k1c'
os.environ.update(K1B_OUT=str(OUT), COMPACT_FINISHER_S='600')  # before compact and judge are imported; spawned workers inherit it
sys.path.insert(0, str(HERE.parent))
import judge
import k1c_report
import port_mask

ROOT = HERE.parents[3]
PYTHON = str(ROOT/'.venv/bin/python')
K1B = json.loads((HERE/'manifest.json').read_text())
ADAPTER = 'experiments/mvp/sft/stowlm-v2/adapter'
THREADS = ('OMP_NUM_THREADS', 'OPENBLAS_NUM_THREADS', 'MKL_NUM_THREADS', 'VECLIB_MAXIMUM_THREADS', 'NUMEXPR_NUM_THREADS')
ENV = {**os.environ, 'STOWLM_MLX_CACHE_LIMIT_GB': '2', 'UV_CACHE_DIR': str(ROOT/'.uv-cache'), 'PYTHONPATH': f'{HERE}:{HERE.parent}', **{k: '1' for k in THREADS}}
ENV.pop('STOWAGE_BALLAST_V4', None)
SCRIPTS = (HERE/'k1c_driver.py', HERE/'k1c_report.py', HERE/'judge.py', HERE/'port_mask.py', HERE/'pair_blockrule.py', HERE/'k1b_report.py', HERE.parent/'compact.py',
           HERE.parent/'blockrule/rule.py', ROOT/'experiments/eval-c1/run_c1.py')
FLAGS = [*K1B['flags'], '--port-rule']


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def seal(files):
    code = ('import json,hashlib;from llm.exit.runner import source_pins;from llm.exit.replay import checker_epoch;'
            'print(json.dumps(dict(source_pins_sha256=hashlib.sha256(json.dumps(source_pins(),sort_keys=True).encode()).hexdigest(),checker_epoch=checker_epoch())))')
    identity = json.loads(subprocess.run([PYTHON, '-c', code], cwd=ROOT, env=ENV, capture_output=True, text=True, check=True).stdout.splitlines()[-1])
    if (identity['source_pins_sha256'], identity['checker_epoch']) != (K1B['source_pins_sha256'], K1B['checker_epoch']):
        raise SystemExit('source differs from K1b')
    manifest = dict(decision='D190', flags=FLAGS, seed=0, finisher_s=600., inputs={f: sha(ROOT/f) for f in sorted(files)}, adapter=K1B['adapter'],
                    scripts={str(p.relative_to(ROOT)): sha(p) for p in SCRIPTS}, **identity)
    path = OUT/'manifest.json'
    if path.exists():
        if json.loads(path.read_text()) != manifest:
            raise SystemExit('manifest differs; refusing to resume')
    else:
        path.write_text(json.dumps(manifest, indent=1, sort_keys=True)+'\n')
    return manifest


def decode(files, shards):
    groups = [sorted(files)[i::shards] for i in range(shards)]

    def one(i):
        with open(OUT/f'decode/s{i}.log', 'a') as stream:
            return subprocess.run([PYTHON, 'experiments/eval-c1/run_c1.py', '--run-dir', str(OUT/f'decode/s{i}'), '--adapter', ADAPTER, '--loose', *groups[i], *FLAGS,
                                   '--store-candidates', str(OUT/'candidates'), '--decode-only'], cwd=ROOT, env=ENV, stdout=stream, stderr=subprocess.STDOUT).returncode
    (OUT/'decode').mkdir(exist_ok=True)
    with ThreadPoolExecutor(shards) as pool:
        codes = list(pool.map(one, range(shards)))
    if any(codes):
        raise SystemExit(f'decode failed: {codes}')


def audits(rows):
    from stowage.io import load_instance
    from stowage.model import PlannerResult
    found = {}
    for row in rows:
        if row['moves'] is not None:
            instance = load_instance(ROOT/row['input'])
            plan = PlannerResult.model_validate(json.loads((OUT/'judged/plans'/f"{row['instance_id']}.json").read_text())).plan
            found[row['instance_id']] = port_mask.audit(instance, plan)
    return found


def main():
    workers, shards = int(sys.argv[1]) if len(sys.argv) > 1 else 4, int(sys.argv[2]) if len(sys.argv) > 2 else 2
    files = sorted(K1B['inputs'])
    OUT.mkdir(exist_ok=True)
    manifest = seal(files)
    if not (OUT/'decode.DONE').exists():
        decode(files, shards)
        (OUT/'decode.DONE').write_text(datetime.now().astimezone().isoformat()+'\n')
    os.environ.update({k: '1' for k in THREADS})
    os.environ.pop('STOWAGE_BALLAST_V4', None)
    with ProcessPoolExecutor(workers) as pool:
        rows = list(pool.map(judge.judge_model, judge.jobs(files)))
    k1c_report.write_report(HERE/'K1C.md', manifest, audits(rows))
    ledger = ROOT/'experiments/fleet/ledger.md'
    ledger.write_text(''.join(line for line in ledger.read_text().splitlines(keepends=True) if not line.startswith('| k1c/')))
    with open(ROOT/'experiments/fleet/history.md', 'a') as history:
        history.write(f'\n- k1c/run (fix-t8-sft, 31 Oct): D190 K1c; finished {datetime.now().astimezone().isoformat(timespec="minutes")}.\n')
    (OUT/'K1C.DONE').write_text(datetime.now().astimezone().isoformat()+'\n')


if __name__ == '__main__':
    main()
