"""Reuse D151 winners only under identical epochs and complete original verification evidence.
No solver work: D172 new-map workers still run normally; the fresh 30-winner audit remains blocking.
"""
from pathlib import Path
import math
import os

import d151

HERE = Path(__file__).resolve().parent
RUN = HERE/'d172'
ROOT = HERE.parents[3]


def main():
    from llm.exit import outcome_epoch
    from llm.exit.replay import checker_epoch
    old = d151.read(HERE/'d151-manifest.json')
    manifest = d151.read(RUN/'manifest.json')
    admitted = d151.read(HERE/'admitted.json')
    epoch = outcome_epoch.process_snapshot()
    if not (outcome_epoch.epoch_id(epoch) == manifest['outcome_epoch'] == old['outcome_epoch']
            and checker_epoch() == manifest['checker_epoch'] == old['checker_epoch']
            and manifest['budget_s'] == old['budget_s'] == 120.):
        raise RuntimeError('D151 reuse requires identical current outcome/checker epoch and recipe')
    if admitted['uuid'] != old['uuid'] or admitted['change_proof']['passed'] is not True:
        raise RuntimeError('D151 has no matching passing original change proof')
    old_entries = {e['case']:e for e in old['entries']}
    admitted_cases = {e['case'] for e in admitted['rows']}
    count = 0
    for entry in manifest['entries']:
        if entry['mode'] != 'winner_only':
            continue
        case = entry['case']
        if case not in admitted_cases:
            raise RuntimeError(f'not originally admitted: {case}')
        old_row_path = HERE/'rows'/(d151.key(case)+'.json')
        old_row = d151.read(old_row_path)
        old_entry = old_entries[case]
        problem = d151.row_problem(old_row, old_entry, old, HERE)
        if problem:
            raise RuntimeError(f'original D151 evidence inadmissible: {case}: {problem}')
        record = d151.read(entry['retained_record'])
        given = record['provenance']
        source, costs = d151.teacher(old_row, old_entry)
        expected = dict(uuid=old['uuid'], case=case, outcome_epoch=old['outcome_epoch'],
                        checker_epoch=old['checker_epoch'], budget_s=120., input_sha256=entry['input_sha256'], source=source)
        if any(given.get(k) != v for k,v in expected.items()) or given['costs'] != costs or source != entry['retained_source']:
            raise RuntimeError(f'D151 record provenance mismatch: {case}')
        detail = old_row[source]
        if (detail.get('passed') is not True or detail.get('mask_passed') is not True or detail.get('unavailable')
                or detail.get('finisher_timeout') or not math.isfinite(detail['moves']) or detail['moves'] < 0):
            raise RuntimeError(f'D151 winner missing full verification evidence: {case}')
        candidate = d151.read(d151.candidate_path(HERE, old_entry, 'gold' if source == 'gold' else 'baseline_twostep'))
        if (candidate['plan'] != record['plan'] or candidate['report'] != record['report']
                or candidate['report']['passed'] is not True or candidate['weighted_moves'] != detail['moves']
                or candidate['report']['count']['weighted_nonproductive'] != detail['moves']):
            raise RuntimeError(f'D151 winning candidate and export disagree: {case}')
        if (d151.sha(entry['retained_record']) != entry['retained_record_sha256']
                or d151.sha(entry['input']) != entry['input_sha256']
                or d151.sha(ROOT/'experiments/mvp/gold'/case/'plan.json') != entry['gold_plan_sha256']):
            raise RuntimeError(f'D151 artifacts changed: {case}')
        row = dict(entry, outcome_epoch=manifest['outcome_epoch'], checker_epoch=manifest['checker_epoch'], budget_s=120.,
                   launch_id=old_row.get('launch_id'), original_admission_launch_id=admitted['launch_id'],
                   check_path='reused_same_epoch', retained=detail,
                   reused_d151_row=str(old_row_path), reused_d151_row_sha256=d151.sha(old_row_path),
                   reused_d151_admission_sha256=d151.sha(HERE/'admitted.json'), wall_s=0.)
        target = RUN/'rows'/(d151.key(case)+'.json')
        if target.exists():
            raise RuntimeError(f'refusing to replace an existing D172 row: {case}')
        d151.write(d151.candidate_path(RUN, entry, 'retained'), candidate)
        d151.write(target, row)
        count += 1
    outcome_epoch.assert_current(epoch)
    d151.write(RUN/'same-epoch-reuse.json', dict(count=count, outcome_epoch=manifest['outcome_epoch'],
                  checker_epoch=manifest['checker_epoch'], d151_manifest_sha256=d151.sha(HERE/'d151-manifest.json'),
                  admission_sha256=d151.sha(HERE/'admitted.json'), fresh_reverification=False))
    print(f'Reused {count} D151 verified winners under the identical current epoch', flush=True)


if __name__ == '__main__':
    os.environ.update({k:'1' for k in d151.THREADS})
    os.environ.pop('STOWAGE_BALLAST_V4', None)
    main()
