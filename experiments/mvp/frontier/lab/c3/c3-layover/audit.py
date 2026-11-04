"""Independently reload recorded passing plans and repeat replay + full checker.

All inputs and output stay in this experiment's folder. This is intentionally
separate from the benchmark runner, with no reuse of its in-memory reports.
"""
from __future__ import annotations
import argparse
import hashlib
import json
import os
from pathlib import Path
import sys
from time import perf_counter

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[5]
sys.dont_write_bytecode = True
for name in ('OMP_NUM_THREADS', 'OPENBLAS_NUM_THREADS', 'VECLIB_MAXIMUM_THREADS',
             'MKL_NUM_THREADS', 'STOWAGE_IN_PORT_WORKERS'):
    os.environ[name] = '1'
os.environ['STOWAGE_VESSEL_CACHE_DIR'] = str(HERE / 'results/vessel-cache')
sys.path.insert(0, str(ROOT / 'src'))


def audit(paths):
    from stowage.model import Instance, MovePlan
    from stowage.checker import check
    from stowage.moves import apply_plan
    rows = []
    for path in paths:
        path = path.resolve()
        path.relative_to(HERE)
        record = json.loads(path.read_text())
        if not record.get('passed'):
            continue
        input_path = (ROOT / record['input']).resolve()
        input_path.relative_to(HERE)
        instance = Instance.model_validate_json(input_path.read_text())
        plan = MovePlan.model_validate(record['plan'])
        began = perf_counter()
        replay = apply_plan(instance, plan)
        report = check(instance, plan)
        actual_hash = hashlib.sha256(input_path.read_bytes()).hexdigest()
        hash_matches = record.get('input_sha256', record.get('sha256')) == actual_hash
        expected_lifts = record['lifts']
        loaded = {p.container_id for p in replay.final_state.ship.placements}
        due_removed = not (set(instance.discharge_list) & loaded)
        all_loaded = set(instance.load_list) <= loaded
        through = {p.container_id for p in instance.onboard.placements} - set(instance.discharge_list)
        all_through = through <= loaded
        counts_equal = abs(float(report.count.weighted_nonproductive)-float(expected_lifts)) < 1e-9
        ok = bool(replay.legal and report.passed and hash_matches and counts_equal
                  and all_loaded and all_through and due_removed)
        row = dict(row=str(path.relative_to(HERE)), passed=ok, replay_legal=replay.legal,
                   full_check_passed=report.passed, input_hash_matches=hash_matches,
                   count_matches=counts_equal, all_loads=all_loaded, all_through=all_through,
                   all_due_removed=due_removed, lifts=float(report.count.weighted_nonproductive),
                   violations=[v.rule_id for v in report.violations], wall_s=perf_counter()-began)
        rows.append(row)
        print(json.dumps(row), flush=True)
    return rows


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('paths', nargs='+', type=Path)
    parser.add_argument('--out', type=Path, default=HERE/'results/audit.json')
    args = parser.parse_args()
    args.out.resolve().relative_to(HERE)
    rows = audit(args.paths)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(dict(checked=len(rows), passed=sum(r['passed'] for r in rows),
                                     rows=rows), indent=2)+'\n')
    if not rows or not all(r['passed'] for r in rows):
        raise SystemExit(1)


if __name__ == '__main__':
    main()
