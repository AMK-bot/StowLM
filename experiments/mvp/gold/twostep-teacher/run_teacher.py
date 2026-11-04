"""D150/D151: re-verify stored gold and run two-step v4.4 on the maps of d151-manifest.json, through the loop's own path.

prepare_teacher_candidates + verify_candidate: same two-step proposal (120 s), one V4 finisher attempt (120 s), full
checker, layered mask replay, under the audit outcome epoch loaded at worker start. 'gold_only' maps skip two-step.
The check runs in this process, not a fresh child per plan, because each child repeats imports and the 6-10 s epoch
snapshot. Same checker, deadline and availability rules; d151.change_proof and a blocking 30-winner audit through the
unmodified path stand in for the per-check guard. A worker asserts the epoch every EPOCH_EVERY maps and at its end, and
writes its receipt under receipts/<launch_id>/ only after that. A stored row is reused only if d151.row_problem passes it,
so crashed, incomplete or other-epoch rows run again. train only; no model is loaded.
Usage: run_teacher.py run <shard> <launch.json>
"""
from llm.exit import outcome_epoch  # snapshot before evaluation modules load

import argparse
import hashlib
import json
from pathlib import Path
import time
import traceback

EPOCH = outcome_epoch.process_snapshot()

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[3]
GOLD, CORPUS = ROOT/'experiments/mvp/gold', ROOT/'experiments/mvp/corpus'
BUDGET_S = 120.
EPOCH_EVERY = 25


def in_process_check(instance, plan, *, timeout_s=300., checker=None, expected_outcome_epoch=None):
    """run_full_check's checker, deadline and availability rules without the per-check child process."""
    from llm.exit.checker_watchdog import CheckExpired, CheckOutcome, _unavailable_reason, check_deadline
    from stowage.checker import check
    started = time.monotonic()
    try:
        with check_deadline(timeout_s):
            report = check(instance, plan)
    except CheckExpired as error:
        return CheckOutcome(False, reason=f'watchdog_{error}', wall_s=time.monotonic()-started)
    reason = _unavailable_reason(report)
    wall = time.monotonic()-started
    return CheckOutcome(False, reason=reason, wall_s=wall) if reason else CheckOutcome(True, report=report, wall_s=wall, checker_s=wall)


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name+'.tmp')
    temporary.write_text(json.dumps(value, sort_keys=True)+'\n')
    temporary.replace(path)


def key(case):
    return case.replace('/', '__')


def compact(record):
    if record is None:
        return None
    planning = record.get('planning') or {}
    return dict(attempted=record.get('attempted'), passed=record.get('passed') is True, moves=record.get('weighted_moves'),
                stage=record.get('stage'), error=record.get('error'), unavailable=bool(record.get('unavailable')),
                unavailable_reason=record.get('unavailable_reason'), finisher_timeout=record.get('finisher_timeout'),
                finisher=record.get('finisher'), timings=record.get('timings'), wall_s=record.get('wall_s'),
                planning_wall_s=record.get('planning_wall_s', planning.get('planning_wall_s')),
                planning_status=record.get('status', planning.get('status')), mask_passed=(record.get('mask') or {}).get('passed'))


def one(row, epoch_id, checker, launch_id):
    from llm.exit.identity import identity_guard
    from llm.exit.selection import load_gold_candidate, prepare_teacher_candidates, verify_candidate
    from stowage.io import load_instance
    path = ROOT/row['input']
    if (CORPUS/'train') not in path.resolve().parents:
        raise ValueError(f"not a train input: {row['input']}")
    raw = path.read_bytes()
    input_sha = hashlib.sha256(raw).hexdigest()
    instance = load_instance(path)
    if row.get('instance_id') and instance.id != row['instance_id']:
        raise ValueError('instance id differs from manifest')
    identity_guard(CORPUS).require_allowed(instance, role='train', input_path=str(path), raw_sha=input_sha)
    started = time.monotonic()
    candidates, cache, records = prepare_teacher_candidates(
        instance, load_gold_candidate(path, CORPUS, GOLD), planning_s=BUDGET_S, ballast_s=BUDGET_S,
        baseline_planner=(lambda *a, **k: None) if row['mode'] == 'gold_only' else None,
        verifier=lambda i, c: verify_candidate(i, c, ballast_s=BUDGET_S, expected_outcome_epoch=EPOCH))
    by_source = {r['source']: r for r in records}
    for candidate in candidates:
        checked = cache[candidate.candidate_id]
        if checked.passed:
            write(HERE/'candidates'/key(row['case'])/f'{candidate.source}.json', dict(
                instance_path=row['input'], input_sha256=input_sha, source=candidate.source,
                weighted_moves=checked.weighted_moves, plan=checked.plan.model_dump(mode='json'),
                report=checked.details['report']))
    if input_sha != row['input_sha256']:
        raise ValueError(f"input changed: {row['case']}")
    two_step = None if row['mode'] == 'gold_only' else compact(by_source.get('baseline_twostep'))
    return dict(row, outcome_epoch=epoch_id, checker_epoch=checker, budget_s=BUDGET_S, check_path='in_process', launch_id=launch_id,
                gold=compact(by_source.get('gold')), two_step=two_step, wall_s=time.monotonic()-started)


def run(shard, launch_path):
    global HERE
    from d151 import key, read, row_problem
    from llm.exit import checker_watchdog
    from llm.exit.replay import checker_epoch
    original_check = checker_watchdog.run_full_check
    launch = read(launch_path)
    if launch.get('run_dir'):
        HERE = Path(launch['run_dir']).resolve()
    manifest = read(launch.get('manifest', HERE/'d151-manifest.json'))
    if manifest.get('schema') == 'd172-teacher-manifest-v1':
        from d172_prepare import row_problem
    epoch_id, checker = outcome_epoch.epoch_id(EPOCH), checker_epoch()
    if (epoch_id, checker) != (manifest['outcome_epoch'], manifest['checker_epoch']):
        raise SystemExit('loaded epoch differs from the manifest')
    assigned = manifest['entries'][shard::launch['workers']]
    done, failed = 0, []
    for row in assigned:
        target = HERE/'rows'/(key(row['case'])+'.json')
        if target.exists() and row_problem(read(target), row, manifest, HERE) is None:
            continue
        if done and done % EPOCH_EVERY == 0:
            outcome_epoch.assert_current(EPOCH)
        done += 1
        try:
            if row['mode'] == 'winner_only':
                checker_watchdog.run_full_check = original_check
                result = retained_one(row, epoch_id, checker, launch['launch_id'])
            else:
                checker_watchdog.run_full_check = in_process_check
                result = one(row, epoch_id, checker, launch['launch_id'])
        except Exception as error:
            if 'epoch' in str(error):
                raise  # a changed epoch ends the run; no receipt, so nothing from this launch is admitted
            result = dict(row, crashed=traceback.format_exc()[-1500:])
            failed.append(row['case'])
        write(target, result)
        print(json.dumps(dict(case=row['case'], gold=(result.get('gold') or {}).get('moves'),
                              two_step=(result.get('two_step') or {}).get('moves'), wall_s=result.get('wall_s'))), flush=True)
    outcome_epoch.assert_current(EPOCH)
    write(HERE/'receipts'/launch['launch_id']/f'shard-{shard}.json', dict(
        ok=True, uuid=manifest['uuid'], launch_id=launch['launch_id'], shard=shard, outcome_epoch=epoch_id,
        checker_epoch=checker, assigned=[row['case'] for row in assigned], processed=done, crashed=failed))


def retained_one(row, epoch_id, checker, launch_id):
    """Recheck a D151 winner under this epoch, using the unmodified watchdog path."""
    from d151 import read, sha
    from llm.exit.identity import identity_guard
    from llm.exit.selection import Candidate, verify_candidate
    from stowage.io import load_instance
    from stowage.model import MovePlan
    started = time.monotonic()
    path = (ROOT/row['input']).resolve()
    if (CORPUS/'train') not in path.parents or sha(path) != row['input_sha256']:
        raise ValueError('retained input is not unchanged train input')
    instance = load_instance(path)
    if instance.id != row['instance_id']:
        raise ValueError('retained instance id differs from manifest')
    identity_guard(CORPUS).require_allowed(instance, role='train', input_path=str(path), raw_sha=row['input_sha256'])
    record = read(row['retained_record'])
    if sha(row['retained_record']) != row['retained_record_sha256']:
        raise ValueError('retained D151 record changed')
    checked = verify_candidate(instance, Candidate(MovePlan.model_validate(record['plan']), 'retained', 'retained'),
                               ballast_s=BUDGET_S, expected_outcome_epoch=EPOCH)
    detail = compact(dict(checked.details, attempted=True, passed=checked.passed,
                          weighted_moves=checked.weighted_moves))
    if checked.passed:
        write(HERE/'candidates'/key(row['case'])/'retained.json', dict(instance_path=row['input'],
            input_sha256=row['input_sha256'], source='retained', weighted_moves=checked.weighted_moves,
            plan=checked.plan.model_dump(mode='json'), report=checked.details['report']))
    return dict(row, outcome_epoch=epoch_id, checker_epoch=checker, budget_s=BUDGET_S,
                launch_id=launch_id, check_path='watchdog', retained=detail, wall_s=time.monotonic()-started)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=('run',))
    parser.add_argument('shard', type=int)
    parser.add_argument('launch', type=Path)
    args = parser.parse_args()
    run(args.shard, args.launch)


if __name__ == '__main__':
    main()
