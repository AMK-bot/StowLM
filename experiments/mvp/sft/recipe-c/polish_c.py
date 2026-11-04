"""D167 120 s polish with polish91's admission: improve_checked keeps every box-to-slot assignment, the polished plan is
checked again here, and the incumbent stays when nothing improves. Resumable, CPU only.
jobs.json: {instance_id: {input, result}}. Usage: polish_c.py <jobs.json> <out> [seconds=120] [workers=4]
"""
from __future__ import annotations

from concurrent.futures import ProcessPoolExecutor
import hashlib
import json
import os
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parents[4]
for name in ('OMP_NUM_THREADS', 'OPENBLAS_NUM_THREADS', 'MKL_NUM_THREADS', 'VECLIB_MAXIMUM_THREADS', 'NUMEXPR_NUM_THREADS'):
    os.environ[name] = '1'


def admitted(before, after):
    """The polished cost, or the incumbent's when the candidate failed or is no better."""
    return before if after is None or after >= before else after


def one(job):
    iid, spec, out, seconds = job
    target = Path(out)/f'{iid}.json'
    source = hashlib.sha256(Path(spec['result']).read_bytes()).hexdigest()
    if target.exists():
        row = json.loads(target.read_text())
        if row.get('requested_polish_s') == seconds and row.get('source_result_sha256') == source:
            return row
    from stowage.checker import check
    from stowage.io import load_instance
    from stowage.model import PlannerResult
    from stowage.solver.gold_warmstart import improve_checked
    instance = load_instance(ROOT/spec['input'])
    own = PlannerResult.model_validate(json.loads(Path(spec['result']).read_text()))
    own_report = check(instance, own.plan)
    if not own_report.passed:
        raise RuntimeError(f'incumbent no longer passes: {iid}')
    before = own_report.count.weighted_nonproductive
    started = time.perf_counter()
    polished = improve_checked(instance, own, time_limit_s=seconds)
    report = check(instance, polished.plan) if polished.plan is not None else None
    after = report.count.weighted_nonproductive if report is not None and report.passed else None
    final = admitted(before, after)
    row = dict(instance_id=iid, before=before, after=final, improved=final < before, requested_polish_s=seconds,
               source_result_sha256=source, polish_s=round(time.perf_counter()-started, 2), message=polished.message)
    if final < before:
        (Path(out)/f'{iid}.result.json').write_text(polished.model_dump_json())
    target.write_text(json.dumps(row, indent=1))
    print(json.dumps({k: row[k] for k in ('instance_id', 'before', 'after', 'polish_s')}), flush=True)
    return row


def main():
    jobs, out = json.loads(Path(sys.argv[1]).read_text()), Path(sys.argv[2])
    seconds = float(sys.argv[3]) if len(sys.argv) > 3 else 120.
    workers = int(sys.argv[4]) if len(sys.argv) > 4 else 4
    out.mkdir(parents=True, exist_ok=True)
    with ProcessPoolExecutor(workers) as pool:
        rows = list(pool.map(one, [(iid, spec, str(out), seconds) for iid, spec in sorted(jobs.items())]))
    if len(rows) != len(jobs):
        raise SystemExit('polish incomplete')


if __name__ == '__main__':
    main()
