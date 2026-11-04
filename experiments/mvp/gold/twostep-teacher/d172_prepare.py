"""Isolated D172 manifest and strict resumable row gates. No training or held-out inputs."""
from __future__ import annotations

import json
import math
from pathlib import Path
import time
import uuid

import d151

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[3]


def row_problem(row, entry, manifest, here):
    for field in ('mode', 'input', 'instance_id'):
        if row.get(field) != entry[field]:
            return f'{field} differs'
    if row.get('gold_plan_sha256') != entry['gold_plan_sha256']:
        return 'gold plan hash differs'
    if d151.sha(ROOT/'experiments/mvp/gold'/entry['case']/'plan.json') != entry['gold_plan_sha256']:
        return 'stored gold plan changed'
    if row.get('case') != entry['case']:
        return 'case differs'
    if entry['mode'] != 'winner_only':
        problem = d151.row_problem(row, entry, manifest, here)
        if problem:
            return problem
        details = [row.get('gold'), row.get('two_step')]
    else:
        if row.get('check_path') == 'reused_same_epoch':
            if (d151.sha(row['reused_d151_row']) != row['reused_d151_row_sha256'] or
                    d151.sha(HERE/'admitted.json') != row['reused_d151_admission_sha256']):
                return 'original D151 verification evidence changed'
        for field in ('outcome_epoch', 'checker_epoch', 'budget_s'):
            if row.get(field) != manifest[field]:
                return f'{field} differs'
        if row.get('input_sha256') != entry['input_sha256'] or row.get('crashed'):
            return 'retained identity differs or crashed'
        if (row.get('retained_record_sha256') != entry['retained_record_sha256'] or
                d151.sha(entry['retained_record']) != entry['retained_record_sha256']):
            return 'retained record changed'
        detail = row.get('retained') or {}
        if not detail.get('attempted') or not detail.get('passed'):
            return 'retained winner failed verification'
        if detail.get('finisher_timeout') or detail.get('unavailable'):
            return 'retained not a verdict'
        candidate = d151.candidate_path(here, entry, 'retained')
        if not candidate.exists():
            return 'retained candidate missing'
        value = d151.read(candidate)
        if (value['input_sha256'] != entry['input_sha256'] or value['weighted_moves'] != detail['moves'] or
                value['report']['count']['weighted_nonproductive'] != detail['moves']):
            return 'retained candidate disagrees'
        # Retained verification evidence must preserve the D151 winner's cost.
        record = d151.read(entry['retained_record'])
        if row.get('check_path') == 'reused_same_epoch' and (
                value['plan'] != record['plan'] or value['report'] != record['report']):
            return 'reused candidate differs from original D151 winner'
        if detail['moves'] != record['provenance']['costs'][entry['retained_source']]:
            return 'retained winner cost changed'
        details = [detail]
    for detail in details:
        if detail and detail.get('passed') and detail.get('mask_passed') is not True:
            return 'passed candidate has no passing mask replay'
    sources = [('retained', row.get('retained'))] if entry['mode'] == 'winner_only' else [
        ('gold', row.get('gold')), ('baseline_twostep', row.get('two_step'))]
    for source, detail in sources:
        if detail and detail.get('passed'):
            if not isinstance(detail.get('moves'), (int, float)) or not math.isfinite(detail['moves']) or detail['moves'] < 0:
                return 'invalid candidate cost'
            if d151.read(d151.candidate_path(here, entry, source))['report'].get('passed') is not True:
                return 'candidate checker did not pass'
    return None


def main():
    from llm.exit import outcome_epoch
    from llm.exit.replay import checker_epoch
    from llm.exit.runner import source_pins
    run_dir = HERE/'d172'
    if (run_dir/'manifest.json').exists():
        raise SystemExit('manifest exists; resume via launch.py --run-dir, never replace its epoch')
    inventory = d151.read(run_dir/'inventory/eligible-small.json')
    winners = d151.read(run_dir/'inventory/d151-winners.json')
    old = d151.read(HERE/'d151-manifest.json')
    by_case = {e['case']: e for e in old['entries']}
    val = d151.read(run_dir/'inventory/val-id-exclusions.json')['entries']
    excluded_cases = {v['case'] for v in val}
    excluded_ids = {v['instance_id'] for v in val}
    entries = [dict(e, rung='small', mode='full') for e in inventory if e['case'] not in winners]
    for case, value in winners.items():
        entry = dict(by_case[case], mode='winner_only', retained_record=value['path'],
                     retained_record_sha256=value['sha256'], retained_source=value['source'])
        entries.append(entry)
    if len({e['case'] for e in entries}) != len(entries):
        raise SystemExit('duplicate manifest map')
    for entry in entries:
        if entry['case'] in excluded_cases or entry['instance_id'] in excluded_ids:
            raise SystemExit('held-out map in manifest')
        raw = Path(entry['input']).resolve()
        if not raw.is_relative_to(ROOT/'experiments/mvp/corpus/train') or d151.sha(raw) != entry['input_sha256']:
            raise SystemExit('input not unchanged train file')
        if raw.relative_to(ROOT/'experiments/mvp/corpus') != Path(entry['case']+'.json'):
            raise SystemExit('input and map case differ')
    started = time.time()
    epoch = outcome_epoch.process_snapshot()
    ready = dict(utc=time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()), source_pins=source_pins())
    d151.write(run_dir/'CODE.READY.json', ready)
    manifest = dict(schema='d172-teacher-manifest-v1', uuid=str(uuid.uuid4()),
                    outcome_epoch=outcome_epoch.epoch_id(epoch), checker_epoch=checker_epoch(),
                    budget_s=120., run_start=started, entries=entries, inventory_entries=inventory,
                    excluded_val_cases=sorted(excluded_cases), val_instance_ids=sorted(excluded_ids),
                    d151_best=str(HERE/'best-d151'), expected_outcome_snapshot=epoch,
                    assumptions=['D151 gold_only winners count as existing teacher rows; retain verified same-epoch evidence.',
                                 'Retry the one D151 timeout map; no exclusions may satisfy completion.'])
    d151.write(run_dir/'manifest.json', manifest)
    print(json.dumps(dict(uuid=manifest['uuid'], epoch=manifest['outcome_epoch'], maps=len(entries),
                          full=sum(e['mode'] == 'full' for e in entries), retained=len(winners))))


if __name__ == '__main__':
    import os
    os.environ.update({name: '1' for name in d151.THREADS})
    os.environ.pop('STOWAGE_BALLAST_V4', None)
    main()
