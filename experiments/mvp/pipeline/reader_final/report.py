"""Write RESULTS.md, hero-readings.json and results.json from what exists. Safe to rerun.

Every plan is rechecked here by a fresh full checker and a fresh rule audit, in this process.
Binding means D166: the no-rule plan passes and breaks the rule, the new plan passes and differs.
The no-rule plan is D211's stored Frontier 0.9 plan, freshly checked: no new baseline search was run.
Usage: python -m experiments.mvp.pipeline.reader_final.report
"""
from __future__ import annotations

import json
from pathlib import Path

from .plan_cases import finished, jobs, same_meaning
from .read_all import HERE, QUEUE, READINGS, TIMING, d211_cases, load_rows
from .reader import ROOT, identity, reader_label, reading_key

D211_PASSED = {'dg_keep_away-02': False, 'dg_keep_away-03': False, 'dg_keep_away-05': False, 'one_port_per_hatch-04': False}


def judged(queue, rows, known):
    """One row per queued sentence: what the reader said, and whether it matched."""
    result = []
    for item in queue:
        row = rows.get(reading_key(item['sentence'], item['context']))
        if row is None:
            result.append({**_public(item), 'status': 'not_read', 'correct': None, 'label': None})
            continue
        raw = row['evidence'].get('raw')
        try:
            answer = json.loads(raw) if row['status'] == 'read' else None
        except (TypeError, ValueError):
            answer = None
        correct = same_meaning(answer, item['expected'], item['context']) if item['expected'] else None
        result.append({**_public(item), 'status': row['status'], 'reading': answer, 'correct': correct,
                       'label': reader_label(row['evidence'], known), 'evidence': row['evidence']})
    return result


def _public(item):
    return {key: item[key] for key in ('group', 'case', 'sentence', 'expected', 'demo')}


def tally(rows):
    """A sentence with an expected answer is in the denominator whether or not it was read."""
    scored = [row for row in rows if row['expected'] is not None]
    return {'sentences': len(rows), 'scored': len(scored), 'correct': sum(row['correct'] is True for row in scored),
            'unread': sum(row['status'] == 'not_read' for row in scored),
            'no_expected_answer': len(rows) - len(scored)}


def case_outcome(job):
    paths = finished(job)
    return json.loads(paths[-1].read_text()) if paths else None


def plan_row(case, outcome, base):
    """Fresh checks of the rule plan against the no-rule plan on the same map."""
    from stowage.model import MovePlan
    from experiments.mvp.pipeline.runner.core import check
    from experiments.mvp.reader.pipeline import intrinsic_violations, plan_signature
    from experiments.mvp.reader.schema import validate_answer, context_from_instance
    from stowage.model import Instance
    row = dict(case=case['case_id'], status=outcome['status'] if outcome else 'not_run',
               passed=bool(outcome and outcome.get('passed')), lifts=(outcome or {}).get('lifts'),
               level=(outcome or {}).get('level'), wall_s=(outcome or {}).get('wall_s'),
               base_status=base['status'] if base else 'not_run', base_lifts=(base or {}).get('lifts'),
               d211_passed=D211_PASSED.get(case['case_id'], True), message=(outcome or {}).get('message'))
    if not outcome or not outcome.get('plan'):
        return row
    instance = Instance.model_validate(outcome['instance'])
    answer = validate_answer(json.dumps(case['expected']), context_from_instance(instance))
    plan = MovePlan.model_validate(outcome['plan'])
    report = check(instance, plan)
    row.update(fresh_checker=bool(report.passed), fresh_rule_violations=len(intrinsic_violations(instance, answer, plan)))
    if base and base.get('plan'):
        before = MovePlan.model_validate(base['plan'])
        base = {**base, 'passed': bool(check(instance, before).passed)}
        row['base_fresh_checker'] = base['passed']
        broke = len(intrinsic_violations(instance, answer, before))
        changed = plan_signature(before) != plan_signature(plan)
        row.update(base_broke_rule=broke > 0, plan_changed=changed, binding=bool(
            base.get('passed') and broke > 0 and changed and row['passed'] and row['fresh_checker']
            and not row['fresh_rule_violations']))
    return row


def stored_baseline(case, instance_check=None):
    """D211's stored 0.9 plan, as a dictionary plan_row can compare."""
    data = json.loads((ROOT / case['baseline_file']).read_text())
    return {'status': 'stored_frontier_0.9', 'plan': data.get('plan'), 'passed': True,
            'lifts': case.get('baseline_weighted')}


def plans(judgements):
    rows = []
    for case in d211_cases():
        job = next(job for job in jobs() if job['id'] == case['case_id'])
        read = next((item for item in judgements if item['group'] == 'd211' and item['case'] == job['id']), {})
        row = plan_row(case, case_outcome(job), stored_baseline(case))
        rows.append({**row, 'sentence': job['sentence'], 'reading_correct': read.get('correct')})
    return rows


def markdown(known, timing, d211, demo, wrong, rows, label_false):
    good = [row for row in rows if row['passed']]
    lines = ['# Adopted reader, end to end (task 1.2)', '',
             f"Adopted: `{known['name']}` (`{known['adapter_sha256'][:12]}…`), decoder `{known['decoder_sha256'][:12]}…`, the sealed-scoring mask. "
             'Synthetic ships and cargo, simulated port. Development maps, not a sealed result.', '',
             '## Reading', '',
             f"D211 sentences read correctly: **{d211['correct']} of {d211['scored']}**. These rows repeat a few sentences across ships, and "
             'they came from an earlier sealed reader set, so this is not a fresh accuracy sample.', '',
             f"Demo sentences with an expected answer: **{demo['correct']} of {demo['scored']}**; {demo['no_expected_answer']} have none to compare with.", '',
             f"Timing: {json.dumps({key: round(value, 2) for key, value in timing.items() if isinstance(value, float)})}. Shared machine.", '']
    if label_false:
        lines += [f'**{label_false} readings failed the label check and must not carry the on-device label.**', '']
    lines += ['## Wrong readings', '']
    lines += [f"- {row['group']} `{row['case']}`: \"{row['sentence']}\" expected `{json.dumps(row['expected'])}`, read `{json.dumps(row.get('reading'))}`"
              for row in wrong] or ['None.']
    lines += ['', '## Said, read, confirmed, replanned', '',
              f"{sum(row['status'] != 'not_run' for row in rows)} of {len(rows)} runs finished. Full-checker passes through the runner: {len(good)}. "
              f"Binding replans: {sum(row.get('binding') is True for row in rows)}. Twelve of D211's 20 cases, chosen to span all four rule types; "
              'one measured path, not a benchmark. \"Before\" is D211\'s stored Frontier 0.9 plan, so a change also reflects the newer engine.', '',
              '| Case | Read right | Status | Lifts | 0.9 lifts | Rule broken before | Changed | Fresh checker | Rule breaks | Binding | Wall s |',
              '|---|---|---|---:|---:|---|---|---|---:|---|---:|']
    for row in rows:
        lines.append('| ' + ' | '.join(str(row.get(key, '—')) if row.get(key) is not None else '—' for key in
                     ('case', 'reading_correct', 'status', 'lifts', 'base_lifts', 'base_broke_rule', 'plan_changed',
                      'fresh_checker', 'fresh_rule_violations', 'binding')) + f" | {row['wall_s'] or 0:.0f} |")
    lines += ['', 'A planner comparing the reading with the registered meaning is simulated: `confirm` accepts only an exact match. '
              'Lift differences on these maps are search noise as much as rule cost (logic review 3, B1), so no cost claim follows.', '',
              'p22-demo: show only readings in `readings/hero-readings.json` whose `correct` isn\'t false.', '']
    return '\n'.join(lines)


def main():
    known = identity()
    queue = json.loads(QUEUE.read_text())
    judgements = judged(queue, load_rows(), known)
    timing = json.loads(TIMING.read_text()) if TIMING.exists() else {}
    timing = {key: value for key, value in timing.items() if key != 'identity'}
    d211 = tally([row for row in judgements if row['group'] == 'd211'])
    demo_rows = [row for row in judgements if row['demo']]
    wrong = [row for row in judgements if row['correct'] is False and (row['demo'] or row['group'] == 'd211')]
    label_false = sum(row['label'] not in (None,) and 'StowLM' not in row['label'] for row in judgements)
    rows = plans(judgements)
    (READINGS / 'hero-readings.json').write_text(json.dumps(
        {'adopted': known, 'readings': demo_rows, 'wrong': wrong}, indent=2, ensure_ascii=False) + '\n')
    voice = [row for row in judgements if row['group'].startswith('voice-')]
    (HERE / 'results.json').write_text(json.dumps(
        {'adopted': known, 'timing': timing, 'd211_reading': d211, 'demo_reading': tally(demo_rows),
         'voice_all_clips': tally(voice), 'wrong': wrong, 'plans': rows}, indent=2, ensure_ascii=False) + '\n')
    (HERE / 'RESULTS.md').write_text(markdown(known, timing, d211, tally(demo_rows), wrong, rows, label_false))
    print(json.dumps({'d211': d211, 'demo': tally(demo_rows), 'wrong': len(wrong),
                      'finished': sum(row['status'] != 'not_run' for row in rows)}))


if __name__ == '__main__':
    main()
