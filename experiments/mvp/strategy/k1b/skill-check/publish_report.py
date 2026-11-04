"""Publish D205 evidence as it arrives; optionally follow the serial audit.

The sample-zero table is published before the matched-result section. No
verdicts are invented for missing or admission-blocked work. The matched queue
is started separately, as authorized by the updated sample-zero order.
"""
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import subprocess
import sys
import time

HERE = Path(__file__).resolve().parent
REPORT = HERE.parent / 'SKILL-CHECK.md'
ROOT = HERE.parents[4]
os.environ.update(PYTHONDONTWRITEBYTECODE='1', UV_CACHE_DIR=str(ROOT / '.uv-cache'),
                  TMPDIR=str(HERE / 'tmp'), STOWAGE_VESSEL_CACHE_DIR=str(HERE / 'cache/vessels'))
for key in ('OMP_NUM_THREADS', 'OPENBLAS_NUM_THREADS', 'MKL_NUM_THREADS',
            'VECLIB_MAXIMUM_THREADS', 'NUMEXPR_NUM_THREADS', 'BLIS_NUM_THREADS'):
    os.environ[key] = '1'
sys.dont_write_bytecode = True


def run(name, *args):
    return subprocess.run([sys.executable, '-B', str(HERE / name), *args], cwd=ROOT,
                          env=os.environ.copy(), capture_output=True, text=True)


def publish():
    stamp = datetime.now(timezone.utc).isoformat()
    part1 = HERE / 'part1.md'
    zero = HERE / 'part1-sample0.md'
    conclusion = '**The matched first-try advantage is not settled.** No matched conclusion is available until all 165 new judgments pass the admission and integrity checks.'
    summary = HERE / 'matched-summary.json'
    if summary.exists():
        import matched_report
        conclusion = matched_report.conclusion(json.loads(summary.read_text()))
    lines = ['# D205: first-try skill versus judge load', '', conclusion, '',
             f'Updated {stamp}. Scope: 55 existing validation maps, three stored-candidate arms; CPU only, native threads 1. No test or test-v3 input, training, git operation, or prior-row modification.', '']
    if part1.exists():
        lines += [part1.read_text(), '']
    elif zero.exists():
        lines += ['The complete sample-zero gate replay is available below. Other indices run only after the matched queue finishes and a fresh leader state indicates a spare core.', '', zero.read_text(), '']
    else:
        count = sum(1 for _ in (HERE / 'rows').rglob('sample-*.json'))
        pid = (HERE / 'part1.pid').read_text().strip() if (HERE / 'part1.pid').exists() else 'pending'
        lines += [f'Part 1 is running serially (recorded worker PID {pid}); {count}/2,640 candidate audit rows are present. These are progress counts, not final arm results. The scan processes all three arms at sample 0 first, then indices 1–15.', '']
    part2 = HERE / 'part2.md'
    if part2.exists():
        lines += [part2.read_text(), '']
    lines += ['Assumptions and execution notes:', '',
              '- The sight/lashing replay uses the same default tanks (`{}`) for every arm. D193 describes this as a warning gate; it is not a finisher verdict.',
              '- Sample 0 is greedy for 8.2 and null, random for K1r. Missing candidates fail the all-map gate denominator and have no finisher trial.',
              '- Exact tests are paired, two-sided binomial sign tests with ties dropped and no multiplicity adjustment.',
              '- Local, validated memoization reuses only identical loading-condition calculations in Part 1. The matched finisher has no such patch. See `skill-check/memo-validation.json` and source-manifest archives.',
              '- The ledger is `skill-check/LEDGER.md`: the explicit write boundary prevents editing the fleet ledger. Cache and temporary writes are local to skill-check; uv uses the requested repository cache.',
              '- Updated admission uses only the leader’s `experiments/fleet/core-state.json`: mask popcount ≥ 8, load1 ≤ 45, and timestamp age strictly under 120 seconds. Missing, malformed, future, stale, or over-limit states wait without starting trials. Exact evidence is recorded and rechecked at trial start.',
              '- Sample 0 has priority. Supplemental Part 1 indices wait until Part 2 is complete and load1 + 1 ≤ the number of set bits in the fresh core mask, a conservative interpretation of a spare core.', '',
              'Reproduction: `UV_CACHE_DIR=$PWD/.uv-cache uv run --offline --no-sync python -B experiments/mvp/strategy/k1b/skill-check/part1.py`; `matched.py --wait-admission --resume-d206` resumes the independent three-worker sample-zero queue; `publish_report.py --follow` updates this report without launching a duplicate queue. Keep existing fresh rows and batches separate before launching another cohort.', '',
              'Verification: 15 focused standalone tests cover admission freshness, thresholds/errors and paired reporting/completeness. The deterministic audit has an additional source/input/candidate/plan binding recheck in `skill-check/verification.json` when complete.', '']
    temporary = REPORT.with_name(f'SKILL-CHECK.md.{os.getpid()}.tmp')
    temporary.write_text('\n'.join(lines))
    temporary.replace(REPORT)


def main():
    if '--follow' not in sys.argv:
        publish()
        return 0
    (HERE / 'publisher.pid').write_text(str(os.getpid()) + '\n')
    verified_full = False
    verified_zero = False
    while True:
        if (HERE / 'part1-sample0-summary.json').exists() and not verified_zero:
            checked = run('verify-part1.py', '--partial')
            (HERE / 'sample0-verification.log').write_text(checked.stdout + checked.stderr)
            verified_zero = True
            publish()  # Sample-zero evidence is published before Part 2 results.
        if (HERE / 'part1-summary.json').exists() and not verified_full:
            checked = run('verify-part1.py')
            (HERE / 'verification.log').write_text(checked.stdout + checked.stderr)
            verified_full = True
        rendered = run('matched_report.py')
        if rendered.returncode:
            (HERE / 'matched-report-error.log').write_text(rendered.stdout + rendered.stderr)
            return rendered.returncode
        publish()
        status_path = HERE / 'matched/status.json'
        status = json.loads(status_path.read_text()) if status_path.exists() else {}
        if verified_full and status.get('status') in ('complete', 'failed'):
            return 0 if status['status'] == 'complete' else 1
        time.sleep(20)


if __name__ == '__main__':
    raise SystemExit(main())
