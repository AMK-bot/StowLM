#!/usr/bin/env python3
"""Compare already-saved independent counts with subsequently opened reports.

Only independent local metrics helpers are imported. No run/report/scoring code
is imported. The final count and its raw-input hashes are checked before report
contents are opened. Writes are limited to comparison.json beside this script.
"""
import ast
import csv
import hashlib
import json
import math
import random
import re
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from statistics import mean

from metrics import ARMS, RULE_TYPES, changed_cells, cp

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[3]
RUN = ROOT / 'experiments/mvp/pipeline/sealed/runs/ext-1'
MEAS = ROOT / 'experiments/mvp/pipeline/sealed/measurements/ext-1'
LABEL = 'fresh held-out extension, run after the freeze; not the pre-registered sealed claim'


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def equivalent(a, b):
    if isinstance(a, bool) or isinstance(b, bool):
        return type(a) is type(b) and a == b
    if isinstance(a, (int, float)) and isinstance(b, (int, float)):
        return math.isclose(a, b, rel_tol=1e-12, abs_tol=1e-12)
    if isinstance(a, list) and isinstance(b, list):
        return len(a) == len(b) and all(equivalent(x, y) for x, y in zip(a, b))
    if isinstance(a, dict) and isinstance(b, dict):
        return a.keys() == b.keys() and all(equivalent(a[k], b[k]) for k in a)
    return a == b


def main():
    final = json.loads((HERE / 'final.json').read_text())
    assert final['phase'] == 'FINAL', 'Save the independent FINAL before comparing reports'
    for name, digest in final['input_sha256'].items():
        assert sha(RUN / name) == digest, 'Raw input changed after independent count: ' + name
    independent = final['metrics']
    rows = json.loads((RUN / 'rows.json').read_text())
    lookup = {(r['map_id'], r['arm']): r for r in rows}
    verdicts = {(r['map_id'], r['arm']): r['passed'] for r in independent['per_trial']}
    report = json.loads((RUN / 'report.json').read_text())
    report_md = (RUN / 'REPORT.md').read_text()
    run_snapshot_hashes = {name: sha(RUN / name) for name in ['REPORT.md', 'report.json', 'per-map.csv', 'rows.json']}
    checks, differences, intervals = [], [], []

    def check(path, expected, observed, category='figures'):
        item = {'path': path, 'ok': equivalent(expected, observed), 'category': category}
        if not item['ok']:
            item.update(expected=expected, observed=observed)
        checks.append(item)

    def interval(path, k, n, observed):
        expected = cp(k, n)
        check(path, expected, observed, 'exact_intervals')
        intervals.append({'path': path, 'k': k, 'n': n,
                          'max_absolute_error': max(abs(a-b) for a, b in zip(expected, observed))
                          if expected is not None and isinstance(observed, list) else 0.0})

    def selected(arm=None, ship=None, kind=None):
        return [r for r in rows if (arm is None or r['arm'] == arm)
                and (ship is None or r['ship_id'] == ship)
                and (kind is None or r.get('instruction_type') == kind)]

    def group(path, saved, own, rs):
        for name, field in [('passes_L0', 'passes'), ('expected_maps', 'trials'),
                            ('rows', 'trials'), ('statuses', 'statuses'),
                            ('relaxed_by_level', 'relaxed_levels'),
                            ('out_of_envelope', 'out_of_envelope'),
                            ('reader_failures', 'reader_failures')]:
            check(path + '/' + name, saved[field], own.get(name))
        interval(path + '/passes_L0_exact95', saved['passes'], saved['trials'], own['passes_L0_exact95'])
        check(path + '/pass_fraction', saved['passes']/saved['trials'], own.get('pass_fraction'))
        check(path + '/missing_rows', 0, own.get('missing_rows'))
        for out, raw in [('audit_cleanup_failures', 'audit_cleanup_failed'),
                         ('audit_timeouts', 'audit_timed_out'),
                         ('child_cleanup_failures', 'child_cleanup_failed'),
                         ('child_timeouts', 'child_timed_out')]:
            check(path + '/' + out, sum(r.get(raw) is True for r in rs), own.get(out))
        broader = sum(r.get('engine_failure') is True or
                      (r.get('status') == 'deadline_exceeded' and not r.get('deadline_with_incumbent'))
                      for r in rs)
        check(path + '/engine_failures_broader_definition', broader, own.get('engine_failures'))
        if saved['engine_failures'] != own.get('engine_failures'):
            differences.append({'path': path + '/engine_failures', 'severity': 'definition difference',
                                'independent_raw_flag': saved['engine_failures'],
                                'report_broader_count': own.get('engine_failures'),
                                'explanation': 'Report also includes clean deadlines with no incumbent; these remain nonpasses.'})
        for field in ['instruction_recheck', 'counterfactual_instruction_recheck']:
            counts = Counter('passed' if r.get(field) is True else 'failed' if r.get(field) is False
                             else 'unavailable' for r in rs)
            for key in ['passed', 'failed', 'unavailable']:
                check(path + '/' + field + '/' + key, counts[key], own[field][key])
        passing = [r for r in rs if verdicts[(r['map_id'], r['arm'])]]
        for lift in ['lifts_current_port', 'lifts_projected_later_port', 'lifts_total', 'weighted_lifts']:
            values = [r[lift] for r in passing if r.get(lift) is not None]
            cell = own['lift_means_over_passes'][lift]
            check(path + '/lift_means_over_passes/' + lift + '/n', len(values), cell['n'])
            check(path + '/lift_means_over_passes/' + lift + '/mean', mean(values) if values else None, cell['mean'])

    def binding(path, saved, own):
        for name, value in [('binding_obedient_passes', saved['binding']['passes']),
                            ('rule_broken_maps', saved['binding']['trials']),
                            ('already_obeyed', saved['already_obeyed']),
                            ('counterfactual_unavailable', saved['counterfactual_unavailable']),
                            ('expected_maps', saved['trials'])]:
            check(path + '/' + name, value, own.get(name))
        interval(path + '/binding_obedient_exact95', saved['binding']['passes'],
                 saved['binding']['trials'], own['binding_obedient_exact95'])
        interval(path + '/already_obeyed_exact95', saved['already_obeyed'], saved['trials'], own['already_obeyed_exact95'])
        for name, category in [('rule_broken_map_ids', 'binding'), ('already_obeyed_map_ids', 'already_obeyed')]:
            check(path + '/' + name, sorted(r['map_id'] for r in saved['per_map'] if r['counterfactual'] == category), sorted(own[name]))
        check(path + '/binding_obedient_map_ids', sorted(r['map_id'] for r in saved['per_map']
              if r['counterfactual'] == 'binding' and r['instructed_pass']), sorted(own['binding_obedient_map_ids']))
        check(path + '/plan_difference/n', saved['changed_cells']['n'], own['plan_difference']['n'])
        check(path + '/plan_difference/mean_boxes', saved['changed_cells']['mean'], own['plan_difference']['mean_boxes'])

    for arm in ARMS:
        saved = independent['arms'][arm]
        group('/arms/' + arm, saved['all'], report['arms'][arm], selected(arm))
        for ship, cell in saved['by_ship'].items():
            group('/by_ship/' + ship + '/arms/' + arm, cell, report['by_ship'][ship]['arms'][arm], selected(arm, ship))
    binding('/instruction', independent['instruction']['all'], report['instruction'])
    for kind in RULE_TYPES:
        saved = independent['instruction']['by_type'][kind]
        group('/instruction_types/' + kind, saved, report['instruction_types'][kind], selected('instruction', kind=kind))
        binding('/instruction_by_type/' + kind, saved, report['instruction_by_type'][kind])
        check('/instruction/by_type/' + kind, report['instruction_by_type'][kind], report['instruction']['by_type'][kind])

    # Independent code11 paired bootstrap: 10,000 Random(0).choices draws,
    # sorted nearest-rank indices 249/9749. Pair order is sorted map ID.
    # No row is eligible unless both independent verdicts are complete L0.
    bootstrap_cells = {}
    for kind in ['all'] + list(RULE_TYPES):
        scope = selected('instruction', kind=None if kind == 'all' else kind)
        already_ids = {r['map_id'] for r in scope
                       if lookup[(r['map_id'], 'pipeline')].get('counterfactual_instruction_recheck') is True}
        own_scope = report['instruction'] if kind == 'all' else report['instruction_by_type'][kind]
        for population in ['signed_lift_change', 'already_obeyed_noise_floor']:
            population_rows = [r for r in scope if population == 'signed_lift_change' or r['map_id'] in already_ids]
            mutual = [r for r in sorted(population_rows, key=lambda r: r['map_id'])
                      if verdicts[(r['map_id'], 'instruction')] and verdicts[(r['map_id'], 'pipeline')]]
            for metric in ['lifts_total', 'lifts_current_port', 'lifts_projected_later_port', 'weighted_lifts']:
                valid = [r for r in mutual if r.get(metric) is not None and
                         lookup[(r['map_id'], 'pipeline')].get(metric) is not None]
                pairs = [(r[metric], lookup[(r['map_id'], 'pipeline')][metric]) for r in valid]
                n = len(pairs)
                differences_of_means, savings = [], []
                if n:
                    rng = random.Random(0)
                    for _ in range(10000):
                        draw = rng.choices(pairs, k=n)
                        left, right = sum(p[0] for p in draw), sum(p[1] for p in draw)
                        differences_of_means.append((left-right)/n)
                        if right:
                            savings.append(1-left/right)
                    differences_of_means.sort()
                    savings.sort()
                ci = [differences_of_means[i] for i in (249, 9749)] if n else None
                # If a zero-denominator resample occurs, use nearest ranks of
                # retained savings draws; report how many retained explicitly.
                saving_ci = [savings[max(0, math.ceil(q*len(savings))-1)] for q in (.025, .975)] if savings else None
                left_mean = mean(p[0] for p in pairs) if n else None
                right_mean = mean(p[1] for p in pairs) if n else None
                expected = {'available': bool(n), 'metric': metric, 'n': n,
                    'map_ids': [r['map_id'] for r in valid],
                    'missing_metric_map_ids': [r['map_id'] for r in mutual if r not in valid],
                    'mutual_passes': len(mutual), 'mutual_passes_denominator': len(population_rows),
                    'mean_left': left_mean, 'mean_right': right_mean,
                    'mean_difference_left_minus_right': mean(a-b for a,b in pairs) if n else None,
                    'wins': sum(a < b for a,b in pairs), 'ties': sum(a == b for a,b in pairs),
                    'losses': sum(a > b for a,b in pairs),
                    'paired_bootstrap95_mean_difference': ci,
                    'saving_fraction': 1-left_mean/right_mean if n and right_mean else None,
                    'paired_bootstrap95_saving_fraction': saving_ci,
                    'saving_interval_resamples': len(savings), 'bootstrap_resamples': 10000,
                    'bootstrap_seed': 0, 'interval': 'paired percentile 95%; nearest rank'}
                own = own_scope[population][metric]
                path = '/bootstrap/' + kind + '/' + population + '/' + metric
                for field, value in expected.items():
                    check(path + '/' + field, value, own.get(field), 'paired_bootstrap')
                interval(path + '/mutual_passes_exact95', len(mutual), len(population_rows), own['mutual_passes_exact95'])
                bootstrap_cells[(kind, population, metric)] = expected
    check('/cohorts/envelope24/arms', report['arms'], report['cohorts']['envelope24']['arms'])
    check('/cohorts/envelope24/by_ship', report['by_ship'], report['cohorts']['envelope24']['by_ship'])
    check('/cohorts/envelope24/instruction_binding', report['instruction'], report['cohorts']['envelope24']['instruction_binding'])
    check('/cohorts/envelope24/instruction_types', report['instruction_types'], report['cohorts']['envelope24']['instruction_types'])
    for key, value in [('rows', 72), ('expected_rows', 72), ('terminal_rows', 72),
                       ('expected_maps', 24), ('error_rows', 0), ('pending_rows', 0),
                       ('complete', True), ('provisional', False), ('verified_complete', True),
                       ('population_accounted', True), ('fresh_audits_complete', True),
                       ('label', LABEL), ('sealed_claim_eligible', False),
                       ('lift_saving_quotable', False), ('wall_times_quotable', False)]:
        check('/' + key, value, report.get(key), 'preregistration')
    check('/fresh_audited_rows', sum(r.get('audit_verified') is True and r.get('audit_fresh_process') is True for r in rows), report['fresh_audited_rows'])

    # Recompute every embedded exact interval, including duplicated cohort cells.
    def walk_intervals(obj, path=''):
        if isinstance(obj, dict):
            for key, val in obj.items():
                if key.endswith('_exact95'):
                    stem = key[:-8]
                    if stem == 'passes_L0': k, n = obj[stem], obj['expected_maps']
                    elif stem == 'passed': k, n = obj[stem], obj['passed_denominator']
                    elif stem == 'binding_obedient': k, n = obj['binding_obedient_passes'], obj['rule_broken_maps']
                    elif stem + '_denominator' in obj: k, n = obj[stem], obj[stem + '_denominator']
                    else: k, n = obj[stem], obj['expected_maps']
                    interval('/report.json' + path + '/' + key, k, n, val)
                walk_intervals(val, path + '/' + key)
        elif isinstance(obj, list):
            for index, val in enumerate(obj): walk_intervals(val, path + '/' + str(index))
    walk_intervals(report)

    # REPORT.md population tables: parse actual cells, rather than searching tokens.
    for line in report_md.splitlines():
        cells = [s.strip() for s in line.strip('|').split('|')]
        if len(cells) == 6 and cells[0] in ['all24', 'feeder_500', 'feeder_800'] and cells[1] in ARMS:
            pop, arm = cells[:2]
            saved = independent['arms'][arm]['all' if pop == 'all24' else 'by_ship']
            if pop != 'all24': saved = saved[pop]
            match = re.fullmatch(r'(\d+)/(\d+); exact95% \[([^,]+), ([^\]]+)\]', cells[2])
            assert match, cells
            k, n, lo, hi = match.groups()
            path = '/REPORT.md/' + pop + '/' + arm
            check(path + '/count', [saved['passes'], saved['trials']], [int(k), int(n)], 'markdown')
            interval(path + '/interval', int(k), int(n), [float(lo), float(hi)])
            check(path + '/outside', saved['out_of_envelope'], int(cells[3]), 'markdown')
            check(path + '/relaxed_levels', saved['relaxed_levels'], ast.literal_eval(cells[4]), 'markdown')
            check(path + '/statuses', saved['statuses'], ast.literal_eval(cells[5]), 'markdown')
        if len(cells) == 4 and cells[0] in ['all'] + list(RULE_TYPES):
            kind = cells[0]
            if (kind, 'signed_lift_change', 'lifts_total') in bootstrap_cells:
                try:
                    float(cells[1])
                except ValueError:
                    continue
                for column, population in [(2, 'signed_lift_change'), (3, 'already_obeyed_noise_floor')]:
                    expected = bootstrap_cells[(kind, population, 'lifts_total')]
                    first, tail = cells[column].split(' ', 1)
                    actual_mean = None if first == 'None' else float(first)
                    actual_ci = None if tail == 'None' else ast.literal_eval(tail)
                    path = '/REPORT.md/paired_lifts/' + kind + '/' + population
                    check(path + '/mean', expected['mean_difference_left_minus_right'], actual_mean, 'markdown')
                    check(path + '/interval', expected['paired_bootstrap95_mean_difference'], actual_ci, 'markdown')
    for index, match in enumerate(re.finditer(r'(\d+)/(\d+); exact95% \[([^,]+), ([^\]]+)\]', report_md)):
        k, n, lo, hi = match.groups()
        interval('/REPORT.md/printed_intervals/' + str(index), int(k), int(n), [float(lo), float(hi)])
    check('/REPORT.md/label', True, LABEL in report_md, 'preregistration')
    check('/REPORT.md/clean_load_list', True, 'clean load list' in report_md, 'preregistration')
    if any('baseline L0 is not required' in note for note in report.get('notes', [])):
        differences.append({'path': '/notes/binding', 'severity': 'P2', 'category': 'definition wording difference',
            'explanation': 'Report says baseline L0 is not required; code11 recount uses a fresh-audited passing baseline. All 24 ext-1 baselines pass, so counts and eligible map IDs agree.'})

    # Verify all exported CSV fields against raw rows or independent derivations.
    with (RUN / 'per-map.csv').open() as stream:
        exported = list(csv.DictReader(stream))
    check('/per-map.csv/rows', 72, len(exported), 'csv')
    check('/per-map.csv/keys', sorted(lookup), sorted((r['map_id'], r['arm']) for r in exported), 'csv')
    for out in exported:
        key = out['map_id'], out['arm']
        raw = lookup[key]
        base = lookup[(key[0], 'pipeline')]
        for field, value in out.items():
            expected = raw.get(field)
            if field == 'level': expected = None if raw.get('level') is None else 'L' + str(raw['level'])
            elif field == 'passed': expected = verdicts[key]
            elif field == 'plan_difference_boxes': expected = changed_cells(base, raw) if key[1] == 'instruction' else None
            elif field in ['instruction_lifts_total_change', 'instruction_lifts_current_port_change']:
                lift = field.removeprefix('instruction_').removesuffix('_change')
                expected = (raw[lift] - base[lift]) if key[1] == 'instruction' and verdicts[key] and verdicts[(key[0], 'pipeline')] else None
            if isinstance(expected, (list, dict)): expected = json.dumps(expected, sort_keys=True)
            expected = '' if expected is None else str(expected)
            check('/per-map.csv/' + key[0] + '/' + key[1] + '/' + field, expected, value, 'csv')

    optional = {name: (MEAS / name).is_file() for name in
                ['REPORT.md', 'FIGURES.json', 'SECONDARY-POOLED.json', 'POOL-VERIFICATION.json']}
    measurement_input_hashes = {}
    if optional['FIGURES.json']:
        figures = json.loads((MEAS / 'FIGURES.json').read_text())
        measurement_input_hashes['FIGURES.json'] = sha(MEAS / 'FIGURES.json')
        primary = figures['primary']
        def figure_ratio(path, own, expected):
            check(path + '/numerator', expected['passes'], own['numerator']['value'], 'measurement')
            check(path + '/denominator', expected['trials'], own['denominator']['value'], 'measurement')
            interval(path + '/exact95', expected['passes'], expected['trials'], own['exact95']['value'])
            check(path + '/label', True, LABEL in own['label'], 'measurement')
        for arm in ARMS:
            figure_ratio('/FIGURES.json/primary/arms/' + arm, primary['arms'][arm], independent['arms'][arm]['all'])
            for ship in ['feeder_500', 'feeder_800']:
                figure_ratio('/FIGURES.json/primary/by_ship/' + ship + '/' + arm,
                             primary['by_ship'][ship][arm], independent['arms'][arm]['by_ship'][ship])
        figure_ratio('/FIGURES.json/primary/binding', primary['binding'], independent['instruction']['all']['binding'])
        for kind in RULE_TYPES:
            own, saved = primary['instruction_by_type'][kind], independent['instruction']['by_type'][kind]
            figure_ratio('/FIGURES.json/primary/instruction_by_type/' + kind + '/binding', own['binding'], saved['binding'])
            figure_ratio('/FIGURES.json/primary/instruction_by_type/' + kind + '/instructed_L0', own['instructed_L0'], saved)
            check('/FIGURES.json/primary/instruction_by_type/' + kind + '/already_obeyed', saved['already_obeyed'], own['already_obeyed']['value'], 'measurement')
        for key in ['already_obeyed', 'counterfactual_unavailable']:
            check('/FIGURES.json/primary/' + key, independent['instruction']['all'][key], primary[key]['value'], 'measurement')
        check('/FIGURES.json/primary/label', LABEL, primary['label']['value'], 'measurement')
        for flag in ['lift_saving_quotable', 'wall_times_quotable']:
            check('/FIGURES.json/' + flag, False, figures[flag], 'preregistration')
        # All extension source pointers and recorded hashes, without opening old run payloads.
        def sources(obj, path=''):
            if isinstance(obj, dict):
                if 'source' in obj and 'value' in obj and obj['source'].get('path') == str(RUN / 'report.json'):
                    source = obj['source']
                    target = report
                    for token in source['json_pointer'].split('/')[1:]: target = target[token.replace('~1', '/').replace('~0', '~')]
                    check('/FIGURES.json' + path + '/source_value', target, obj['value'], 'source_pointers')
                    check('/FIGURES.json' + path + '/source_hash', sha(RUN / 'report.json'), source['sha256'], 'source_pointers')
                for k, v in obj.items(): sources(v, path + '/' + k)
            elif isinstance(obj, list):
                for i, v in enumerate(obj): sources(v, path + '/' + str(i))
        sources(figures)
        for name in ['ext1_rows', 'ext1_report']:
            source = figures['inputs'][name]
            check('/FIGURES.json/inputs/' + name, sha(Path(source['path'])), source['sha256'], 'source_pointers')
        pool = figures['secondary_pooled']
        check('/FIGURES.json/secondary_pooled/label', True, 'SECONDARY' in pool['label'], 'preregistration')
        if not pool['available']:
            differences.append({'path': '/FIGURES.json/secondary_pooled', 'severity': 'optional unavailable', 'explanation': pool['unavailable_reason']})
        else:
            # Original component counts are the independently reviewed code11
            # envelope cells, not a new reading/recount of prior run payloads.
            old_counts = {'pipeline': (24, 24), 'twostep': (0, 24), 'instruction': (18, 24)}
            code11 = ROOT / 'experiments/review/v6/code11.md'
            measurement_input_hashes['original_component_review:code11.md'] = sha(code11)
            for name, own in list(pool['arms'].items()) + [('binding', pool['binding'])]:
                saved = independent['instruction']['all']['binding'] if name == 'binding' else independent['arms'][name]['all']
                original = (13, 18) if name == 'binding' else old_counts[name]
                path = '/FIGURES.json/secondary_pooled/' + name
                components = own['components']
                check(path + '/components_count', 2, len(components), 'pooling')
                check(path + '/original_component', list(original),
                      [components[0]['numerator']['value'], components[0]['denominator']['value']], 'pooling')
                check(path + '/extension_component', [saved['passes'], saved['trials']],
                      [components[1]['numerator']['value'], components[1]['denominator']['value']], 'pooling')
                k, n = original[0] + saved['passes'], original[1] + saved['trials']
                check(path + '/numerator', k, own['numerator']['value'], 'pooling')
                check(path + '/denominator', n, own['denominator']['value'], 'pooling')
                interval(path + '/exact95', k, n, own['exact95']['value'])
                check(path + '/label', True, 'SECONDARY' in own['label'], 'pooling')
            for flag in ['lift_saving_quotable', 'wall_times_quotable', 'sealed_claim_eligible']:
                check('/FIGURES.json/secondary_pooled/' + flag, False, pool[flag], 'preregistration')
            for epoch, digest in [('ext1', 'acf2f275efa73f45c43709f4a52807dd480390e1497f085cab11d3604265bbec'),
                                  ('final3', 'a11b42021a33f939739b6ea93d9517cd59209476b1dec840700503a10b14fdbd')]:
                check('/FIGURES.json/secondary_pooled/manifest_epochs/' + epoch, digest,
                      pool['manifest_epochs'][epoch]['value'], 'pooling')
            check('/SECONDARY-POOLED.json/present', True, optional['SECONDARY-POOLED.json'], 'pooling')
            if optional['SECONDARY-POOLED.json']:
                pooled_path = MEAS / 'SECONDARY-POOLED.json'
                measurement_input_hashes['SECONDARY-POOLED.json'] = sha(pooled_path)
                check('/SECONDARY-POOLED.json/identity', pool, json.loads(pooled_path.read_text()), 'pooling')
            verification = pool.get('verification') or {}
            verification_path = MEAS / 'POOL-VERIFICATION.json'
            check('/POOL-VERIFICATION.json/present', True, optional['POOL-VERIFICATION.json'], 'pooling')
            check('/POOL-VERIFICATION.json/source_path', str(verification_path),
                  str(ROOT / verification.get('path', 'missing')), 'pooling')
            if optional['POOL-VERIFICATION.json']:
                measurement_input_hashes['POOL-VERIFICATION.json'] = sha(verification_path)
                check('/POOL-VERIFICATION.json/source_hash', sha(verification_path), verification.get('sha256'), 'pooling')
                receipt = json.loads(verification_path.read_text())
                check('/POOL-VERIFICATION.json/verified', True, receipt.get('verified'), 'pooling')
                check('/POOL-VERIFICATION.json/discrepancies', [], receipt.get('discrepancies'), 'pooling')
                for epoch in ['ext1', 'final3']:
                    check('/POOL-VERIFICATION.json/reports/' + epoch + '/verified', True,
                          receipt['reports'][epoch].get('verified'), 'pooling')
                for name, field in [('report.json', 'report_sha256'), ('rows.json', 'rows_sha256')]:
                    check('/POOL-VERIFICATION.json/reports/ext1/' + field, sha(RUN / name),
                          receipt['reports']['ext1'][field], 'pooling')
    if optional['REPORT.md']:
        text = (MEAS / 'REPORT.md').read_text()
        measurement_input_hashes['REPORT.md'] = sha(MEAS / 'REPORT.md')
        check('/measurement/REPORT.md/label', True, LABEL in text, 'measurement')
        for index, match in enumerate(re.finditer(r'(\d+)/(\d+) \(exact95% ([\d.]+)–([\d.]+)%\)', text)):
            k, n, lo, hi = match.groups()
            expected = cp(int(k), int(n))
            check('/measurement/REPORT.md/printed_intervals/' + str(index),
                  [f'{x*100:.1f}' for x in expected], [lo, hi], 'rounded_exact_intervals')
        # Extension Markdown counts are checked independently of the figure JSON.
        ext_text = text.split('Separate original 3.1:')[0]
        for arm, term in [('pipeline', 'pipeline'), ('twostep', 'simple two-step'), ('instruction', 'instructed L0')]:
            saved = independent['arms'][arm]['all']
            check('/measurement/REPORT.md/primary/' + arm, True,
                  f'{term} {saved["passes"]}/{saved["trials"]}' in ext_text, 'measurement')
        ext_section = text.split('## ' + LABEL)[1].split('\n## ')[0]
        for ship in ['feeder_500', 'feeder_800']:
            for arm in ARMS:
                saved = independent['arms'][arm]['by_ship'][ship]
                check('/measurement/REPORT.md/by_ship/' + ship + '/' + arm, True,
                      f'| {ship} | {arm} | {saved["passes"]}/{saved["trials"]} ' in ext_section, 'measurement')
        for kind in RULE_TYPES:
            saved = independent['instruction']['by_type'][kind]
            prefix = f'| {kind} | {saved["binding"]["passes"]}/{saved["binding"]["trials"]} '
            check('/measurement/REPORT.md/by_type/' + kind, True, prefix in ext_section, 'measurement')

    for name, digest in run_snapshot_hashes.items():
        check('/snapshot/run/' + name, digest, sha(RUN / name), 'snapshot')
    for name, digest in measurement_input_hashes.items():
        path = ROOT / 'experiments/review/v6/code11.md' if name.startswith('original_component_review:') else MEAS / name
        check('/snapshot/measurement/' + name, digest, sha(path), 'snapshot')
    bad = [x for x in checks if not x['ok']]
    result = {'compared_utc': datetime.now(timezone.utc).isoformat(),
              'independent_final_sha256': sha(HERE / 'final.json'),
              'run_input_sha256': run_snapshot_hashes,
              'measurement_input_sha256': measurement_input_hashes, 'optional_measurement_files': optional,
              'matched_checks': len(checks) - len(bad), 'total_checks': len(checks),
              'checks_by_category': dict(Counter(x['category'] for x in checks)),
              'exact_interval_checks': len(intervals),
              'maximum_interval_absolute_error': max(x['max_absolute_error'] for x in intervals),
              'mismatches': bad, 'definition_differences_and_optional_items': differences,
              'all_required_figures_agree': not bad, 'P1': bad,
              'P2': [item for item in differences if item['severity'] == 'P2'],
              'limits': ['Original 3.1 rows and figures were not independently recounted here; measurement Markdown intervals are mathematically rechecked for both displayed cohorts.',
                         'Signed lift and savings bootstrap diagnostics use independent code11 resampling on mutual L0 pairs, with current/later port and already-obeyed populations checked separately. These are secondary diagnostics, not a lift-saving claim.',
                         'Allocation sort order and absence of unlogged sealed accesses retain the limits recorded in final.json.'],
              'checks': checks}
    output = HERE / 'comparison.json'
    output.write_text(json.dumps(result, indent=2, sort_keys=True) + '\n')
    print(json.dumps({k: result[k] for k in ['matched_checks', 'total_checks', 'checks_by_category', 'exact_interval_checks', 'maximum_interval_absolute_error', 'mismatches', 'definition_differences_and_optional_items', 'all_required_figures_agree']}, indent=2))
    return 1 if bad else 0


if __name__ == '__main__':
    raise SystemExit(main())
