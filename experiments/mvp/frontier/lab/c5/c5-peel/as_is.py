"""Offline classification only. The runtime ladder must never import this module.

All historical full-checker reports are read through the content-addressed
archive manifest, SHA-256 verified, and classified without rerunning solving.
The fixed 35 failed maps are selected by REPORT-FIXED's pre-existing portfolio
status; no archive report is substituted for a new cold measurement.
"""
from __future__ import annotations

from collections import Counter, defaultdict
import hashlib
import json
from pathlib import Path

from tiers import HERE, ROOT, TABLE_PATH, analyze_report, load_table, rank_analysis

SOURCE = ROOT / 'experiments/mvp/frontier/lab/c4/c2-layout'


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def compact(analysis):
    analysis = dict(analysis)
    analysis['broken_rules'] = [dict((key, value) for key, value in rule.items()
                                  if key != 'occurrences') | dict(
        occurrence_count=len(rule['occurrences']),
        units=sorted({v['unit'] for v in rule['occurrences'] if v.get('unit')}),
        maximum_measured_breach=max((v['breach'] for v in rule['occurrences']
                                     if v['breach'] is not None), default=None))
        for rule in analysis['broken_rules']]
    return analysis


def build():
    manifest_path = SOURCE / 'DIAGNOSTIC-ARCHIVE.json'
    fixed_path = SOURCE / 'REPORT-FIXED.json'
    manifest = json.loads(manifest_path.read_text())
    fixed = json.loads(fixed_path.read_text())
    table = load_table()
    by_original = defaultdict(list)
    raw_cache = {}
    for row in manifest:
        by_original[row['original']].append(row)
        if row['sha256'] not in raw_cache:
            path = ROOT / row['archived']
            if digest(path) != row['sha256']:
                raise ValueError(f'Archive digest mismatch: {path}')
            raw_cache[row['sha256']] = json.loads(path.read_text())
    reports = {}
    by_map = defaultdict(list)
    for row in manifest:
        if not row['original'].endswith('.report.json'):
            continue
        key = row['sha256']
        if key not in reports:
            report = raw_cache[key]
            reports[key] = dict(report_path=row['archived'], report_sha256=key,
                                original_report_paths=[], candidate_plan_paths=[],
                                **compact(analyze_report(report, table)))
        entry = reports[key]
        entry['original_report_paths'].append(row['original'])
        original_plan = row['original'].removesuffix('.report.json')+'.plan.json'
        entry['candidate_plan_paths'].extend(plan['archived'] for plan in by_original.get(original_plan, []))
        instance_id = Path(row['original']).name.removesuffix('.report.json').rsplit('-', 1)[0]
        if key not in by_map[instance_id]:
            by_map[instance_id].append(key)
    for entry in reports.values():
        entry['original_report_paths'] = sorted(set(entry['original_report_paths']))
        entry['candidate_plan_paths'] = sorted(set(entry['candidate_plan_paths']))
    maps = []
    for row in fixed['maps']:
        if row['best_passing']['passed']:
            continue
        entries = [reports[key] for key in by_map[row['instance_id']]]
        failed = [entry for entry in entries if not entry['passed']]
        eligible = [entry for entry in failed if entry['level'] is not None]
        best = min(eligible, key=lambda entry: (entry['level'], rank_analysis(entry), entry['report_sha256']), default=None)
        diagnostic_best = best or min(failed, key=lambda entry: (rank_analysis(entry), entry['report_sha256']), default=None)
        maps.append(dict(instance_id=row['instance_id'], rung=row['rung'],
                         input=row['input'], historical_pass=False,
                         failed_reports=len(failed),
                         report_sha256s=sorted(entry['report_sha256'] for entry in failed),
                         reached_level=best['level'] if best else None,
                         best_report_sha256=diagnostic_best['report_sha256'] if diagnostic_best else None,
                         ineligible_reasons=diagnostic_best['ineligible_reasons'] if diagnostic_best else ['No saved report'],
                         broken_rule_ids=diagnostic_best['broken_rule_ids'] if diagnostic_best else [],
                         broken_by_tier=diagnostic_best['broken_by_tier'] if diagnostic_best else {},
                         total_breach=diagnostic_best['total_breach'] if diagnostic_best else None,
                         lifts=diagnostic_best['lifts'] if diagnostic_best else None))
    levels = dict(Counter(f'L{row["reached_level"]}' if row['reached_level'] is not None else 'T0/unknown' for row in maps))
    by_rung = {rung: dict(Counter(f'L{row["reached_level"]}' if row['reached_level'] is not None else 'T0/unknown'
                                 for row in maps if row['rung'] == rung))
               for rung in ('f60', 'f70', 'f80', 'medium12')}
    return dict(scope='Historical saved reports only; no solving or new pass claim; not a cold baseline.',
                selection='All maps with best_passing.passed false in c2-layout/REPORT-FIXED.json.',
                within_map_choice='Lowest eligible level, then fewest hard rule IDs, summed measured breach, lifts.',
                breach_units='Raw original-unit absolute measured/limit gaps summed over hard occurrences; no conversion.',
                occurrence_evidence='Full measured/limit and location data remain in each SHA-256-verified report_path.',
                plan_binding='candidate_plan_paths match original diagnostic stem; duplicates can be ambiguous after historical overwrites.',
                sources=[dict(path=str(path.relative_to(ROOT)), sha256=digest(path))
                         for path in (TABLE_PATH, manifest_path, fixed_path)],
                archive_counts=dict(manifest_entries=len(manifest), unique_artifacts=len(raw_cache),
                                    unique_reports=len(reports),
                                    failed_reports=sum(not entry['passed'] for entry in reports.values()),
                                    passing_reports=sum(entry['passed'] for entry in reports.values())),
                failing_maps=len(maps), levels=levels, by_rung=by_rung,
                maps=maps, reports=reports)


def markdown(data):
    lines = ['# Saved stows: as-is tier classification', '', data['scope'], '',
             'The fixed historical portfolio has 35 failed maps: '+', '.join(f'{k}: {v}' for k, v in sorted(data['levels'].items()))+'.', '',
             '| Rung | Failed maps | L4 | L5 | T0 / unknown |',
             '| --- | ---: | ---: | ---: | ---: |']
    for rung, counts in data['by_rung'].items():
        lines.append(f'| {rung} | {sum(counts.values())} | {counts.get("L4",0)} | {counts.get("L5",0)} | {counts.get("T0/unknown",0)} |')
    lines += ['', '| Map | Level reached | Hard rules broken in selected saved report |', '| --- | --- | --- |']
    for row in data['maps']:
        lines.append(f'| {row["instance_id"]} | '+(f'L{row["reached_level"]}' if row['reached_level'] is not None else 'T0 / unknown')+' | '+', '.join(row['broken_rule_ids'])+' |')
    lines += ['', 'Every saved full-checker report is included in AS_IS.json, including failures on maps that have another historical L0 witness. The per-map choice first minimises level, then the fixed within-level ranking. This is a retrospective witness table and reads warm historical material; runtime solving reads none of it.', '',
              'Breach is the sum of absolute measured-minus-limit gaps over hard occurrences, in original units. No missing measured/limit pair becomes zero: its breach remains null and ranks after measured gaps. Each report has its exact content-addressed path and SHA-256, preserving all measured/limit values and locations without duplicating the 389 MB archive. Candidate plan paths are stem associations and can be ambiguous when historical paths were overwritten.', '',
              'Only hard violations determine level. Soft and tolerated violations remain diagnostics, consistent with the unchanged checker definition of L0. T0, missing replay count, incomplete checker parts or inconsistent checker verdict make a report ineligible at every level.', '']
    return '\n'.join(lines)


if __name__ == '__main__':
    data = build()
    (HERE / 'AS_IS.json').write_text(json.dumps(data, indent=2, sort_keys=True)+'\n')
    (HERE / 'AS_IS.md').write_text(markdown(data))
    print(json.dumps({key: data[key] for key in ('archive_counts', 'failing_maps', 'levels', 'by_rung')}, sort_keys=True))
