#!/usr/bin/env python3
"""Append the explicitly selected TEST v2.1 cohort; never execute planners."""
from __future__ import annotations
import argparse
import hashlib
import importlib.util
import json
from pathlib import Path
from eval.harness_metrics import aggregate_results, checker_outcome, paired_comparison

_spec = importlib.util.spec_from_file_location('small_v2_helpers', Path(__file__).with_name('summarize_small_v2.py'))
v2 = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(v2)
h = v2.helpers
ROOT = Path(__file__).resolve().parents[3]
MARKER = b'<!-- BEGIN SMALL TEST V2.1 REFERENCES -->'
END_MARKER = b'<!-- END SMALL TEST V2.1 REFERENCES -->'
SOURCES = {
    'solver': [('test-solver-small-v21-screen', 'v2.1 official screened, heavy-first')],
    'solver, no securing screen': [('test-solver-small-v2', 'v2 original ten'),
                                  ('test-solver-small-v21-noscreen-extra', 'v2.1 added five')],
    'bounds': [('test-bounds-small-v2', 'v2 original ten'),
               ('test-bounds-small-v21-extra', 'v2.1 added five')],
}


def union_records(runs_root, sources, expected):
    selected = {r['sha256']: r for r in expected}
    rows, seen_ids, seen_hashes, epochs = [], set(), set(), []
    if len(selected) != len(expected) or len({r['path'] for r in expected}) != len(expected):
        raise ValueError('Duplicate frozen input identities')
    for folder, epoch in sources:
        loaded = h.load_records(runs_root / folder, {'small'})
        source_digests = set()
        for row in loaded:
            sha, identity = row.get('input_sha256'), row['instance_id']
            if sha not in selected:
                raise ValueError(f'Unmatched input: {folder}/{identity}')
            if sha in seen_hashes or identity in seen_ids:
                raise ValueError(f'Duplicate input identity across source epochs: {identity}')
            allowed_ids = {Path(selected[sha]['path']).stem, selected[sha].get('instance_id')}
            if identity not in allowed_ids:
                raise ValueError(f'Unmatched instance ID/hash: {identity}')
            seen_ids.add(identity)
            seen_hashes.add(sha)
            # Keep canonical rows untouched; provenance is attached to copies only.
            enriched = dict(row, reference_epoch=epoch, reference_run_folder=folder)
            rows.append(enriched)
            source_digests.add(hashlib.sha256(json.dumps(row.get('source_before', {}), sort_keys=True).encode()).hexdigest())
        epochs.append(dict(label=epoch, run_folder=folder, attempts=len(loaded),
                           source_snapshot_digests=sorted(source_digests),
                           source_roots=sorted({r.get('source_root', 'unrecorded') for r in loaded})))
    return rows, epochs


def summarize(runs_root, expected):
    records, methods, provenance = {}, {}, {}
    for method, sources in SOURCES.items():
        rows, epochs = union_records(runs_root, sources, expected)
        records[method], provenance[method] = rows, epochs
        if method == 'bounds':
            continue
        seen = {r['input_sha256'] for r in rows}
        missing = [r for r in expected if r['sha256'] not in seen]
        # Allocation-only entries make the D68 denominator the entire frozen cohort.
        allocated = rows + [dict(instance_id=Path(r['path']).stem, input_sha256=r['sha256'],
                                 allocation_only=True, method='solver') for r in missing]
        summary = aggregate_results(allocated, method='solver', split='test', population='own')
        summary.update(expected_instances=len(expected), missing_paths=[r['path'] for r in missing])
        methods[method] = summary
    pair = {'unavailable': 'Full same frozen population required; no partial intersection'}
    if all(len(records[m]) == len(expected) for m in methods):
        try:
            pair = paired_comparison(records['solver'], records['solver, no securing screen'], population='own')
        except ValueError as exc:
            pair = {'unavailable': str(exc)}
    return dict(expected=expected, records=records, methods=methods, source_epochs=provenance,
                paired_comparison=pair)


def markdown(summary):
    n = len(summary['expected'])
    lines = ['## Small — frozen TEST v2.1, official solver and ablation', '',
             f'Explicit released population: **{n} small instances**. Earlier tiny and ten-case TEST v2 sections remain historical and byte-identical.', '',
             'Official **solver** uses `ScalableConfig(securing_screen=True)` and its heavy-first ordering (D92/D94), matching gold. '
             'Official random seeds are each instance seed, matching gold. '
             '**solver, no securing screen** retains the original ten attempts and adds five feeder_1700 attempts, all with fixed seed 0. '
             'Two-step awaits leader go; exact remains tiny only.', '',
             'APPROXIMATION: C2 OFF; `STOWAGE_BALLAST_V4=1` scoped only to finalization (D88). '
             'Every plan is gated by the full checker. Failed and unattempted cases remain in the frozen denominator; '
             'objective means include only passing, unrepaired, explicitly shippable plans.', '',
             '**Source confounding and seed caveat:** the ablation combines original-ten and added-five source epochs, '
             'and uses fixed seed 0 while the official solver uses instance seeds. '
             'Snapshot hashes and roots below identify changes; differences cannot be attributed solely to the securing screen when code or seeds differ. '
             'The paired comparison describes measured outcomes on matching inputs, not an isolated causal screen effect.', '']
    aggregates=[]
    for method, a in summary['methods'].items():
        aggregates.append([method, f"{a['n_instances']}/{n}", len(a['missing_paths']),
                           f"{a['n_passed']}/{n}", f"{a['n_shippable']}/{n}",
                           a['moves']['weighted_nonproductive']['n'],
                           a['moves']['weighted_nonproductive']['mean'], a['pipeline_time_s']['sum']])
    lines += h.table(['Method', 'Attempts/frozen', 'Unattempted', 'Checker/frozen', 'Shippable/frozen',
                      'Objective n', 'Mean weighted', 'Pipeline s'], aggregates)
    indexed = {m: {r['input_sha256']: r for r in rs} for m, rs in summary['records'].items()}
    compact, detail=[] , []
    for e in summary['expected']:
        cells=[Path(e['path']).stem, e.get('ship')]
        for method in summary['methods']:
            r=indexed[method].get(e['sha256'])
            if r is None:
                cells += ['unattempted', None, None]
                continue
            c=h.counts(r)
            cells += [checker_outcome(r), c['weighted_nonproductive'] if h.eligible(r) else None,
                      (r.get('timing') or {}).get('total_s')]
            detail.append([r['instance_id'], method, r['reference_epoch'], checker_outcome(r), h.first_failure(r),
                           r.get('shippable'), *[c[k] for k in (*h.FIELDS, 'future_restow_lifts', 'future_hatch_cover_lifts')],
                           c['weighted_nonproductive'], (r.get('timing') or {}).get('plan_s'),
                           (r.get('timing') or {}).get('total_s')])
        b=indexed['bounds'].get(e['sha256'], {})
        cells += [(b.get('lower_bound') or {}).get('weighted')]
        compact.append(cells)
    lines += ['', '### All fifteen instances, both configurations', '']
    lines += h.table(['Instance', 'Ship', 'Solver checker', 'Solver weighted', 'Solver s',
                      'No-screen checker', 'No-screen weighted', 'No-screen s', 'Weighted LB'], compact)
    lines += ['', '### Move splits and first failures (diagnostic counts include failed attempts)', '',
              'R = yard rehandles; S = restow lifts; H = hatch-cover lifts. Failed-plan counts are diagnostic only.', '']
    lines += h.table(['Instance', 'Method', 'Epoch', 'Checker', 'First hard rule', 'Shippable', 'R now', 'S now',
                      'H now', 'S later', 'H later', 'Weighted diagnostic', 'Plan s', 'Pipeline s'], detail)
    lines += ['', f"Bounds recorded: {len(indexed['bounds'])}/{n}; bounds are not plans and receive no checker verdict.", '',
              '### Paired comparison and source epochs', '',
              'Solver minus no-screen. Only full matching populations are admitted; objective eligibility still excludes failed plans. '
              'The paired bootstrap estimates uncertainty by resampling matching instances '
              '([D68 implementation and source](../../../src/eval/harness_metrics.py)).', '',
              '```json', json.dumps(summary['paired_comparison'], indent=2, sort_keys=True), '```', '']
    for method, epochs in summary['source_epochs'].items():
        lines += [f'**{method}**', '', '```json', json.dumps(epochs, indent=2, sort_keys=True), '```', '']
    lines += ['[Full D68 aggregates, records, release pins and input settings](test-bounds-small-v21/summary.json).', '']
    return '\n'.join(lines)


def append_section(report, body, backup_dir):
    original=report.read_bytes()
    if original.count(MARKER)>1 or original.count(END_MARKER)>1:
        raise ValueError('Duplicate v2.1 markers')
    if MARKER in original:
        prefix, rest=original.split(MARKER, 1)
        if END_MARKER not in rest or rest.split(END_MARKER, 1)[1].strip():
            raise ValueError('Incomplete or nonterminal v2.1 section')
    elif END_MARKER in original:
        raise ValueError('Orphan v2.1 end marker')
    else:
        prefix=original
    backup_dir.mkdir(parents=True, exist_ok=True)
    backup=backup_dir/'accepted-pre-v21-report.md'
    if backup.exists() and backup.read_bytes()!=prefix:
        raise ValueError('Accepted prefix differs from preserved copy')
    backup.write_bytes(prefix)
    digest=hashlib.sha256(prefix).hexdigest()
    report.write_bytes(prefix+MARKER+b'\n\n'+body.encode()+END_MARKER+b'\n')
    return digest


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--runs-root', type=Path, default=ROOT/'experiments/mvp/runs')
    p.add_argument('--release-report', type=Path, required=True, help='Explicit frozen copy of EVAL-V2-REPORT.json')
    p.add_argument('--accepted-prefix', type=Path, default=Path(__file__).with_name('approved-small-v2-report.md'))
    p.add_argument('--manifest', type=Path, default=ROOT/'experiments/mvp/corpus/manifest.json')
    p.add_argument('--expected-count', type=int, default=15)
    args=p.parse_args()
    expected=v2.released_inputs(args.release_report,args.manifest,args.expected_count)
    for row in expected:
        row['instance_id'] = h.read_json(args.manifest.parent / row['path'])['id']
    summary=summarize(args.runs_root, expected)
    summary.update(release_report_sha256=h.digest(args.release_report), manifest_sha256=h.digest(args.manifest))
    destination=args.runs_root/'test-bounds-small-v21'
    report_prefix = (args.runs_root/'test-references.md').read_bytes().split(MARKER, 1)[0]
    if report_prefix != args.accepted_prefix.read_bytes():
        raise ValueError('Accepted pre-v2.1 report differs from explicit approved prefix')
    summary['accepted_prefix_sha256']=append_section(args.runs_root/'test-references.md', markdown(summary),destination)
    (destination/'summary.json').write_text(json.dumps(summary,indent=2,sort_keys=True,allow_nan=False)+'\n')
    print(json.dumps({k: v['n_passed'] for k,v in summary['methods'].items()}))

if __name__=='__main__':
    main()
