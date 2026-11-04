"""D220 validation-only 30-map brief experiment, two admitted workers, no repair."""
from __future__ import annotations

import argparse
from concurrent.futures import ProcessPoolExecutor, wait, FIRST_COMPLETED
from datetime import datetime, timezone
from decimal import Decimal
import hashlib
import json
import multiprocessing as mp
import os
from pathlib import Path
import time

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
RUN = HERE/'runs/brief'
STANDING = 'One port per hatch.'
PURPOSE = 'reader-d220-brief'


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(f'.{os.getpid()}.tmp')
    tmp.write_text(json.dumps(value, indent=2)+'\n')
    tmp.replace(path)


def cases():
    value = json.loads((RUN/'cohort.json').read_text())
    return value['cases'] if isinstance(value, dict) else value


def freeze_sources():
    paths = [HERE/n for n in ('brief_planner.py', 'brief_cohort.py', 'pipeline.py', 'frontier_driver.py',
                              'run_brief.py', 'select_binding.py', 'schema.py', 'occupancy.py', 'constraints.py', 'allocation.py')]
    paths += [ROOT/'experiments/mvp/hybrid/planner.py', ROOT/'experiments/mvp/hybrid/admission.py']
    paths += list((ROOT/'experiments/mvp/frontier').glob('*.py'))
    sources = {str(p.relative_to(ROOT)): digest(p) for p in sorted(paths)}
    path = RUN/'implementation.json'
    value = dict(sources=sources, cohort_sha256=digest(RUN/'cohort.json'), standing=STANDING,
                 max_usd='2', purpose=PURPOSE, workers=2, native_workers=1,
                 created_utc=datetime.now(timezone.utc).isoformat(),
                 policy='stored baseline search_s; finalize_s=300; constructor_s=120; seed=0; parsed-constraint necessary geometry preflight; no repair')
    if path.exists():
        prior = json.loads(path.read_text())
        if prior['sources'] != sources or prior['cohort_sha256'] != value['cohort_sha256']:
            raise ValueError('Frozen cohort or implementation changed')
    else:
        write(path, value)


def verify_sources():
    value = json.loads((RUN/'implementation.json').read_text())
    prereg = json.loads((RUN/'preregister.json').read_text())
    if prereg['cohort_sha256'] != value['cohort_sha256']:
        raise ValueError('Cohort preregistration does not match implementation freeze')
    if value['cohort_sha256'] != digest(RUN/'cohort.json'):
        raise ValueError('Frozen cohort changed')
    for name, expected in value['sources'].items():
        if digest(ROOT/name) != expected:
            raise ValueError('Frozen implementation changed: '+name)


def prepare():
    from stowage.io import load_instance
    from experiments.mvp.reader.brief_planner import VoyageBrief, read_brief
    from experiments.mvp.reader.pipeline import SparkParser
    freeze_sources()
    parser = None
    for case in cases():
        path = RUN/'parser'/f"{case['case_id']}.json"
        if path.exists():
            continue
        if parser is None:
            parser = SparkParser(purpose=PURPOSE, allocation_usd=Decimal('2'))
        instance = load_instance(ROOT/case['input'])
        began = time.perf_counter()
        parsed, _ = read_brief(instance, VoyageBrief(STANDING, (case['sentence'],)), parser=parser)
        row = dict(case_id=case['case_id'], brief=parsed, parse_wall_s=time.perf_counter()-began)
        write(path, row)
        print(json.dumps(dict(event='parsed', case_id=case['case_id'],
                              statuses=[s['status'] for s in parsed['sentences']])), flush=True)
    print('All 30 briefs parsed and checkpointed before planning.', flush=True)


def admission():
    from experiments.mvp.hybrid.admission import reading
    while True:
        current = reading()
        if current['allowed']:
            return current
        print(json.dumps(dict(event='waiting_admission', pid=os.getpid(), evidence=current)), flush=True)
        time.sleep(15)


def hard_answers(brief, instance):
    from experiments.mvp.reader.schema import validate_answer, context_from_instance
    context = context_from_instance(instance)
    return [validate_answer(json.dumps(s['parsed']), context) for s in brief['sentences'] if s['binds']]


def plan_one(case):
    from stowage.io import load_instance
    from experiments.mvp.reader.brief_planner import execute_brief
    verify_sources()
    target = RUN/'plans'/f"{case['case_id']}.json"
    claim = RUN/'claims'/f"{case['case_id']}.json"
    claim.parent.mkdir(parents=True, exist_ok=True)
    if target.exists():
        return case['case_id'], 'cached'
    if claim.exists():
        raise RuntimeError('Unfinished prior attempt; no automatic retry: '+case['case_id'])
    admitted = admission()
    with claim.open('x') as handle:
        json.dump(dict(pid=os.getpid(), admission=admitted), handle)
    started = time.perf_counter()
    row = dict(case_id=case['case_id'], passed=False, repaired=False, admission=admitted,
               planner_pid=os.getpid())
    try:
        if digest(ROOT/case['input']) != case['input_sha256'] or digest(ROOT/case['baseline_file']) != case['baseline_sha256']:
            raise ValueError('Input or stored baseline changed')
        instance = load_instance(ROOT/case['input'])
        baseline = json.loads((ROOT/case['baseline_file']).read_text())
        parser_path = RUN/'parser'/f"{case['case_id']}.json"
        row['parser_sha256'] = digest(parser_path)
        parsed = json.loads(parser_path.read_text())
        if parsed['case_id'] != case['case_id']:
            raise ValueError('Parser checkpoint case mismatch')
        result, evidence = execute_brief(instance, parsed['brief'], hard_answers(parsed['brief'], instance),
                                          search_s=float(baseline['search_s']), finalize_s=300.,
                                          constructor_s=120., workers=1, seed=0)
        row.update(evidence=evidence, passed=result is not None, search_s=float(baseline['search_s']),
                   parse_wall_s=parsed['parse_wall_s'])
        if result is not None:
            row['plan'] = json.loads(result.plan.model_dump_json())
    except Exception as error:
        row.update(passed=False, error_type=type(error).__name__)
    row['wall_s'] = time.perf_counter()-started
    row['pipeline_wall_s'] = row['wall_s']+row.get('parse_wall_s', 0)
    write(target, row)
    return case['case_id'], row['passed'], round(row['wall_s'], 1), row.get('error_type')


def audit_one(case):
    """Fresh process, full checker; intended target only enters this audit."""
    from stowage.checker import check
    from stowage.io import load_instance
    from stowage.model import MovePlan
    from experiments.mvp.reader.schema import validate_answer, canonicalize, context_from_instance
    from experiments.mvp.reader.pipeline import intrinsic_violations, plan_signature
    from experiments.mvp.hybrid.admission import evidence_errors
    verify_sources()
    admitted = admission()
    started = time.perf_counter()
    row = dict(case_id=case['case_id'], audit_pid=os.getpid(), admission=admitted, passed=False,
               audit_completed=False, baseline_verified=False)
    try:
        if digest(ROOT/case['input']) != case['input_sha256'] or digest(ROOT/case['baseline_file']) != case['baseline_sha256']:
            raise ValueError('Frozen input or baseline changed')
        instance = load_instance(ROOT/case['input'])
        context = context_from_instance(instance)
        intended = validate_answer(json.dumps(case['expected']), context)
        baseline = json.loads((ROOT/case['baseline_file']).read_text())
        before = MovePlan.model_validate(baseline['plan'])
        checked = check(instance, before)
        before_errors = intrinsic_violations(instance, intended, before)
        row.update(baseline_passed=bool(checked.passed), baseline_moves=float(checked.count.weighted_nonproductive),
                   baseline_violations=before_errors, baseline_breaks_instruction=bool(before_errors))
        if not checked.passed or row['baseline_moves'] != baseline['weighted']:
            raise ValueError('Stored baseline full checker/cost mismatch')
        row['baseline_verified'] = True
        plan_path = RUN/'plans'/f"{case['case_id']}.json"
        parser_path = RUN/'parser'/f"{case['case_id']}.json"
        row.update(plan_sha256=digest(plan_path), parser_sha256=digest(parser_path),
                   input_sha256=case['input_sha256'], baseline_sha256=case['baseline_sha256'])
        planned = json.loads(plan_path.read_text())
        parser_checkpoint = json.loads(parser_path.read_text())
        parsed = parser_checkpoint['brief']
        if (planned['case_id'] != case['case_id'] or parser_checkpoint['case_id'] != case['case_id']
                or planned['parser_sha256'] != row['parser_sha256']
                or ('evidence' in planned and planned['evidence'].get('sentences') != parsed['sentences'])):
            raise ValueError('Plan and parser evidence linkage mismatch')
        voyage = [s for s in parsed['sentences'] if s['role'] == 'voyage']
        row['parse_exact'] = bool(len(voyage) == 1 and voyage[0]['parsed'] is not None and
                                  canonicalize(voyage[0]['parsed']) == canonicalize(case['expected']))
        row['plan_admission_errors'] = evidence_errors(planned['admission'])
        row.update(plan_present='plan' in planned, planner_claimed_pass=planned['passed'],
                   pipeline_wall_s=planned['pipeline_wall_s'])
        if 'plan' in planned:
            after = MovePlan.model_validate(planned['plan'])
            checked = check(instance, after)
            intended_errors = intrinsic_violations(instance, intended, after)
            parsed_errors = [reason for answer in hard_answers(parsed, instance)
                             for reason in intrinsic_violations(instance, answer, after)]
            row.update(full_checker_passed=bool(checked.passed), instruction_violations=intended_errors,
                       parsed_violations=parsed_errors, instruction_obeyed=not intended_errors,
                       moves=float(checked.count.weighted_nonproductive))
            row['passed'] = bool(checked.passed and not parsed_errors and not row['plan_admission_errors'])
            row['compliant_pass'] = bool(row['passed'] and not intended_errors)
            row['plan_changed'] = plan_signature(after) != plan_signature(before)
            row['reader_binding_credit'] = bool(row['compliant_pass'] and row['parse_exact']
                                                and row['baseline_breaks_instruction'] and row['plan_changed'])
            row['move_delta'] = row['moves']-row['baseline_moves']
        if digest(plan_path) != row['plan_sha256'] or digest(parser_path) != row['parser_sha256']:
            raise ValueError('Artifacts changed during fresh audit')
        row['audit_completed'] = True
    except Exception as error:
        row.update(error_type=type(error).__name__)
    row['audit_wall_s'] = time.perf_counter()-started
    write(RUN/'audits'/f"{case['case_id']}.json", row)
    return case['case_id'], row['passed'], row.get('instruction_obeyed'), row.get('move_delta')


def run():
    verify_sources()
    cohort = cases()
    if len(cohort) != 30 or any(not (RUN/'parser'/f"{c['case_id']}.json").exists() for c in cohort):
        raise ValueError('Exactly 30 frozen and parsed validation briefs are required')
    write(RUN/'controller.json', dict(pid=os.getpid(), workers=2, native_workers=1,
                                      started_utc=datetime.now(timezone.utc).isoformat()))
    for function, folder in ((plan_one, 'plans'), (audit_one, 'audits')):
        pending = iter([c for c in cohort if not (RUN/folder/f"{c['case_id']}.json").exists()])
        with ProcessPoolExecutor(2, mp_context=mp.get_context('spawn'), max_tasks_per_child=1) as pool:
            active = {}
            for _ in range(2):
                case = next(pending, None)
                if case is not None:
                    active[pool.submit(function, case)] = case
            while active:
                done, _ = wait(active, timeout=15, return_when=FIRST_COMPLETED)
                for future in done:
                    case = active.pop(future)
                    print(json.dumps(dict(event=folder, result=future.result())), flush=True)
                    case = next(pending, None)
                    if case is not None:
                        active[pool.submit(function, case)] = case
    print('30-map plans and fresh audits complete.', flush=True)


if __name__ == '__main__':
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument('mode', choices=('prepare', 'run'))
    args = cli.parse_args()
    prepare() if args.mode == 'prepare' else run()
