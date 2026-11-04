"""Registered map-level reports from audit rows; never opens maps or result plans."""
from __future__ import annotations

from collections import Counter
import csv
import json
import math
from pathlib import Path
import random
import statistics

from experiments.mvp.pipeline.contract.schema import canonical_json
from experiments.mvp.pipeline.sealed.instructions import RULE_TYPES

COHORTS = {'tiny15': 15, 'small30': 30, 'envelope24': 24}
ARMS = ('pipeline', 'twostep', 'instruction', 'twostep_300')
TWOSTEP_LABEL = "a simple two-step planner, modelled from one field expert's description"
METRICS = ('weighted_lifts', 'lifts_total', 'lifts_current_port',
           'lifts_projected_later_port')
CONFOUND = ('Port blocking and different relocation policies can explain savings; '
            'experienced planners may already block.')
CSV_FIELDS = ('cohort', 'map_id', 'map_sha256', 'ship_id', 'arm', 'status', 'level',
              'passed', 'full_checker_pass', 'instruction_recheck',
              'counterfactual_instruction_recheck', 'audit_verified', 'audit_fresh_process',
              'lifts_current_port', 'lifts_projected_later_port', 'lifts_total',
              'weighted_lifts', 'wall_s', 'reading_wall_s',
              'child_elapsed_s', 'child_timeout_s', 'child_timed_out', 'child_cleanup_failed',
              'driver_elapsed_s', 'driver_admission_wait_s',
              'audit_elapsed_s', 'audit_timeout_s', 'audit_timed_out', 'audit_cleanup_failed',
              'deadline_exceeded', 'deadline_with_incumbent', 'deadline_incumbent_verified',
              'hard_wall_s', 'first_checked_wall_s',
              'instruction_type',
              'instruction_target_count', 'no_matching_cargo', 'fixture_surrogate',
              'reader_failure', 'engine_failure', 'reading_status', 'confirmation_source',
              'plan_difference_boxes', 'instruction_lifts_total_change',
              'instruction_lifts_current_port_change',
              'reason', 'error', 'instruction_unavailable', 'audit_error', 'dispatched')


def _level(value):
    return 'L'+str(value) if type(value) is int and 0 <= value <= 5 else value


def passed(row):
    """Only L0 checker passes with a fresh independent audit count."""
    return ((row.get('status') == 'checked_plan' or
             (row.get('status') == 'deadline_exceeded' and row.get('deadline_with_incumbent') is True))
            and _level(row.get('level')) == 'L0'
            and row.get('passed') is True and row.get('full_checker_pass') is True
            and row.get('audit_verified') is True and row.get('audit_fresh_process') is True
            and (row.get('arm') != 'instruction' or row.get('instruction_recheck') is True))


def binomial95(k, n):
    """Exact two-sided Clopper–Pearson interval, with binomial-tail inversion."""
    if type(k) is not int or type(n) is not int or not 0 <= k <= n:
        raise ValueError('binomial counts require 0 <= k <= n')
    if not n:
        return None

    def tail(p, start, end):
        return sum(math.comb(n, j)*p**j*(1-p)**(n-j) for j in range(start, end))

    def invert(start, end, increasing):
        lo, hi = 0., 1.
        for _ in range(64):
            mid = (lo+hi)/2
            value = tail(mid, start, end)
            if (value < .025) == increasing:
                lo = mid
            else:
                hi = mid
        return (lo+hi)/2

    return [0. if not k else invert(k, n+1, True),
            1. if k == n else invert(0, k+1, False)]


def _number(value):
    return type(value) in (int, float) and math.isfinite(value) and value >= 0


def _mean(values):
    return statistics.mean(values) if values else None


def _interval(values):
    values.sort()
    return [values[249], values[9749]] if values else None


def compare(left, right, *, field='weighted_lifts', expected=None):
    """Paired mean left-minus-right, negative favours left; no imputation."""
    mutual = sorted(i for i in left.keys() & right.keys()
                    if passed(left[i]) and passed(right[i]))
    ids = [i for i in mutual if _number(left[i].get(field)) and _number(right[i].get(field))]
    pairs = [(left[i][field], right[i][field]) for i in ids]
    diffs = [a-b for a, b in pairs]
    bootstrap = []
    savings = []
    if pairs:
        rng = random.Random(0)
        for _ in range(10000):
            sample = rng.choices(pairs, k=len(pairs))
            a, b = sum(x for x, _ in sample), sum(y for _, y in sample)
            bootstrap.append((a-b)/len(sample))
            if b:
                savings.append(1-a/b)
    savings.sort()
    saving_interval = ([savings[max(0, math.ceil(.025*len(savings))-1)],
                        savings[max(0, math.ceil(.975*len(savings))-1)]] if savings else None)
    a, b = _mean([x for x, _ in pairs]), _mean([y for _, y in pairs])
    denominator = len(left.keys() | right.keys()) if expected is None else expected
    return dict(metric=field, mutual_passes=len(mutual),
                mutual_passes_denominator=denominator,
                mutual_passes_exact95=binomial95(len(mutual), denominator),
                n=len(ids), map_ids=ids,
                missing_metric_map_ids=sorted(set(mutual)-set(ids)),
                wins=sum(d < 0 for d in diffs), ties=sum(d == 0 for d in diffs),
                losses=sum(d > 0 for d in diffs), mean_left=a, mean_right=b,
                mean_difference_left_minus_right=_mean(diffs),
                paired_bootstrap95_mean_difference=_interval(bootstrap),
                saving_fraction=1-a/b if b else None,
                paired_bootstrap95_saving_fraction=saving_interval,
                saving_interval_resamples=len(savings), bootstrap_resamples=10000,
                bootstrap_seed=0, interval='paired percentile 95%; nearest rank',
                available=bool(ids) and len(ids) == len(mutual),
                relocation_policy_confound=CONFOUND)


def _checks(values, expected):
    count = sum(v is True for v in values)
    return dict(passed=count, passed_denominator=expected,
                passed_exact95=binomial95(count, expected),
                failed=sum(v is False for v in values),
                unavailable=sum(v is None for v in values)+max(0, expected-len(values)))


def _reader_failed(row):
    return row.get('reader_failure') is True or row.get('reading_status') in {
        'error', 'mismatch', 'refused', 'reader_error', 'reader_mismatch', 'rejected'}


def _engine_failed(row):
    if _reader_failed(row) or row.get('status') in {'out_of_envelope', 'constraint_unavailable', 'not_run'}:
        return False
    return row.get('engine_failure') is True or (row.get('arm') == 'instruction'
        and row.get('reading_status') in {'accepted', 'ok', 'matched'} and not passed(row))


def _observed_timing(values, field):
    observed = [r[field] for r in values.values() if _number(r.get(field))]
    return dict(n=len(observed), mean=_mean(observed), total=sum(observed),
                minimum=min(observed) if observed else None,
                maximum=max(observed) if observed else None)


def _arm_summary(values, expected):
    passes = [r for r in values.values() if passed(r)]
    statuses = Counter(r['status'] for r in values.values())
    relaxed = Counter(_level(r.get('level')) or 'unknown' for r in values.values()
                      if r['status'] == 'relaxed_plan')
    wall = [r['wall_s'] for r in values.values() if _number(r.get('wall_s'))]
    reading_wall = [r['reading_wall_s'] for r in values.values() if _number(r.get('reading_wall_s'))]
    return dict(expected_maps=expected, rows=len(values), missing_rows=max(0, expected-len(values)),
                passes_L0=len(passes), passes_L0_exact95=binomial95(len(passes), expected),
                pass_interval_method='exact two-sided Clopper-Pearson 95%',
                pass_fraction=len(passes)/expected if expected else None,
                out_of_envelope=statuses.get('out_of_envelope', 0),
                relaxed_by_level=dict(sorted(relaxed.items())), statuses=dict(sorted(statuses.items())),
                child_elapsed_s=_observed_timing(values, 'child_elapsed_s'),
                audit_elapsed_s=_observed_timing(values, 'audit_elapsed_s'),
                child_timeouts=sum(r.get('child_timed_out') is True for r in values.values()),
                audit_timeouts=sum(r.get('audit_timed_out') is True for r in values.values()),
                child_cleanup_failures=sum(r.get('child_cleanup_failed') is True for r in values.values()),
                audit_cleanup_failures=sum(r.get('audit_cleanup_failed') is True for r in values.values()),
                reader_failures=sum(_reader_failed(r) for r in values.values()),
                engine_failures=sum(_engine_failed(r) for r in values.values()),
                instruction_recheck=_checks([r.get('instruction_recheck') for r in values.values()], expected),
                instruction_target_presence=dict(
                    no_matching_cargo=sum(r.get('no_matching_cargo') is True for r in values.values()),
                    matching_cargo=sum(r.get('no_matching_cargo') is False for r in values.values()),
                    not_applicable_or_unavailable=sum(r.get('no_matching_cargo') is None for r in values.values())),
                counterfactual_instruction_recheck=_checks(
                    [r.get('counterfactual_instruction_recheck') for r in values.values()], expected),
                counterfactual_instruction_recheck_over_L0_passes=_checks(
                    [r.get('counterfactual_instruction_recheck') for r in passes], len(passes)),
                lift_means_over_passes={key: dict(n=sum(_number(r.get(key)) for r in passes),
                    mean=_mean([r[key] for r in passes if _number(r.get(key))])) for key in METRICS},
                reading_wall_s=dict(n=len(reading_wall), mean=_mean(reading_wall), total=sum(reading_wall)),
                wall_s=dict(n=len(wall), mean=_mean(wall), total=sum(wall),
                            minimum=min(wall) if wall else None, maximum=max(wall) if wall else None))


def _audited_plan(row):
    return (row.get('audit_verified') is True and row.get('audit_fresh_process') is True
            and (row.get('plan') is not None or row.get('status') in {'checked_plan', 'relaxed_plan'}))


def _plan_difference(left, right):
    if not _audited_plan(left) or not _audited_plan(right):
        return None
    a, b = left.get('final_cells'), right.get('final_cells')
    if not isinstance(a, dict) or not isinstance(b, dict):
        return None

    def cells(value):
        return sorted(value) if isinstance(value, list) else value

    return sum(cells(a.get(box)) != cells(b.get(box)) for box in a.keys() | b.keys())


def _instruction_binding(baseline, instructed, expected):
    ids = sorted(baseline.keys() | instructed.keys())
    broken, already = [], []
    for iid in ids:
        base = baseline.get(iid, {})
        if not _audited_plan(base):
            continue
        if base.get('counterfactual_instruction_recheck') is False:
            broken.append(iid)
        elif base.get('counterfactual_instruction_recheck') is True:
            already.append(iid)
    obedient = [i for i in broken if passed(instructed.get(i, {}))]
    diffs = {i: _plan_difference(baseline.get(i, {}), instructed.get(i, {})) for i in ids}
    measures = {k: compare(instructed, baseline, field=k, expected=expected) for k in METRICS}
    noise = {k: compare({i: instructed[i] for i in already if i in instructed},
                       {i: baseline[i] for i in already}, field=k, expected=len(already)) for k in METRICS}
    return dict(expected_maps=expected, rule_broken_maps=len(broken), rule_broken_map_ids=broken,
                binding_obedient_passes=len(obedient), binding_obedient_map_ids=obedient,
                binding_obedient_exact95=binomial95(len(obedient), len(broken)),
                binding_obedient_denominator='maps with a fresh-audited no-rule plan that broke the rule',
                already_obeyed=len(already), already_obeyed_map_ids=already,
                already_obeyed_exact95=binomial95(len(already), expected),
                counterfactual_unavailable=max(0, expected-len(broken)-len(already)),
                plan_difference=dict(by_map=diffs,
                    n=sum(v is not None for v in diffs.values()),
                    mean_boxes=_mean([v for v in diffs.values() if v is not None]),
                    definition='load-list boxes occupying different final cells; a 40-foot box counts once'),
                signed_lift_change=measures, already_obeyed_noise_floor=noise,
                cost_sign='instructed minus no-rule baseline; negative changes are search variation',
                cost_population='mutual fresh-audited L0 passes only')


def _pass_table(left, right, ids):
    cells = Counter((passed(left.get(i, {})), passed(right.get(i, {}))) for i in ids)
    n = len(ids)
    return dict(expected_maps=n, both_pass=cells[True, True], left_only=cells[True, False],
                right_only=cells[False, True], neither_pass=cells[False, False],
                left_only_exact95=binomial95(cells[True, False], n),
                right_only_exact95=binomial95(cells[False, True], n),
                neither_pass_exact95=binomial95(cells[False, False], n),
                left_passes=cells[True, True]+cells[True, False],
                left_passes_exact95=binomial95(cells[True, True]+cells[True, False], n),
                right_passes=cells[True, True]+cells[False, True],
                right_passes_exact95=binomial95(cells[True, True]+cells[False, True], n),
                both_pass_exact95=binomial95(cells[True, True], n),
                missing_rows_count_as_nonpass=True)


def _population(cells, expected, ids):
    pairs = [('pipeline_vs_twostep', 'pipeline', 'twostep')]
    if 'instruction' in cells:
        pairs.append(('instruction_vs_pipeline', 'instruction', 'pipeline'))
    if 'twostep_300' in cells:
        pairs.append(('pipeline_vs_twostep_300', 'pipeline', 'twostep_300'))
    return dict(expected_maps=expected,
        arms={a: _arm_summary(values, expected) for a, values in cells.items()},
        comparisons={name: {k: compare(cells[a], cells[b], field=k, expected=expected)
                            for k in METRICS} for name, a, b in pairs},
        pass_tables={name: _pass_table(cells[a], cells[b], ids) for name, a, b in pairs})


def _ship_id(row):
    value = row.get('ship_id', row.get('vessel_id'))
    return str(value) if value is not None else 'unknown'


def _ship_group(ship):
    # These are labels of registered public ships, not inferred outcome strata.
    if '1700' in ship.replace(',', '') or '1_700' in ship:
        return '1700_TEU'
    if '500' in ship or '800' in ship:
        return '500-800_TEU'
    return 'unknown'


def _expected(metadata):
    maps = metadata.get('expected_maps')
    if maps is not None:
        identities = [(m['cohort'], m['map_id']) for m in maps]
        if len(identities) != len(set(identities)):
            raise ValueError('duplicate expected map')
        if any(c not in COHORTS for c, _ in identities):
            raise ValueError('unknown expected cohort')
        return {c: sum(k == c for k, _ in identities) for c in COHORTS}, set(identities)
    sizes = metadata.get('expected_cohort_sizes', COHORTS)
    if set(sizes) != set(COHORTS) or any(type(v) is not int or v < 0 for v in sizes.values()):
        raise ValueError('expected_cohort_sizes must name all three cohorts with nonnegative counts')
    return sizes, None


def _lift_claim(cohorts, eligible):
    env = cohorts['envelope24']['comparisons']['pipeline_vs_twostep']
    medium = env['lifts_total']['mutual_passes'] >= 8
    cohort = 'envelope24' if medium else 'small30'
    population = cohorts[cohort]
    if not medium:
        population = population.get('ship_groups', {}).get('500-800_TEU')
    pairs = population['comparisons']['pipeline_vs_twostep'] if population else None
    total = pairs['lifts_total'] if pairs else compare({}, {}, field='lifts_total')
    current = pairs['lifts_current_port'] if pairs else compare({}, {}, field='lifts_current_port')
    interval = total['paired_bootstrap95_mean_difference']
    supported = bool(total['available'] and interval is not None and interval[1] < 0)
    current_interval = current['paired_bootstrap95_mean_difference']
    current_complete = bool(current['available'] and current_interval is not None
                            and current['map_ids'] == total['map_ids'])
    current_saving = bool(current_complete and current_interval[1] < 0)
    quotable = bool(eligible and supported and current_complete)
    reason = ('paired interval establishes fewer extra crane lifts' if supported else
              'paired interval does not establish fewer extra crane lifts')
    if supported and not current_complete:
        reason = 'current-port paired interval incomplete; primary quotation withheld'
    return dict(cohort=cohort, cohort_label='medium envelope' if medium else 'small',
                ship_population='registered envelope ships' if medium else '500-800 TEU',
                metric='lifts_total', weighted=False, all_other_measures='secondary',
                envelope_mutual_passes=env['lifts_total']['mutual_passes'],
                envelope_mutual_passes_exact95=env['lifts_total']['mutual_passes_exact95'],
                envelope_mutual_passes_denominator=env['lifts_total']['mutual_passes_denominator'],
                medium_lift_claim_allowed=medium, medium_cohort_eligible=medium,
                medium_lift_claim_allowed_definition='cohort threshold only; medium_quotable also requires complete eligible evidence',
                medium_quotable=bool(medium and quotable),
                fallback_rule='envelope mutual passes < 8 only',
                lifts_total=total, lifts_current_port=current,
                statistically_supported=supported, quotable=quotable, reason=reason,
                current_port_reporting_complete=current_complete,
                saving_scope=('current-port and total' if current_saving else
                    'projected (D15); current-port saving not established') if supported and current_complete else None,
                relocation_policy_confound=CONFOUND)


def write_report(rows: list[dict], out_dir, *, metadata=None) -> dict:
    """Write full accounting, exact pass intervals and paired registered contrasts."""
    metadata = dict(metadata or {})
    expected, expected_ids = _expected(metadata)
    registered = metadata.get('registered_arms', metadata.get('sensitivity_arms', []))
    sensitivity = 'twostep_300' in registered or any(r.get('arm') == 'twostep_300' for r in rows)
    arms = {c: {a: {} for a in ARMS if (a not in {'instruction', 'twostep_300'} or c == 'envelope24')
                and (a != 'twostep_300' or sensitivity)} for c in COHORTS}
    hashes, ships = {}, {(m['cohort'], m['map_id']): _ship_id(m) for m in metadata.get('expected_maps', [])}
    for row in rows:
        c, a, iid = row['cohort'], row['arm'], row['map_id']
        if c not in arms or a not in arms[c]:
            raise ValueError('row outside registered cohort/arm')
        if iid in arms[c][a]:
            raise ValueError('duplicate map/arm result')
        if expected_ids is not None and (c, iid) not in expected_ids:
            raise ValueError('row outside expected map population')
        if type(row.get('status')) is not str:
            raise ValueError('missing result status')
        for key in (*METRICS, 'wall_s', 'reading_wall_s', 'child_elapsed_s', 'child_timeout_s',
                    'audit_elapsed_s', 'audit_timeout_s'):
            if row.get(key) is not None and not _number(row[key]):
                raise ValueError('invalid nonnegative finite metric: '+key)
        digest = row.get('map_sha256')
        if digest is not None:
            if (c, iid) in hashes and hashes[c, iid] != digest:
                raise ValueError('map hash differs between arms')
            hashes[c, iid] = digest
        ship = _ship_id(row)
        if ship != 'unknown':
            if ships.get((c, iid), 'unknown') not in {'unknown', ship}:
                raise ValueError('ship differs between arms or expected population')
            ships[c, iid] = ship
        arms[c][a][iid] = row
    cohorts, complete = {}, True
    for c, cells in arms.items():
        n = expected[c]
        required_ids = ({i for cohort, i in expected_ids if cohort == c} if expected_ids is not None
                        else set().union(*(set(v) for v in cells.values())))
        cohort_complete = all(len(v) == n and set(v) == required_ids for v in cells.values())
        complete = complete and cohort_complete
        # Unknown missing maps stay explicit when only cohort totals were supplied.
        all_ids = required_ids | {f'__missing_{i}' for i in range(max(0, n-len(required_ids)))}
        result = _population(cells, n, all_ids)
        result['complete'] = cohort_complete
        group_ids = {}
        for iid in sorted(all_ids):
            ship = ships.get((c, iid), 'unknown')
            group = ship if c == 'envelope24' else _ship_group(ship)
            group_ids.setdefault(group, set()).add(iid)
        result['by_ship' if c == 'envelope24' else 'ship_groups'] = {
            group: _population({a: {i: r for i, r in values.items() if i in ids}
                                for a, values in cells.items()}, len(ids), ids)
            for group, ids in sorted(group_ids.items())}
        if c != 'envelope24':
            result['pooled_ship_comparisons'] = 'diagnostic only; report ship groups apart, no pooled small claim'
        else:
            groups = {kind: set() for kind in RULE_TYPES}
            for iid in sorted(all_ids):
                kind = cells['instruction'].get(iid, {}).get('instruction_type') or 'unavailable'
                groups.setdefault(kind, set()).add(iid)
            result['instruction_types'] = {
                kind: _arm_summary({i: cells['instruction'][i] for i in ids if i in cells['instruction']}, len(ids))
                for kind, ids in sorted(groups.items())}
            result['instruction_binding'] = _instruction_binding(cells['pipeline'], cells['instruction'], n)
            result['instruction_binding']['by_type'] = {
                kind: _instruction_binding({i: r for i, r in cells['pipeline'].items() if i in ids},
                    {i: r for i, r in cells['instruction'].items() if i in ids}, len(ids))
                for kind, ids in sorted(groups.items())}
        cohorts[c] = result
    expected_rows = sum(expected[c]*len(cells) for c, cells in arms.items())
    surrogate = bool(metadata.get('fixture_surrogate') or metadata.get('stubbed')
                     or any(r.get('fixture_surrogate') for r in rows))
    mode = metadata.get('mode', 'unspecified')
    audit_complete = all(r.get('audit_verified') is True and r.get('audit_fresh_process') is True for r in rows)
    instruction_types = cohorts['envelope24']['instruction_types']
    instruction_assignment_complete = (set(instruction_types) == {
        'close_bay', 'max_tiers', 'dg_keep_away', 'one_port_per_hatch'}
        and all(v['rows'] == 6 for v in instruction_types.values()))
    population_accounted = complete
    harness_errors = any(r['status'] in {'error', 'not_run'} for r in rows)
    complete = complete and metadata.get('complete', True) is True and not harness_errors
    claim_eligible = (mode == 'sealed' and expected == COHORTS and complete and sensitivity
                      and instruction_assignment_complete and not surrogate and audit_complete)
    summary = dict(schema_version=2, mode=mode, complete=complete,
                   provisional=not claim_eligible,
                   population_accounted=population_accounted, rows=len(rows),
                   expected_rows=expected_rows, fresh_audits_complete=audit_complete,
                   instruction_assignment_complete=instruction_assignment_complete,
                   sealed_claim_eligible=claim_eligible,
                   label='sealed measured results' if claim_eligible else 'development/provisional; no sealed claims',
                   arm_labels=dict(pipeline='frozen pipeline, standard profile', twostep=TWOSTEP_LABEL,
                       instruction='same frozen pipeline with one confirmed instruction',
                       twostep_300=TWOSTEP_LABEL+'; secondary 300-second finisher sensitivity'),
                   cohorts=cohorts, metadata=metadata,
                   primary_lift_result=_lift_claim(cohorts, claim_eligible),
                   notes=['Synthetic ships and cargo, simulated port; fresh draws from the same generator, not new voyage types.',
                          'planning step from a clean load list, templated sentences; frozen standard profile.',
                          'Pass means L0 full checker pass with completion and every confirmed instruction obeyed.',
                          'Every pass count has an exact two-sided Clopper-Pearson 95% interval; unavailable trials remain in its denominator.',
                          'Out-of-envelope maps remain in the all-map denominator and are never passes.',
                          'Lift means use passing maps only; do not compare means across envelope cells or passing subsets.',
                          'Fewer extra crane lifts excludes productive loading/discharge.',
                          'Lifts include projected later-port lifts (D15), shown separately from current-port lifts.',
                          'Paired comparisons use mutual passes only; negative left-minus-right differences favour left.',
                          'Instruction comparisons reuse the no-rule pipeline result; no duplicate baseline run.',
                          'Binding obedient passes require a fresh-audited baseline breaking the rule and an instructed fresh-audited L0 pass.',
                          'Already-obeyed instruction pairs are a search-noise floor; negative instruction cost is search variation, not rule savings.',
                          'No-rule baseline counterfactual instruction rechecks never gate its pass.',
                          'Confirmation source sealed_expected_match is exact expected-rule equality, not a human planner confirmation.',
                          'Per-map wall_s records the planning child; reading_wall_s records its separate serial prepass reading once.',
                          CONFOUND,
                          'Waived weight-tolerance sensitivity (evaluation.md:85-87; 0/500/2000 kg): the labelled simple baseline is tested with finisher saturation instead.',
                          'No intake, voice, free wording, reader accuracy, live-profile, real-ship or industry-performance claim is measured here.'])
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    (out/'report.json').write_text(canonical_json(summary)+'\n')
    with (out/'per-map.csv').open('w', newline='') as stream:
        writer = csv.DictWriter(stream, fieldnames=CSV_FIELDS)
        writer.writeheader()
        for row in sorted(rows, key=lambda r: (r['cohort'], r['map_id'], r['arm'])):
            flat = {k: row.get(k) for k in CSV_FIELDS}
            flat['passed'], flat['level'] = passed(row), _level(row.get('level'))
            flat['ship_id'] = ships.get((row['cohort'], row['map_id']), 'unknown')
            if row['arm'] == 'instruction':
                base = arms[row['cohort']]['pipeline'].get(row['map_id'], {})
                flat['plan_difference_boxes'] = _plan_difference(base, row)
                if passed(base) and passed(row):
                    for metric in ('lifts_total', 'lifts_current_port'):
                        if _number(base.get(metric)) and _number(row.get(metric)):
                            flat['instruction_'+metric+'_change'] = row[metric]-base[metric]
            writer.writerow(flat)
    (out/'REPORT.md').write_text(_markdown(summary))
    return summary


def _count(k, n, interval):
    bounds = ('unavailable' if interval is None else f'{interval[0]:.4f}, {interval[1]:.4f}')
    return f'{k}/{n} (exact 95% [{bounds}])'


def _markdown(summary):
    lines = ['# Pipeline sealed report', '', summary['label'], '', *summary['notes'], '',
             'Two-step: '+TWOSTEP_LABEL+'.', '',
             '| Cohort / ship population | Arm | Rows | L0 passes (exact 95%) | Out of envelope | Relaxed levels | Mean map seconds |',
             '|---|---|---:|---|---:|---|---:|']
    populations = []
    for c, result in summary['cohorts'].items():
        # Legacy ship populations are deliberately not pooled in the printed report.
        if c == 'envelope24':
            populations.append((c, result))
        populations += [(c+' / '+ship, cell) for ship, cell in
                        result.get('by_ship', result.get('ship_groups', {})).items()]
    for name, result in populations:
        for arm, cell in result['arms'].items():
            count = _count(cell['passes_L0'], cell['expected_maps'], cell['passes_L0_exact95'])
            lines.append(f"| {name} | {arm} | {cell['rows']} | {count} | {cell['out_of_envelope']} | "
                         f"{json.dumps(cell['relaxed_by_level'], sort_keys=True)} | {cell['wall_s']['mean']} |")
    primary = summary['primary_lift_result']
    lines += ['', f"Primary: unweighted lifts_total, {primary['cohort_label']}, {primary['ship_population']}; "
              f"quotable: {primary['quotable']}. {primary['reason']}.",
              'Primary total and current-port intervals are always shown together:', '',
              f"- Total extra lifts: {primary['lifts_total']['mean_difference_left_minus_right']}; "
              f"paired 95% {primary['lifts_total']['paired_bootstrap95_mean_difference']}.",
              f"- Current-port extra lifts: {primary['lifts_current_port']['mean_difference_left_minus_right']}; "
              f"paired 95% {primary['lifts_current_port']['paired_bootstrap95_mean_difference']}.",
              f"D15 scope: {primary['saving_scope']}.", '', CONFOUND, '',
              '| Cohort / ship population | Comparison | Both pass (exact 95%) | Left only (exact 95%) | Right only (exact 95%) | Neither pass (exact 95%) |',
              '|---|---|---|---:|---:|---:|']
    for name, result in populations:
        for comparison, table in result['pass_tables'].items():
            count = _count(table['both_pass'], table['expected_maps'], table['both_pass_exact95'])
            lines.append(f"| {name} | {comparison} | {count} | "
                         f"{_count(table['left_only'], table['expected_maps'], table['left_only_exact95'])} | "
                         f"{_count(table['right_only'], table['expected_maps'], table['right_only_exact95'])} | "
                         f"{_count(table['neither_pass'], table['expected_maps'], table['neither_pass_exact95'])} |")
    lines += ['', 'Pass counts above are separate from mutual-pass lift savings below. '+CONFOUND, '',
              '| Cohort / ship population | Comparison | Metric | Mutual passes (exact 95%) | Metric n | Wins / ties / losses | Signed mean left-minus-right | Paired 95% interval | Relocation-policy confound |',
              '|---|---|---|---|---:|---|---:|---|---|']
    for name, result in populations:
        for comparison, measures in result['comparisons'].items():
            for metric, pair in measures.items():
                count = _count(pair['mutual_passes'], pair['mutual_passes_denominator'], pair['mutual_passes_exact95'])
                lines.append(f"| {name} | {comparison} | {metric} | {count} | {pair['n']} | "
                             f"{pair['wins']} / {pair['ties']} / {pair['losses']} | "
                             f"{pair['mean_difference_left_minus_right']} | {pair['paired_bootstrap95_mean_difference']} | {CONFOUND} |")
    envelope = summary['cohorts']['envelope24']
    binding = envelope['instruction_binding']
    lines += ['', '| Instruction population | Binding obedient passes / baseline rule-broken maps (exact 95%) | Already obeyed (exact 95%) | Counterfactual unavailable | Reader failures | Engine failures |',
              '|---|---|---|---:|---:|---:|']
    for name, cell in [('all types', binding), *binding['by_type'].items()]:
        failures = (envelope['arms']['instruction'] if name == 'all types' else envelope['instruction_types'][name])
        count = _count(cell['binding_obedient_passes'], cell['rule_broken_maps'], cell['binding_obedient_exact95'])
        already = _count(cell['already_obeyed'], cell['expected_maps'], cell['already_obeyed_exact95'])
        lines.append(f"| {name} | {count} | {already} | {cell['counterfactual_unavailable']} | "
                     f"{failures['reader_failures']} | {failures['engine_failures']} |")
    lines += ['', '| Instruction population | Signed instructed-minus-baseline total extra lifts (paired 95%) | Already-obeyed noise floor (paired 95%) | Mean boxes in different cells |',
              '|---|---|---|---:|']
    for name, cell in [('all types', binding), *binding['by_type'].items()]:
        cost, noise = cell['signed_lift_change']['lifts_total'], cell['already_obeyed_noise_floor']['lifts_total']
        lines.append(f"| {name} | {cost['mean_difference_left_minus_right']} {cost['paired_bootstrap95_mean_difference']} | "
                     f"{noise['mean_difference_left_minus_right']} {noise['paired_bootstrap95_mean_difference']} | {cell['plan_difference']['mean_boxes']} |")
    lines += ['', '| Instruction type | L0 passes (exact 95%) | Recheck pass (exact 95%) | Recheck fail | Unavailable |',
              '|---|---|---|---:|---:|']
    for name, cell in envelope['instruction_types'].items():
        checks = cell['instruction_recheck']
        lines.append(f"| {name} | {_count(cell['passes_L0'], cell['expected_maps'], cell['passes_L0_exact95'])} | "
                     f"{_count(checks['passed'], checks['passed_denominator'], checks['passed_exact95'])} | "
                     f"{checks['failed']} | {checks['unavailable']} |")
    lines += ['', 'Per-map status, gate, lift, recheck, plan-difference and timing values: `per-map.csv`.',
              'All passing-only means and missing metrics: `report.json`. Missing metrics are unavailable, never zero.', '']
    return '\n'.join(lines)
