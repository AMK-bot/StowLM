"""Summarize all fixed TRAIN maps, score gates and saved full-check failures.

This is a JSON-only reporter: it never loads maps, runs a solver or claims a new
checker pass. Run-order precedence is explicit; no outcomes select the cohort.
"""
from __future__ import annotations
import argparse
import hashlib
import json
import re
from pathlib import Path
from statistics import mean

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[5]
RUNGS = ('f60', 'f70', 'f80', 'medium12')
PRIMARY = (
    'D192.medium-feeder_500-f60-high-s192200000',
    'D192.medium-feeder_800-f60-low-s192200101',
    'D192.medium-feeder_500-f60-high-s192200002',
    'D192.medium-feeder_500-f60-low-s192200000',
)
C3_PRIMARY = (71., 106., 70., 55.)
C3_BOUNDS = (65., 90., 70., 51.)
# Frozen arm A omitted a HiGHS thread cap in one fallback path. Its actual
# thread count is unverified, so exclude it from compliant-arm comparisons.
KNOWN_INVALID_RUN = HERE / 'results/ladder60-70'
INVALID_REASON = ('Fallback omitted an explicit HiGHS thread cap; compliance was not '
                  'established. The same omission caused a confirmed nine-thread scheduler '
                  'mismatch in the separate f80 smoke; no per-map violation is inferred for A.')
CODES = {
    'expert.e9.over_width_length': 'E9',
    'iacs.member_irs.container_securing': 'S',
    'iscode.loading.draft': 'D',
    'iscode.loading.trim': 'T',
    'iscode.loading.list': 'L',
    'expert.e6.heavy_under_deck': 'E6',
    'solas.v22.visibility': 'V',
    'iscode.a2_3.weather': 'W',
    'iscode.b2_3.containership_second_check': 'A',
}


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def rel(path):
    path = Path(path).resolve()
    try:
        return path.relative_to(ROOT).as_posix()
    except ValueError:
        return str(path)


def finite_mean(values):
    values = [v for v in values if isinstance(v, (int, float))]
    return mean(values) if values else None


def rules_of(trial):
    rules = trial.get('rules') or trial.get('rule_ids') or []
    if isinstance(rules, str):
        rules = [rules]
    rules = list(rules)
    for violation in trial.get('violations') or []:
        if isinstance(violation, str):
            rules.append(violation)
        elif isinstance(violation, dict) and violation.get('rule_id'):
            rules.append(violation['rule_id'])
    return sorted(set(rules))


def checked_trials(row):
    """Keep actual failed full-check trials; construction statuses stay separate."""
    found = []
    for index, trial in enumerate(row.get('method_metadata', {}).get('trials', [])):
        if trial.get('full_check') is False and rules_of(trial):
            found.append(dict(trial, rules=rules_of(trial), gate='method_full_checker',
                              trial_index=index, evidence=row['evidence'], evidence_sha256=row['evidence_sha256']))
    for index, trial in enumerate(row.get('tries', [])):
        if trial.get('passed') is False and rules_of(trial):
            found.append(dict(trial, rules=rules_of(trial), gate='runner_full_checker',
                              trial_index=index, evidence=row['evidence'], evidence_sha256=row['evidence_sha256']))
    return found


def replay_observed(row):
    if row.get('passed') is True:
        return True
    metadata = row.get('method_metadata', {})
    return bool(metadata.get('replay_legal') or any(t.get('replay_legal') is True
                         for t in metadata.get('trials', [])) or row.get('tries'))


def closest(trials):
    def key(t):
        lifts = t.get('lifts', t.get('search_lifts'))
        return (len(t['rules']), lifts if isinstance(lifts, (int, float)) else float('inf'),
                t.get('trial_index', 0))
    return min(trials, key=key, default=None)


def compact_trial(trial):
    if trial is None:
        return None
    keys = ('kind', 'lane', 'gate', 'trial_index', 'rules', 'lifts', 'search_lifts', 'violations',
            'failure_reason', 'replay_legal', 'full_check', 'source', 'warm_reference', 'workload', 'loadavg',
            'diagnostic', 'raw_report', 'raw_plan', 'report_path', 'plan_path', 'report', 'plan', 'full_check_s',
            'evidence', 'evidence_sha256')
    result = {k: trial[k] for k in keys if k in trial}
    ratios = [v['measured']/v['limit'] for v in trial.get('violations', [])
              if isinstance(v, dict) and 'securing' in v.get('rule_id', '')
              and isinstance(v.get('measured'), (int, float))
              and isinstance(v.get('limit'), (int, float)) and v['limit'] > 0]
    if ratios:
        result['max_securing_ratio'] = max(ratios)
    return result


def construction_evidence(row):
    """Extract small statuses; never classify timeouts/infeasible as software errors."""
    reasons, errors = [], []
    def visit(value, path):
        if isinstance(value, dict):
            for key, item in value.items():
                if key in ('violations', 'plan', 'report', 'containers', 'slots', 'yard_addresses'):
                    continue
                location = f'{path}.{key}'
                if isinstance(item, (str, int, float)) and key in (
                        'status', 'reason', 'error', 'exception', 'message', 'failure_reason',
                        'lp_status', 'solver_status'):
                    text = str(item)
                    software = (key in ('error', 'exception') or
                        bool(re.search(r'(?:status\s*[:=]?\s*4\b|\blp error\b|traceback|'
                                       r'exception|thread.*(?:mismatch|scheduler|already)|'
                                       r'(?:runtime|value|type|arithmetic)error)', text, re.I)) or
                        (key in ('lp_status', 'solver_status') and item == 4))
                    entry = dict(path=location, value=item)
                    reasons.append(entry)
                    if software:
                        errors.append(dict(entry, scope='Recorded software/solver error; not a physical rule failure.'))
                if isinstance(item, (dict, list)):
                    visit(item, location)
        elif isinstance(value, list):
            for index, item in enumerate(value):
                visit(item, f'{path}[{index}]')
    visit(row.get('method_metadata', {}), 'method_metadata')
    visit({'candidate_errors': row.get('candidate_errors', []),
           'failure_reason': row.get('failure_reason'), 'tries': row.get('tries', [])}, 'runner')
    if row.get('resource_protocol_status') == 'invalid':
        errors.append(dict(path='run_annotation',value=row.get('resource_protocol_reason'),
            scope='Known arm-level resource defect; occurrence of LP status 4 on this individual map is not inferred.'))
    return reasons, errors


def summarize_row(row):
    if row is None:
        return dict(status='not_measured', measured=False, replay_legal=False, passed=False,
                    lifts=None, parts=None, evidence=None, closest_checked=None,
                    observed_failed_rules=[], failure_layer='not_measured')
    metadata = row.get('method_metadata', {})
    construction_reasons, software_errors = construction_evidence(row)
    trials = checked_trials(row)
    complete_trials = [t for t in metadata.get('trials', []) if t.get('replay_legal')]
    failed = not row.get('passed')
    if not failed:
        layer = None
    elif row.get('status') in ('no_candidates', 'method_error', 'error'):
        layer = 'construction_or_no_returned_candidate'
    elif any(t.get('gate') == 'runner_full_checker' for t in trials):
        layer = 'finished_plan_full_checker'
    else:
        layer = 'finishing_or_candidate_rejection'
    return dict(status=row.get('status'), measured=True, replay_legal=replay_observed(row),
                passed=row.get('passed') is True, lifts=row.get('lifts'), parts=row.get('parts'),
                wall_s=row.get('wall_s'), propose_s=row.get('propose_s'), finish_s=row.get('finish_s'),
                resource_protocol_status=row.get('resource_protocol_status', 'not_independently_audited'),
                resource_protocol_reason=row.get('resource_protocol_reason'),
                software_errors=software_errors, construction_reasons=construction_reasons,
                evidence=row['evidence'], evidence_sha256=row['evidence_sha256'],
                source=metadata.get('source'), warm_reference=metadata.get('warm_reference'),
                source_metadata={k:metadata[k] for k in ('source','warm_reference','reference','seed_source','workload','loadavg','loadavg_start','loadavg_end','work_limits') if k in metadata},
                config=row.get('config'), failure_reason=row.get('failure_reason'), failure_layer=layer,
                closest_checked=compact_trial(closest(trials)),
                observed_failed_rules=sorted({r for t in trials for r in t['rules']}),
                checked_failed_trials=[compact_trial(t) for t in trials],
                complete_replay_trial_count=len(complete_trials),
                incomplete_or_screened_trials=[{k:v for k,v in t.items() if k in
                       ('kind','status','reason','lp_uncovered','left','columns','closed_hold')}
                       for t in metadata.get('trials', []) if not t.get('replay_legal') and not rules_of(t)],
                runner_candidate_errors=row.get('candidate_errors', []),
                runner_finish_attempts=row.get('tries', []),
                replay_provenance='Full pass or explicit method replay flag, otherwise runner tries imply real score accepted complete actions.')


def read_runs(runs, manifest, invalid_runs=()):
    rows = []
    invalid = {KNOWN_INVALID_RUN.resolve()} | {Path(p).resolve() for p in invalid_runs}
    for order, name in enumerate(runs):
        directory = Path(name)
        if not directory.is_dir():
            raise ValueError(f'Run directory does not exist: {name}')
        for path in sorted(directory.glob('*.json')):
            if path.name == 'summary.json':
                continue
            row = json.loads(path.read_text())
            if not isinstance(row, dict) or not row.get('instance_id') or 'passed' not in row:
                continue
            if row.get('input') not in manifest:
                raise ValueError(f'Run row outside fixed cohort: {path}')
            expected = manifest[row['input']]
            if row['instance_id'] != expected['instance_id'] or row.get('input_sha256') != expected['input_sha256']:
                raise ValueError(f'Mismatched input identity/hash in {path}')
            if row.get('complete') is not True:
                raise ValueError(f'Incomplete row is not reportable: {path}')
            invalid_arm = directory.resolve() in invalid
            rows.append(dict(row, evidence=rel(path), evidence_sha256=sha(path), run_order=order,
                resource_protocol_status='invalid' if invalid_arm else 'not_independently_audited',
                resource_protocol_reason=INVALID_REASON if invalid_arm else None))
    return rows


def bound_rows(paths):
    out = {}
    sources = []
    for path in paths:
        path = Path(path)
        if not path.exists():
            continue
        data = json.loads(path.read_text())
        if isinstance(data, dict):
            data = data.get('rows', data.get('results', data.get('maps', [])))
            if isinstance(data, dict):
                data = list(data.values())
        for row in data:
            if not isinstance(row, dict) or row.get('lower_bound') is None:
                continue
            key = row.get('input') or row.get('instance_id')
            if key is None:
                continue
            old = out.get(key)
            if old is None or row['lower_bound'] > old['lower_bound']:
                out[key] = dict(row, evidence=rel(path), evidence_sha256=sha(path))
        sources.append(dict(path=rel(path), sha256=sha(path)))
    return out, sources


def rung_stats(rows):
    measured = [r for r in rows if r['measured']]
    passing = [r for r in measured if r['passed']]
    invalid = sum(r.get('resource_protocol_status') == 'invalid' for r in measured)
    return dict(selected=len(rows), measured=len(measured), invalid_resource_arm_rows=invalid,
                replay_legal=sum(r['replay_legal'] for r in measured),
                full_passes=len(passing),
                mean_lifts_passing=finite_mean(r['lifts'] for r in passing),
                mean_wall_s_measured=(None if invalid else finite_mean(r.get('wall_s') for r in measured)),
                mean_wall_s_descriptive=finite_mean(r.get('wall_s') for r in measured),
                timing_scope=('INVALID RESOURCE ARM: descriptive only, no compliant timing comparison.' if invalid
                              else 'Descriptive observed runtime; resource compliance is not independently established by this reporter.'),
                total_wall_s_measured=sum(r.get('wall_s') or 0 for r in measured),
                missing=len(rows)-len(measured))


def make_report(manifest, rows, bounds, bound_sources, runs):
    by_input = {r['input']:r for r in manifest}
    grouped = {m['input']:[] for m in manifest}
    for row in rows:
        grouped[row['input']].append(row)
    maps = []
    for m in sorted(manifest, key=lambda m:(RUNGS.index(m['rung']),m['input'])):
        attempts = grouped[m['input']]
        latest = attempts[-1] if attempts else None
        passed = [r for r in attempts if r.get('passed')]
        best = min(passed, key=lambda r:(r['lifts'], -r['run_order'], r.get('wall_s',float('inf'))),default=None)
        eligible = [r for r in passed if r.get('resource_protocol_status') != 'invalid']
        best_eligible = min(eligible, key=lambda r:(r['lifts'], -r['run_order'], r.get('wall_s',float('inf'))),default=None)
        all_trials = [t for r in attempts for t in checked_trials(r)]
        maps.append(dict(m, latest=summarize_row(latest), best_passing=summarize_row(best),
                         best_passing_protocol_eligible=summarize_row(best_eligible),
                         checked_failure_closest_any_run=compact_trial(closest(all_trials)),
                         attempted_runs=len(attempts),
                         evidence_rows=[dict(path=r['evidence'],sha256=r['evidence_sha256'],passed=r.get('passed'),status=r.get('status'),
                                             resource_protocol_status=r.get('resource_protocol_status')) for r in attempts]))
    rungs = {}
    for rung in RUNGS:
        selected = [r for r in maps if r['rung'] == rung]
        best_coverage = [dict(r['best_passing'],measured=r['latest']['measured'],
                              replay_legal=any(replay_observed(t) for t in grouped[r['input']])) for r in selected]
        rungs[rung] = dict(latest=rung_stats([r['latest'] for r in selected]),
                           portfolio=rung_stats(best_coverage),
                           protocol_eligible_portfolio_full_passes=sum(r['best_passing_protocol_eligible']['passed'] for r in selected),
                           c3_full_passes={'f60':8,'f70':3}.get(rung),
                           v6_constructor_passes=sum(r.get('v6_passed',False) for r in selected) if rung!='medium12' else None,
                           selected_instance_ids=[r['instance_id'] for r in selected])
        # A best-of-runs portfolio has no single measured execution time.
        rungs[rung]['portfolio']['mean_wall_s_measured'] = None
        rungs[rung]['portfolio']['total_wall_s_measured'] = None
        rungs[rung]['portfolio']['mean_wall_s_descriptive'] = None
        rungs[rung]['portfolio']['timing_scope'] = 'Best-of-runs plan portfolio has no single execution time; may include invalid-arm checked witnesses.'
        rungs[rung]['portfolio']['total_all_attempt_wall_s'] = sum(
            row.get('wall_s') or 0 for row in rows if by_input[row['input']]['rung']==rung)
    primary = []
    for ident,c3ub,c3lb in zip(PRIMARY,C3_PRIMARY,C3_BOUNDS):
        m=next(r for r in maps if r['instance_id']==ident)
        b=bounds.get(m['input'],bounds.get(ident))
        if b is not None and b.get('input_sha256') not in (None,m['input_sha256']):
            raise ValueError(f'Bound input hash mismatch for {ident}')
        lb=b['lower_bound'] if b else c3lb
        best=m['best_passing']
        ub=best['lifts'] if best['passed'] else None
        primary.append(dict(instance_id=ident,input=m['input'],c3_lifts=c3ub,c3_lower_bound=c3lb,
                 current_measured_lifts=ub,parts=best.get('parts'),lower_bound=lb,
                 gap=ub-lb if ub is not None else None,
                 best_evidence=best.get('evidence'),bound_evidence=b.get('evidence') if b else None,
                 bound_evidence_sha256=b.get('evidence_sha256') if b else None,
                 bound_status=b.get('status') if b else 'C3 prior certificate',
                 proof_scope='Fewest checker-passing lifts under D15 fixed-stow, no later loads projection.'))
    arms=[]
    for index,name in enumerate(runs):
        arm_rows=[r for r in rows if r['run_order']==index]
        arms.append(dict(run=rel(name), resource_protocol_status=('invalid' if any(
            r.get('resource_protocol_status')=='invalid' for r in arm_rows) else 'not_independently_audited'),
            resource_protocol_reason=next((r.get('resource_protocol_reason') for r in arm_rows if r.get('resource_protocol_reason')),None),
            outcomes=rung_stats([summarize_row(r) for r in arm_rows])))
    return dict(schema_version=1,runs=[rel(p) for p in runs],arms=arms,rungs=rungs,maps=maps,primary=primary,
       primary_measured_passes=sum(p['current_measured_lifts'] is not None for p in primary),
       primary_mean_lifts=(finite_mean(p['current_measured_lifts'] for p in primary)
                           if all(p['current_measured_lifts'] is not None for p in primary) else None),
       bound_sources=bound_sources,
       context=dict(c3_passes={'f60':8,'f70':3},v6_constructor_passes={'f60':5,'f70':0,'f80':0},
           v6='Constructor built to pass, not a lift benchmark. Four C3 warm primary maps were preselected from V6 passes.',
           whole_cohort='Every fixed TRAIN map is reported, including missing and failed rows; cohort selected independently of outcome.',
           comparator='No fresh 0.9.0 arm is inferred from stored rows; C3 comparisons are historical context only.',
           e9='The authorized E9 search-mask fix is inherited. A pass difference against an unfixed arm cannot be credited entirely to solver changes.',
           projection='Medium lift counts include projected later-port lifts under D15: fixed stow, no later loads.',
           load='Wall-clock runs are load dependent; per-row source_metadata retains workload where supplied.',
           gates='Pass = saved lab/score.py final unchanged full-checker gate; replay alone is not a pass.',
           selection='Latest = last supplied run containing this input; portfolio = lowest-lift full pass across supplied runs.',
           resource_protocol='Arm A is excluded from compliant-arm comparisons because its fallback thread cap was missing, not because every map was observed using excess threads. The confirmed nine-thread mismatch occurred in the separate f80 smoke. Checked A plans remain witnesses. Other arms are not independently resource audited by this JSON-only reporter.',
           portfolio_resource_scope='Portfolio includes any full-checked witness, including invalid resource arms; protocol_eligible_portfolio excludes explicitly invalid arms without asserting independent compliance.',
           closest='Fewest distinct failing full-check rule IDs, then lifts, then trial index; diagnostic only, not physical distance.',
           physics='A second-check parent rule ID alone does not identify its failing submetric. Detailed violations are retained.',
           unknown='No-candidate returns, restricted-column uncovered mass and timeouts do not prove physical infeasibility.'))


def fmt(value):
    if value is None:
        return '—'
    if isinstance(value,float):
        return f'{value:.2f}'.rstrip('0').rstrip('.')
    return str(value)


def escape(value):
    return str(value).replace('|','\\|').replace('\n',' ')


def local_link(path):
    path = Path(path)
    return str(path if path.is_absolute() else ROOT/path)


def rule_text(trial):
    if not trial:
        return 'no full-check failure recorded'
    return ', '.join(CODES.get(r,r) for r in trial['rules'])


def markdown(report):
    lines=['# Cycle 4: complete fixed cohorts and failure gates','',
      'All 48 fixed TRAIN development maps are listed below. f60/f70/f80 each contain all 12 V6 metadata maps, independent of whether that constructor passed; medium12 is the fixed D185 cohort. A full pass is the final unchanged checker gate saved by `lab/score.py`. This reporter runs no checker.','',
      'V6 is a constructor built to pass, not a lift benchmark. C3 passed 8/12 f60 and 3/12 f70. Its four warm primary maps were preselected from V6-passing maps; historical results are context, not a fresh controlled comparison. Lift totals include the D15 fixed-stow, no-later-loads projection.','',
      '“Latest” uses the last supplied run per input. “Portfolio” keeps the lowest-lift full pass across supplied runs. Missing maps remain visible. Wall-clock budgets are load dependent; row workload/source information is in [REPORT.json](REPORT.json).','',
      '**Arm A `results/ladder60-70` is excluded from resource-compliant comparisons:** one fallback lacked an explicit HiGHS thread cap. A nine-thread scheduler mismatch was confirmed in the separate f80 smoke; individual A thread violations are not inferred. Its checked plans remain physical witnesses, but its timing/search arm is conservatively excluded. JSON separately counts passes excluding annotated arms. This JSON-only reporter does not independently audit resource use.','',
      '| Rung | Selected | Latest measured / replay / full | Portfolio replay / full | Mean lifts of latest passes | Mean latest wall s |',
      '|---|---:|---:|---:|---:|---:|']
    for rung in RUNGS:
        r=report['rungs'][rung];a=r['latest'];b=r['portfolio']
        lines.append(f"| {rung} | {a['selected']} | {a['measured']} / {a['replay_legal']} / {a['full_passes']} | {b['replay_legal']} / {b['full_passes']} | {fmt(a['mean_lifts_passing'])} | {fmt(a['mean_wall_s_measured'])} |")
    lines += ['','Codes: **S** securing; **D** draft; **T** trim; **L** list; **V** visibility; **E6** heavy-under-deck; **E9** OOG width/length clearance; **W** weather; **A** containership second check. Full IDs and measured/limit details are retained below and in JSON. A alone does not identify area-to-flood.','',
      '## Every map','',
      '| Map | Latest status | Replay / full | Latest lifts | Portfolio lifts | Closest checked failure (latest) | Source |',
      '|---|---|---:|---:|---:|---|---|']
    for m in report['maps']:
        a=m['latest'];b=m['best_passing'];name=m['instance_id'].removeprefix('D192.medium-')
        evidence=a.get('evidence');link=f'[{Path(evidence).parent.name}]({local_link(evidence)})' if evidence else '—'
        lines.append(f"| {name} | {a['status']} | {int(a['replay_legal'])} / {int(a['passed'])} | {fmt(a['lifts'])} | {fmt(b['lifts'])} | {'—' if a['passed'] else escape(rule_text(a.get('closest_checked')))} | {link} |")
    lines += ['','## Every failed latest outcome','',
      'No-candidate/construction outcomes and full-checker failures are separate layers. A method may full-check and discard complete trial stows before returning zero candidates; its trial failures remain real evidence about those stows. Restricted page pools, cell screens, bounded searches and timeouts never establish physical infeasibility.','']
    for m in report['maps']:
        a=m['latest']
        if a['passed']:
            continue
        lines += [f"### {m['instance_id']}",'',f"Outcome: **{a['status']}**; layer: `{a['failure_layer']}`. Replay observed: **{a['replay_legal']}**. {escape(a.get('failure_reason') or 'No completed score row available.')}",'']
        if a.get('software_errors'):
            lines += ['Software/resource evidence (separate from physical checker rules):','']
            for error in a['software_errors']:
                lines.append(f"- `{escape(error['path'])}`: {escape(error['value'])} {escape(error.get('scope',''))}")
            lines.append('')
        if a.get('construction_reasons'):
            lines += ['Construction/solver reasons: ' + '; '.join(
                f"`{escape(t['path'])}`={escape(t['value'])}" for t in a['construction_reasons']) + '.', '']
        trial=a.get('closest_checked')
        if not trial:
            lines += ['No full-check failure rule was recorded for the latest outcome. Construction failure is not a failing physical rule.','']
            continue
        lines += [f"Closest checked trial: `{trial.get('kind',trial.get('gate'))}`; lifts {fmt(trial.get('lifts',trial.get('search_lifts')))}; rules: {', '.join('`'+r+'`' for r in trial['rules'])}.",'']
        if trial.get('max_securing_ratio') is not None:
            lines += [f"Largest saved securing ratio: **{trial['max_securing_ratio']:.4f}** (limit 1).",'']
        detailed=[v for v in trial.get('violations',[]) if isinstance(v,dict)]
        if detailed:
            examples = {}
            for violation in detailed:
                examples.setdefault(violation.get('rule_id'), violation)
            lines += ['First saved violation per distinct rule is displayed; all '
                      +str(len(detailed))+' violations remain in REPORT.json and the evidence row.','']
            lines += ['| Rule | Message | Measured | Limit |','|---|---|---|---|']
            for v in examples.values():
                lines.append('| '+' | '.join(escape(v.get(k,'—')) for k in ('rule_id','message','measured','limit'))+' |')
            lines.append('')
        else:
            lines += ['Detailed measured/limit violations were not saved for this trial; no failing submetric is inferred.','']
        if a.get('evidence'):
            lines += [f"Evidence: [{a['evidence']}]({local_link(a['evidence'])}); SHA-256 `{a['evidence_sha256']}`.",'']
    lines += ['## Primary lift bounds','',
       '| Map | C3 lifts | Current supplied-run best | Parts | Lower bound | Gap |',
       '|---|---:|---:|---|---:|---:|']
    for p in report['primary']:
        lines.append(f"| {p['instance_id'].removeprefix('D192.medium-')} | {fmt(p['c3_lifts'])} | {fmt(p['current_measured_lifts'])} | {escape(p['parts'])} | {fmt(p['lower_bound'])} | {fmt(p['gap'])} |")
    lines += ['','Any zero gap refers to fewest lifts among checker-passing plans under the D15 fixed-stow, no-later-loads projection. A bound solver’s own optimal status refers to its relaxation. Necessary physics rows may leave the bound unchanged; the reporter takes only saved lower-bound certificates, never an incumbent objective as a bound.','']
    return '\n'.join(lines)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--runs',nargs='+',required=True,help='Score run directories, oldest to newest; patches may follow cohorts.')
    parser.add_argument('--bounds',nargs='*',default=None,help='Optional saved bound result JSON files; strongest per map retained.')
    parser.add_argument('--invalid-runs',nargs='*',default=[],help='Additional resource-invalid arms. Frozen results/ladder60-70 is always flagged.')
    args=parser.parse_args()
    manifest=json.loads((HERE/'MANIFEST.json').read_text())
    assert len(manifest)==48 and len({r['input'] for r in manifest})==48
    rows=read_runs(args.runs,{r['input']:r for r in manifest},args.invalid_runs)
    paths=args.bounds if args.bounds is not None else [HERE/'bound_results.json',HERE/'bound_tiers_results.json']
    bounds,sources=bound_rows(paths)
    report=make_report(manifest,rows,bounds,sources,args.runs)
    (HERE/'REPORT.json').write_text(json.dumps(report,indent=2,sort_keys=True)+'\n')
    (HERE/'FAILURES.md').write_text(markdown(report))
    print(json.dumps(dict(rungs=report['rungs'],primary=report['primary']),indent=2))


if __name__=='__main__':
    main()
