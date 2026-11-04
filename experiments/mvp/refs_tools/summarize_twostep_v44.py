#!/usr/bin/env python3
"""Render current 4.4 references; keep all earlier two-step evidence archived."""
from __future__ import annotations
import argparse
from collections import Counter
from copy import deepcopy
from datetime import datetime, timezone
import importlib.util
import itertools
import json
from pathlib import Path
from eval.harness_metrics import aggregate_results, checker_outcome, paired_comparison


def module(name, filename):
    spec = importlib.util.spec_from_file_location(name, Path(__file__).with_name(filename))
    value = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(value)
    return value


small = module('twostep_v44_small', 'summarize_small_twostep_v21.py')
h, v21, ROOT = small.h, small.v21, small.ROOT
SMALL = 'test-twostep-small-v21-v44'
TINY = 'test-twostep-tiny-v44'
VERSION = '4.4.0'


def validate_current(rows, expected_hashes):
    if len(rows) != len(expected_hashes) or {r['input_sha256'] for r in rows} != set(expected_hashes):
        raise ValueError('Current two-step references must cover every frozen input exactly once')
    hashes = set()
    for row in rows:
        if not row.get('completed'):
            raise ValueError('Incomplete current two-step attempt')
        if (row.get('result') or {}).get('planner_version') != VERSION:
            raise ValueError('Current two-step planner version is not 4.4.0')
        source = (row.get('source_before') or {}).get('src/stowage/solver/twostep.py')
        if not source or len(source) != 64:
            raise ValueError('Missing two-step source hash')
        hashes.add(source)
    if len(hashes) != 1:
        raise ValueError('Mixed two-step source hashes')
    return hashes.pop()


def changes(before, after):
    old = {r['input_sha256']: r for r in before}
    new = {r['input_sha256']: r for r in after}
    if old.keys() != new.keys():
        raise ValueError('Version comparison requires identical frozen inputs')
    rows = []
    for sha, row in sorted(new.items()):
        prior = old[sha]
        def signature(r):
            return dict(checker=checker_outcome(r), shippable=r.get('shippable'),
                        first_rule=h.first_failure(r), counts=h.counts(r),
                        planner_status=(r.get('result') or {}).get('status'))
        a, b = signature(prior), signature(row)
        rows.append(dict(input_sha256=sha, instance_id=row['instance_id'], before=a, after=b,
                         changed_fields=[k for k in a if a[k] != b[k]],
                         before_version=(prior.get('result') or {}).get('planner_version'),
                         after_version=(row.get('result') or {}).get('planner_version')))
    return dict(n=len(rows), changed_n=sum(bool(r['changed_fields']) for r in rows), rows=rows)


def tiny_summary(runs_root):
    result = deepcopy(h.read_json(runs_root/'test-bounds/summary.json'))
    rows = h.load_records(runs_root/TINY, {'tiny'})
    source = validate_current(rows, result['expected_input_sha256'])
    result.pop('execution', None)  # Historical compute describes the archived run.
    result['created_at'] = datetime.now(timezone.utc).isoformat()
    result['records']['twostep'] = rows
    a = aggregate_results(rows, method='twostep', split='test', population='own')
    a.update(expected_instances=result['expected_instances'], missing_instances=0,
             checker_outcomes=dict(Counter(checker_outcome(r) for r in rows)),
             first_failing_rules=dict(Counter(h.first_failure(r) for r in rows if h.first_failure(r))),
             exact_labels=None, pipeline_diagnostics=[h.pipeline_diagnostic(r) for r in rows],
             objective_eligible_instances=[r['instance_id'] for r in rows if h.eligible(r)])
    result['methods']['twostep'] = a
    for first, second in itertools.combinations(h.METHODS[:3], 2):
        if 'twostep' not in (first, second):
            continue
        try:
            result['paired_comparisons'][f'{first}_minus_{second}'] = paired_comparison(
                result['records'][first], result['records'][second], population='own')
        except ValueError as exc:
            result['paired_comparisons'][f'{first}_minus_{second}'] = dict(unavailable=str(exc))
    folder = runs_root/TINY
    result['run_pins']['twostep'] = dict(
        manifest_sha256=h.digest(folder/'manifest.json'), manifest=h.read_json(folder/'manifest.json'),
        record_sha256=h.digest(folder/'records.jsonl'),
        source_before_variants=[rows[0]['source_before']],
        source_drift_instances=[r['instance_id'] for r in rows if r.get('source_drift')],
        reference_exclusions=[h.pipeline_diagnostic(r) for r in rows if r.get('reference_exclusion_reason')])
    result['twostep_version'] = VERSION
    result['twostep_source_sha256'] = source
    return result


def small_summary(runs_root, expected):
    sources = small.SOURCES
    try:
        small.SOURCES = dict(sources, twostep=[(SMALL, 'two-step 4.4.0 source-pinned')])
        result = small.summarize(runs_root, expected)
    finally:
        small.SOURCES = sources
    result['twostep_source_sha256'] = validate_current(result['records']['twostep'], [e['sha256'] for e in expected])
    result['twostep_version'] = VERSION
    return result


def render(tiny, current_small, accepted_report, archive_relative):
    # Preserve the accepted solver/ablation body and every measured number in it.
    if accepted_report.count(v21.MARKER) != 1 or accepted_report.count(v21.END_MARKER) != 1:
        raise ValueError('Archive lacks unique accepted solver/ablation section')
    solver_body = accepted_report.split(v21.MARKER, 1)[1].split(v21.END_MARKER, 1)[0].decode()
    solver_body = solver_body.replace('Earlier tiny and ten-case TEST v2 sections remain historical and byte-identical.',
                                      'Earlier TEST v2 measurements remain archived; this accepted solver/ablation section retains its measured numbers.')
    solver_body = solver_body.replace('Two-step awaits leader go; exact remains tiny only.',
                                      'Current two-step 4.4.0 references follow below; exact remains tiny only.')
    tiny_text = h.markdown(tiny).replace('test-bounds/summary.json', f'{TINY}/summary.json')
    tiny_text = tiny_text.replace('## Every plan attempt', '## Tiny — every plan attempt (two-step 4.4.0)')
    small_text = small.markdown(current_small).replace('4.3.0', VERSION)
    small_text = small_text.replace('Earlier accepted sections are preserved byte for byte.',
                                    'Earlier two-step runs are archived; accepted solver and bound measurements are unchanged.')
    small_text = small_text.replace('test-twostep-small-v21/', f'{SMALL}/')
    provenance = (f'\n\nCurrent two-step version: **{VERSION}** on both rungs. '
                  f'Tiny `twostep.py` SHA256: `{tiny["twostep_source_sha256"]}`; '
                  f'small SHA256: `{current_small["twostep_source_sha256"]}`. '
                  f'[Archived pre-4.4 report]({archive_relative}); '
                  '[archived small 4.3 evidence](test-twostep-small-v21/summary.json); '
                  '[archived original tiny evidence](test-twostep/manifest.json). '
                  f'[Version comparison]({SMALL}/version-comparison.json).\n\n')
    return tiny_text + provenance + v21.MARKER.decode() + solver_body + v21.END_MARKER.decode() + '\n\n' + small_text


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--runs-root', type=Path, default=ROOT/'experiments/mvp/runs')
    p.add_argument('--release-report', type=Path, default=Path(__file__).with_name('small-v21-release-frozen.json'))
    p.add_argument('--manifest', type=Path, default=ROOT/'experiments/mvp/corpus/manifest.json')
    p.add_argument('--archive-report', type=Path, required=True)
    args = p.parse_args()
    expected = v21.v2.released_inputs(args.release_report, args.manifest, 15)
    for e in expected:
        e['instance_id'] = h.read_json(args.manifest.parent/e['path'])['id']
    tiny = tiny_summary(args.runs_root)
    current = small_summary(args.runs_root, expected)
    if tiny['twostep_source_sha256'] != current['twostep_source_sha256']:
        raise ValueError('Tiny and small must share the frozen two-step 4.4.0 source hash')
    comparison = dict(small_43_to_44=changes(h.load_records(args.runs_root/'test-twostep-small-v21', {'small'}), current['records']['twostep']),
                      tiny_original_to_44=changes(h.load_records(args.runs_root/'test-twostep', {'tiny'}), tiny['records']['twostep']))
    for folder, value in ((TINY, tiny), (SMALL, current)):
        (args.runs_root/folder/'summary.json').write_text(json.dumps(value, indent=2, sort_keys=True, allow_nan=False)+'\n')
    (args.runs_root/SMALL/'version-comparison.json').write_text(json.dumps(comparison, indent=2, sort_keys=True, allow_nan=False)+'\n')
    archive_relative = str(args.archive_report.resolve().relative_to(args.runs_root.resolve()))
    (args.runs_root/'test-references.md').write_text(render(tiny, current, args.archive_report.read_bytes(), archive_relative))
    print(json.dumps(dict(source=current['twostep_source_sha256'], small_changed=comparison['small_43_to_44']['changed_n'],
                          tiny_changed=comparison['tiny_original_to_44']['changed_n'], paired=current['paired_mutual_pass']), sort_keys=True))


if __name__ == '__main__':
    main()
