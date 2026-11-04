"""D167 pipeline, detached and resumable: validation (C, C-ens, two-step+), the C vs C-ens rule, then test once.

Runs on the source of 9.1/9.2 (D171: the post-freeze bundle was rolled back); the source pins are recorded and must not
change during the run. Each planner run is one or more run_c1.py shards at the D167 flags; a stage is skipped
when its DONE marker exists. Everything is pre-registered in D167, so nothing here reads a result to change a recipe; the
only branch on results is D167's own rule and, for C-ens, reusing 8.2's first 8 candidates where the 16-sample run proves
the outcome (chosen sample below 8, or no candidate passed).
Usage: driver.py [--stop-after validation]
"""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys

import report_c as report

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[3]
PYTHON = str(ROOT/'.venv/bin/python')
A82, A83 = 'experiments/mvp/sft/stowlm-v2/adapter', 'experiments/mvp/sft/stowlm-v3/adapter'
CODE_READY = ROOT/'experiments/mvp/exit/deployment-d137/D137-CODE.READY'
VAL = ('experiments/mvp/valsplit/audit_small30.txt', 'experiments/mvp/valsplit/reserve_small25.txt')
TEST_LIST = ROOT/'experiments/mvp/runs/test-91/inputs.txt'
TEST_OUT = ROOT/'experiments/mvp/runs/test-91b'
GPU_SHARDS = int(os.environ.get('RECIPE_C_GPU_SHARDS', 3))
CPU_WORKERS = int(os.environ.get('RECIPE_C_CPU_WORKERS', 4))
SCRIPTS = (HERE/'driver.py', HERE/'report_c.py', HERE/'twostep_plus.py', HERE/'polish_c.py', ROOT/'experiments/eval-c1/run_c1.py')
THREADS = ('OMP_NUM_THREADS', 'OPENBLAS_NUM_THREADS', 'MKL_NUM_THREADS', 'VECLIB_MAXIMUM_THREADS', 'NUMEXPR_NUM_THREADS')
ENV = {**os.environ, 'STOWLM_MLX_CACHE_LIMIT_GB': '2', 'UV_CACHE_DIR': str(ROOT/'.uv-cache'), **{k: '1' for k in THREADS}}
ENV.pop('STOWAGE_BALLAST_V4', None)
FLAGS = ['--temperature', '0.7', '--greedy-first', '--mask-workers', '0', '--time-limit', '7200', '--d79', '--securing', '--lookahead',
         '--ballast-v4-finalize', '--backtrack', '32', '--candidate-finalize-s', '600', '--no-last-resort', '--no-repair']


def log(message):
    print(f'{datetime.now():%H:%M:%S} {message}', flush=True)


def stage(marker, work):
    if not marker.exists():
        work()
        marker.write_text(datetime.now().astimezone().isoformat()+'\n')


def together(commands, logs):
    def one(pair):
        command, path = pair
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        with open(path, 'a') as stream:
            return subprocess.run([PYTHON, *map(str, command)], cwd=ROOT, env=ENV, stdout=stream, stderr=subprocess.STDOUT).returncode
    with ThreadPoolExecutor(len(commands)) as pool:
        codes = list(pool.map(one, zip(commands, logs)))
    if any(codes):
        raise SystemExit(f'failed: {codes}; see {logs[0]}')


def shards(items, n):
    return [items[i::n] for i in range(n) if items[i::n]]


def run_planner(adapter, files, samples, out):
    """One arm into <out>/s<i>; run_c1 refuses a changed configuration, so a resume can't mix recipes."""
    if not files:
        return
    groups = shards(sorted(files), GPU_SHARDS)
    commands = [['experiments/eval-c1/run_c1.py', '--run-dir', out/f's{i}', '--adapter', adapter, '--n-samples', samples,
                 '--loose', *group, *FLAGS] for i, group in enumerate(groups)]
    together(commands, [out/f's{i}.log' for i in range(len(groups))])


def run_twostep(files, out):
    groups = shards(sorted(files), CPU_WORKERS)
    out.mkdir(parents=True, exist_ok=True)
    report.check_twostep_cache(out, {f: hashlib.sha256((ROOT/f).read_bytes()).hexdigest() for f in files}, 600.)
    for i, group in enumerate(groups):
        (out/f'shard-{i}.txt').write_text('\n'.join(group)+'\n')
    together([[HERE/'twostep_plus.py', out/f'shard-{i}.txt', out, 600] for i in range(len(groups))],
             [out/f'shard-{i}.log' for i in range(len(groups))])


def polish(jobs, out):
    out.mkdir(parents=True, exist_ok=True)
    report.write(out/'jobs.json', jobs)
    together([[HERE/'polish_c.py', out/'jobs.json', out, 120, CPU_WORKERS]], [out/'polish.log'])


def recipe_c(out, files):
    stage(out/'c.DONE', lambda: run_planner(A82, files, 16, out/'c'))


def recipe_ens(out, files):
    stage(out/'ens83.DONE', lambda: run_planner(A83, files, 8, out/'ens-83'))
    stage(out/'ens82.DONE', lambda: run_planner(A82, report.not_derivable(report.arm(out/'c'), files), 8, out/'ens-82'))


def seal(out, files):
    """One experiment manifest, written before any stage and compared on every resume, so a DONE marker can't hide a changed
    adapter, input, script or flag. Test also seals its choice and the validation manifest it follows from."""
    adapters = {name: {f.name: hashlib.sha256(f.read_bytes()).hexdigest() for f in sorted((ROOT/name).iterdir()) if f.is_file()}
                for name in (A82, A83)}
    manifest = dict(flags=FLAGS, samples=dict(c=16, ens=8), finisher_s=600, polish_s=120, seed=0, gpu_shards=GPU_SHARDS, cpu_workers=CPU_WORKERS,
                    adapters=adapters, inputs={f: hashlib.sha256((ROOT/f).read_bytes()).hexdigest() for f in sorted(files)},
                    scripts={str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest() for p in SCRIPTS})
    path = out/'experiment-manifest.json'
    if path.exists():
        if json.loads(path.read_text()) != manifest:
            raise SystemExit(f'{path} differs from this run; refusing to resume')
    else:
        report.write(path, manifest)
    return manifest


def seal_test(out, validation_dir):
    """The arm and the source are fixed once by validation; a later change of choice.json can't start the other arm."""
    sealed = dict(choice=json.loads((validation_dir/'choice.json').read_text())['chosen'],
                  validation_manifest_sha256=hashlib.sha256((validation_dir/'experiment-manifest.json').read_bytes()).hexdigest())
    path = out/'test-seal.json'
    if path.exists():
        if json.loads(path.read_text()) != sealed:
            raise SystemExit('test seal differs from validation; refusing to resume')
    else:
        report.write(path, sealed)
    return sealed['choice']


def same_source(out, validation_dir):
    left, right = (json.loads((d/'install-binding.json').read_text()) for d in (out, validation_dir))
    if any(left[k] != right[k] for k in ('source_pins_sha256', 'checker_epoch')):
        raise SystemExit('test source differs from validation')


def bind(out):
    binding = dict(checked=datetime.now().astimezone().isoformat(),
                   recipe=dict(flags=FLAGS, gpu_shards=GPU_SHARDS, polish_s=120, decision='D167'))
    code = ('import json;from llm.exit.runner import source_pins;from llm.exit.replay import checker_epoch;import hashlib;'
            f'p=source_pins();r=json.load(open({str(CODE_READY)!r}))["source_pins"];'
            'print(json.dumps(dict(source_pins_sha256=hashlib.sha256(json.dumps(p,sort_keys=True).encode()).hexdigest(),'
            'source_pins_equal_code_ready=p==r,checker_epoch=checker_epoch())))')
    binding.update(json.loads(subprocess.run([PYTHON, '-c', code], cwd=ROOT, env=ENV, capture_output=True, text=True, check=True).stdout.splitlines()[-1]))
    previous = out/'install-binding.json'
    if previous.exists():
        old = json.loads(previous.read_text())
        if any(old[k] != binding[k] for k in ('source_pins_sha256', 'checker_epoch')):
            raise SystemExit('source or install changed since this run began')
    else:
        report.write(previous, binding)


def validation():
    files = [line for name in VAL for line in (ROOT/name).read_text().split()]
    out = HERE/'val'
    out.mkdir(exist_ok=True)
    bind(out)
    seal(out, files)
    recipe_c(out, files)
    log('C done')
    recipe_ens(out, files)
    log('C-ens decoded')
    stage(out/'twostep.DONE', lambda: run_twostep(files, out/'twostep-plus'))
    log('two-step+ done')
    stage(out/'polish.DONE', lambda: (polish(report.jobs_for_arm(report.arm(out/'c')), out/'polish-c'),
                                      polish(report.jobs_for_arm(report.ensemble(out)), out/'polish-ens'),
                                      polish(report.jobs_for_twostep(out/'twostep-plus'), out/'polish-twostep')))
    report.validation_report(out, HERE/'VALIDATION.md', json.loads((out/'install-binding.json').read_text()))
    log('validation report written')


def test():
    files = (TEST_LIST).read_text().split()
    out = TEST_OUT
    out.mkdir(exist_ok=True)
    bind(out)
    same_source(out, HERE/'val')
    seal(out, files)
    choice = seal_test(out, HERE/'val')
    if choice == 'C':
        recipe_c(out, files)
    else:  # 8+8 from scratch: test has no 16-sample run to reuse
        stage(out/'ens83.DONE', lambda: run_planner(A83, files, 8, out/'ens-83'))
        stage(out/'ens82.DONE', lambda: run_planner(A82, files, 8, out/'ens-82'))
    log('test decode done')
    stage(out/'twostep.DONE', lambda: run_twostep(files, out/'twostep-plus'))
    arm = report.arm(out/'c') if choice == 'C' else report.ensemble(out, derive=False)
    stage(out/'polish.DONE', lambda: (polish(report.jobs_for_arm(arm), out/'polish'),
                                      polish(report.jobs_for_twostep(out/'twostep-plus'), out/'polish-twostep')))
    report.test_summary(out, choice, json.loads((out/'install-binding.json').read_text()))
    log('test summary written')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--stop-after', choices=('validation',))
    args = parser.parse_args()
    validation()
    if args.stop_after != 'validation':
        test()
        ledger = ROOT/'experiments/fleet/ledger.md'
        ledger.write_text(''.join(line for line in ledger.read_text().splitlines(keepends=True) if not line.startswith('| recipe-c/')))
        with open(ROOT/'experiments/fleet/history.md', 'a') as history:
            history.write(f'\n- recipe-c (fix-t8-sft, 30 Oct): D167 validation + 9.1b; finished {datetime.now().astimezone().isoformat(timespec="minutes")}.\n')
        (HERE/'COMPLETE').write_text(datetime.now().astimezone().isoformat()+'\n')


if __name__ == '__main__':
    main()
