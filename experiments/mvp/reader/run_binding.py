"""Preregister, parse, run <=20 constrained validation replans, then fresh audits.

Run prepare once, then run. Checkpoints resume without repeating planner calls.
At most two map workers, one native solver worker each, load threshold >=36.
"""
from __future__ import annotations

import argparse
from concurrent.futures import ProcessPoolExecutor, as_completed
from datetime import datetime, timezone
import hashlib
import json
import multiprocessing as mp
import os
from pathlib import Path
import time

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
RUN = HERE / 'runs/binding'


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(f'.{os.getpid()}.tmp')
    tmp.write_text(json.dumps(value, indent=2) + '\n')
    tmp.replace(path)


def prepare():
    from experiments.mvp.reader.pipeline import SparkParser
    from experiments.mvp.reader.schema import answer_envelope, canonicalize
    cases_path = HERE / 'binding-cases.json'
    selected = json.loads(cases_path.read_text())
    cases = selected['cases']
    if len(cases) != 20 or not selected['validation_only']:
        raise ValueError('Exactly 20 validation cases are required')
    for kind in ('close_bay', 'max_tiers', 'dg_keep_away', 'one_port_per_hatch'):
        if sum(c['type'] == kind for c in cases) != 5:
            raise ValueError('Exactly five maps per constraint type required')
    RUN.mkdir(parents=True, exist_ok=True)
    freeze = RUN / 'preregister.json'
    binding = {'cases_sha256': digest(cases_path), 'created_utc': datetime.now(timezone.utc).isoformat(),
               'max_constrained_planner_calls': 20, 'workers': 2, 'load_limit': 64,
               'search_policy': 'same stored baseline search_s; finalize_s=300; constructor_s=120; no repair',
               'sources': {str(p.relative_to(ROOT)): digest(p) for p in
                           [HERE/'pipeline.py', HERE/'frontier_driver.py', HERE/'allocation.py', HERE/'constraints.py',
                            HERE/'run_binding.py', HERE/'binding_audit.py',
                            ROOT/'experiments/mvp/hybrid/planner.py',
                            *[ROOT/'experiments/mvp/frontier'/n for n in
                              ('planner.py','alns.py','master.py','sequence.py','sight.py','blocked.py')]]}}
    if freeze.exists():
        prior = json.loads(freeze.read_text())
        if prior['cases_sha256'] != binding['cases_sha256'] or prior['sources'] != binding['sources']:
            raise ValueError('Prerecorded cohort or implementation changed')
    else:
        write(freeze, binding)
    parser = None
    for case in cases:
        if 'val' not in Path(case['input']).parts or any(x in Path(case['input']).parts for x in ('train', 'test')):
            raise ValueError('Only validation inputs may be planned')
        if digest(ROOT/case['input']) != case['input_sha256']:
            raise ValueError('Input changed since selection')
        path = RUN/'parser'/f"{case['case_id']}.json"
        if path.exists():
            recorded = json.loads(path.read_text())
            if recorded['sentence'] != case['sentence'] or recorded['context'] != case['context']:
                raise ValueError('Cached parse inputs changed')
            continue
        if parser is None:
            parser = SparkParser()
        start = time.perf_counter()
        record = dict(case, baseline_sha256=digest(ROOT/case['baseline_file']))
        try:
            answer, evidence = parser.parse(case['sentence'], case['context'])
            record.update(parsed=answer_envelope(answer), parser=evidence,
                          parse_exact=canonicalize(answer) == canonicalize(case['expected']))
        except Exception as error:
            record.update(parsed=None, parser_error=type(error).__name__)
        record['parse_wall_s'] = time.perf_counter()-start
        write(path, record)
        print(f"parsed {case['case_id']} exact={record.get('parse_exact')} error={record.get('parser_error')}", flush=True)
        if record.get('parser_error'):
            # Avoid compounding unknown billing. Resume preserves the failed case.
            raise RuntimeError('Parser failed; checkpoint retained, no automatic retry')
    print('All 20 parsed cases fixed before any constrained planner call.', flush=True)


def plan_one(case):
    target = RUN/'plans'/f"{case['case_id']}.json"
    if target.exists():
        return case['case_id'], 'cached'
    claim = RUN/'claims'/f"{case['case_id']}.json"
    claim.parent.mkdir(parents=True, exist_ok=True)
    try:
        with claim.open('x') as handle:
            json.dump({'pid': os.getpid(), 'case_id': case['case_id'], 'time': datetime.now(timezone.utc).isoformat()}, handle)
    except FileExistsError:
        write(target, dict(case, passed=False, error='Earlier attempt has no completed checkpoint; no automatic replan retry'))
        return case['case_id'], 'earlier attempt retained; not retried'
    from stowage.checker import check
    from stowage.io import load_instance
    from stowage.model import MovePlan
    from experiments.mvp.reader.schema import Refusal, context_from_instance, validate_answer
    from experiments.mvp.reader.pipeline import compile_constraint, intrinsic_violations, plan_signature
    from experiments.mvp.reader.frontier_driver import plan_and_check
    from experiments.mvp.reader.binding_claims import sentence_target_agreement
    start = time.perf_counter()
    row = dict(case, planner_pid=os.getpid(), repaired=False, passed=False, binding=False)
    try:
        row.update(sentence_target_agreement(case))
        if not row['parse_agreement']:
            raise ValueError(row['sentence_target_failure'])
        if digest(ROOT/case['input']) != case['input_sha256'] or digest(ROOT/case['baseline_file']) != case['baseline_sha256']:
            raise ValueError('Frozen input/baseline changed')
        instance = load_instance(ROOT/case['input'])
        if context_from_instance(instance) != case['context']:
            raise ValueError('Parser context differs from actual instance')
        answer = validate_answer(json.dumps(case['parsed']), case['context'])
        if isinstance(answer, Refusal):
            raise ValueError('Spark refused the sentence; no constraint applied')
        baseline = json.loads((ROOT/case['baseline_file']).read_text())
        before = MovePlan.model_validate(baseline['plan'])
        before_check = check(instance, before)
        violations = intrinsic_violations(instance, answer, before)
        row.update(unconstrained_passed=bool(before_check.passed),
                   unconstrained_moves=float(before_check.count.weighted_nonproductive),
                   unconstrained_violations=violations, baseline_plan_signature=plan_signature(before))
        if not before_check.passed or row['unconstrained_moves'] != baseline['weighted']:
            raise ValueError('Stored unconstrained frontier plan failed fresh reproduction')
        compiled, allocation = compile_constraint(instance, answer, workers=1, seed=0)
        row['compilation'] = allocation
        search_s = float(baseline.get('search_s', min(120., max(20., 6.*len(instance.load_list)))))
        result, details = plan_and_check(instance, compiled, search_s=search_s,
                                         finalize_s=300., seed=0, workers=1, constructor_s=120.)
        row.update(record=details, search_s=search_s, finalize_s=300., passed=result is not None)
        if result is not None:
            checked = check(instance, result.plan)
            semantic_errors = intrinsic_violations(instance, answer, result.plan)
            allocation_errors = compiled.violations(result.plan)
            row.update(plan=json.loads(result.plan.model_dump_json()),
                       constrained_moves=float(checked.count.weighted_nonproductive),
                       constrained_passed=bool(checked.passed), instruction_violations=semantic_errors,
                       allocation_violations=allocation_errors, plan_changed=plan_signature(result.plan) != plan_signature(before))
            row['passed'] = bool(checked.passed and not semantic_errors and not allocation_errors)
            row['sentence_compliance_verified'] = row['passed']
    except Exception as error:
        row.update(passed=False, error=f'{type(error).__name__}: {error}')
    row['wall_s'] = time.perf_counter()-start
    write(target, row)
    return case['case_id'], row['passed'], row.get('constrained_moves'), round(row['wall_s'], 1), row.get('error')


def audit_one(case):
    from experiments.mvp.reader.binding_audit import audit
    path = RUN/'plans'/f"{case['case_id']}.json"
    target = RUN/'audits'/f"{case['case_id']}.json"
    if target.exists():
        return case['case_id'], 'cached audit'
    result = audit(path)
    write(target, result)
    return case['case_id'], result.get('passed'), result.get('binding')


def run(load_limit):
    if load_limit < 36:
        raise ValueError('load limit must be at least 36')
    write(RUN/'controller.json', {'pid': os.getpid(), 'load_limit': load_limit, 'workers': 2,
                                 'started_utc': datetime.now(timezone.utc).isoformat()})
    prereg = json.loads((RUN/'preregister.json').read_text())
    for path, expected in prereg['sources'].items():
        if digest(ROOT/path) != expected:
            raise ValueError('Source changed after preregistration: '+path)
    selected = json.loads((HERE/'binding-cases.json').read_text())
    if digest(HERE/'binding-cases.json') != prereg['cases_sha256']:
        raise ValueError('Cohort changed after preregistration')
    cases = [json.loads((RUN/'parser'/f"{c['case_id']}.json").read_text()) for c in selected['cases']]
    # No new plan if a checkpoint exists, including failed attempts.
    for function, folder in ((plan_one, 'plans'), (audit_one, 'audits')):
        pending = [c for c in cases if not (RUN/folder/f"{c['case_id']}.json").exists()]
        with ProcessPoolExecutor(2, mp_context=mp.get_context('spawn'), max_tasks_per_child=1) as pool:
            futures = []
            for case in pending:
                while os.getloadavg()[0] > load_limit:
                    print(f'load {os.getloadavg()[0]:.1f} above {load_limit}; waiting 10s', flush=True)
                    time.sleep(10)
                futures.append(pool.submit(function, case))
            for future in as_completed(futures):
                print(future.result(), flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('mode', choices=['prepare', 'run'])
    parser.add_argument('--load-limit', type=float, default=64.)
    args = parser.parse_args()
    if args.mode == 'prepare':
        prepare()
    else:
        run(args.load_limit)
