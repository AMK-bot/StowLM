#!/usr/bin/env python3
"""Append released two-step measurements and explicitly conditional mutual-pass pairs."""
from __future__ import annotations
import argparse
import hashlib
import importlib.util
import json
from pathlib import Path
from eval.harness_metrics import aggregate_results, checker_outcome

_spec = importlib.util.spec_from_file_location('small_v21_helpers', Path(__file__).with_name('summarize_small_v21.py'))
v21 = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(v21)
h, ROOT = v21.h, v21.ROOT
MARKER = b'<!-- BEGIN SMALL TEST V2.1 TWO-STEP REFERENCES -->'
END_MARKER = b'<!-- END SMALL TEST V2.1 TWO-STEP REFERENCES -->'
SOURCES = {
    'twostep': [('test-twostep-small-v21', 'two-step 4.3.0 source-pinned')],
    'solver': v21.SOURCES['solver'],
    'bounds': v21.SOURCES['bounds'],
}


def mean(values):
    return sum(values) / len(values) if values else None


def attach_production_receipt(summary, path):
    """Preserve observed production drift without replacing measured snapshot evidence."""
    if path.exists():
        summary['production_source_at_end'] = dict(path=str(path), sha256=h.digest(path), receipt=h.read_json(path))


def summarize(runs_root, expected):
    records, provenance, methods = {}, {}, {}
    for method, sources in SOURCES.items():
        rows, epochs = v21.union_records(runs_root, sources, expected)
        expected_by_hash = {e['sha256']: e for e in expected}
        for row in rows:
            native_id = expected_by_hash[row['input_sha256']].get('instance_id')
            if native_id is not None and row['instance_id'] != native_id:
                raise ValueError(f"Native instance ID mismatch: {row['instance_id']}")
        records[method], provenance[method] = rows, epochs
        if method != 'bounds':
            seen = {r['input_sha256'] for r in rows}
            missing = [e for e in expected if e['sha256'] not in seen]
            allocated = rows + [dict(instance_id=e.get('instance_id', Path(e['path']).stem),
                                    input_sha256=e['sha256'], allocation_only=True, method=method)
                                for e in missing]
            methods[method] = aggregate_results(allocated, method=method, split='test', population='own')
            methods[method].update(expected_instances=len(expected), missing_paths=[e['path'] for e in missing])
    index = {method: {r['input_sha256']: r for r in rows} for method, rows in records.items()}
    for sha in index['twostep'].keys() & index['solver'].keys():
        a, b = index['twostep'][sha], index['solver'][sha]
        if (a.get('effective_seed') is not None or b.get('effective_seed') is not None) and a.get('effective_seed') != b.get('effective_seed'):
            raise ValueError(f"Two-step/official solver effective seed mismatch: {a['instance_id']}")
    pairs = []
    ships = sorted({e['ship'] for e in expected})
    for ship in ships + ['overall']:
        cohort = [e for e in expected if ship == 'overall' or e['ship'] == ship]
        pair_rows = []
        for e in cohort:
            a, b, bound = (index[m].get(e['sha256']) for m in ('twostep', 'solver', 'bounds'))
            if a and b and h.eligible(a) and h.eligible(b):
                aw, bw = h.counts(a)['weighted_nonproductive'], h.counts(b)['weighted_nonproductive']
                if aw is None or bw is None:
                    raise ValueError('Eligible plan lacks measured objective')
                if not bound or (bound.get('lower_bound') or {}).get('weighted') is None:
                    raise ValueError('Mutual-pass pair lacks measured bound')
                pair_rows.append(dict(input_sha256=e['sha256'], instance_id=a['instance_id'],
                                      twostep=aw, solver=bw, bound=bound['lower_bound']['weighted'],
                                      delta=aw-bw))
        def rate(method):
            return sum(checker_outcome(index[method].get(e['sha256'], {})) == 'passed' for e in cohort)
        def eligible_count(method):
            return sum(h.eligible(index[method].get(e['sha256'], {})) for e in cohort)
        result = dict(ship=ship, frozen_n=len(cohort), twostep_pass=rate('twostep'), solver_pass=rate('solver'),
                      twostep_shippable=eligible_count('twostep'), solver_shippable=eligible_count('solver'),
                      mutual_n=len(pair_rows), excluded_from_mutual_n=len(cohort)-len(pair_rows), pairs=pair_rows,
                      mean_delta_twostep_minus_solver=mean([r['delta'] for r in pair_rows]),
                      twostep_wins=sum(r['delta']<0 for r in pair_rows), ties=sum(r['delta']==0 for r in pair_rows),
                      twostep_losses=sum(r['delta']>0 for r in pair_rows))
        for method in ('twostep', 'solver', 'bound'):
            values = [r[method] for r in pair_rows]
            result[method] = dict(n=len(values), sum=sum(values) if values else None, mean=mean(values))
        pairs.append(result)
    source_evidence = []
    for row in records['twostep']:
        source_evidence.append(dict(instance_id=row['instance_id'], effective_seed=row.get('effective_seed'),
                                   component_versions=row.get('component_versions'),
                                   source_snapshot_sha256=hashlib.sha256(json.dumps(row.get('source_before', {}), sort_keys=True).encode()).hexdigest(),
                                   twostep_source={k: v for k, v in (row.get('source_before') or {}).items() if 'twostep' in k}))
    return dict(expected=expected, records=records, methods=methods, paired_mutual_pass=pairs,
                source_epochs=provenance, twostep_source_evidence=source_evidence)


def markdown(summary):
    n=len(summary['expected'])
    lines=['## Small — TEST v2.1 two-step 4.3.0 references', '',
           f'Frozen denominator: **{n} small instances**. Every failure and missing attempt stays in all pass-rate denominators. '
           'Earlier accepted sections are preserved byte for byte.', '',
           'APPROXIMATION: C2 OFF; `STOWAGE_BALLAST_V4=1` scoped only to finalization; no repair. '
           'Every plan receives the full checker. Objectives below require a passing, unrepaired, explicitly shippable plan. '
           'Failed-plan move counts are diagnostic only.', '',
           'Two-step source hashes, component versions and effective seeds are recorded below. '
           'The official solver uses the accepted gold configuration (securing screen and heavy-first ordering). '
           'Source epochs may differ between methods; this is an observed comparison on identical frozen inputs, '
           'not an isolated causal estimate of the planning algorithm.', '']
    drift=summary.get('production_source_at_end')
    if drift:
        receipt=drift['receipt']
        lines += [f"**Production source changed after the measured snapshot:** measurements are pinned to two-step `{receipt.get('frozen_version')}`; "
                  f"live production was `{receipt.get('live_version')}` at `{receipt.get('checked_utc')}`. "
                  f"Changed files: {', '.join('`'+p+'`' for p in receipt.get('changed', []))}. "
                  'These measurements do not evaluate live 4.4.0. The leader specified a separate rerun request; no rerun is implied.', '',
                  '[Full frozen/live source hashes and observation receipt](test-twostep-small-v21/production-source-at-end.json). '
                  f"Receipt SHA256: `{drift['sha256']}`.", '',
                  f"Measured `twostep.py` SHA256: `{receipt.get('frozen', {}).get('src/stowage/solver/twostep.py')}`. "
                  f"Live SHA256: `{receipt.get('live', {}).get('src/stowage/solver/twostep.py')}`.", '']
    aggregate=[]
    for method, data in summary['methods'].items():
        aggregate.append([method, data['n_instances'], f"{data['n_passed']}/{n}", f"{data['n_shippable']}/{n}",
                          data['moves']['weighted_nonproductive']['n'], data['moves']['weighted_nonproductive']['mean']])
    lines += h.table(['Method','Attempts','Checker/frozen','Shippable/frozen','Objective n','Own-pass mean W'],aggregate)
    index={m:{r['input_sha256']:r for r in rows} for m,rows in summary['records'].items()}
    rows=[]
    for e in summary['expected']:
        row=index['twostep'].get(e['sha256'])
        solver=index['solver'].get(e['sha256'], {})
        bound=index['bounds'].get(e['sha256'], {})
        c=h.counts(row or {})
        timing=(row or {}).get('timing') or {}
        rows.append([Path(e['path']).stem,e['ship'],checker_outcome(row) if row else 'unattempted',
                     (row or {}).get('shippable'), c['weighted_nonproductive'] if row and h.eligible(row) else None,
                     *[c[k] for k in (*h.FIELDS,'future_restow_lifts','future_hatch_cover_lifts')],
                     h.first_failure(row) if row else None,timing.get('plan_s'),timing.get('total_s'),
                     h.counts(solver)['weighted_nonproductive'] if h.eligible(solver) else None,
                     (bound.get('lower_bound') or {}).get('weighted')])
    lines += ['', '### Fifteen two-step outcomes', '', 'W = weighted nonproductive moves; R = yard rehandles; S = restow lifts; H = hatch-cover lifts.', '']
    lines += h.table(['Instance','Ship','Checker','Shippable','Eligible W','R now','S now','H now','S later','H later',
                      'First hard rule','Plan s','Pipeline s','Official solver W','Bound W'], rows)
    failures=[]
    for e in summary['expected']:
        row=index['twostep'].get(e['sha256'])
        if row is None:
            failures.append([Path(e['path']).stem,'unattempted',None,None,None,None])
        elif not h.eligible(row):
            result=row.get('result') or {}
            failures.append([row['instance_id'],result.get('status'),row.get('failed_stage'),
                             h.first_failure(row),result.get('message'),row.get('error')])
    lines += ['', '### Failed or excluded two-step attempts — diagnostic evidence', '',
              'A planner dead end without a plan has no checker verdict or failing checker rule. '
              'Missing rules and objectives remain missing; planner messages do not establish checker infeasibility.', '']
    lines += h.table(['Instance','Planner status','Failed stage','First hard checker rule','Planner message','Recorded error'],failures)
    if not failures:
        lines += ['', 'No failed or excluded two-step attempts.']
    lines += ['', '### Conditional matched mutual passes', '',
              'Each objective and bound in a row uses exactly the same mutual checker-pass, objective-eligible, shippable inputs. '
              'This conditional subset is not a full-population paired comparison. Failed and missing cases remain in the frozen denominators above. '
              'Delta = two-step minus solver; negative favors two-step. Wins/ties/losses refer to two-step. '
              'A bound is a measured floor on unavoidable moves; zero bounds are not divided into a relative gap '
              '([bound definitions](../../../src/stowage/bounds/__init__.py)).', '']
    pair_rows=[]
    for p in summary['paired_mutual_pass']:
        pair_rows.append([p['ship'],p['frozen_n'],p['twostep_pass'],p['solver_pass'],p['mutual_n'],
                          p['twostep']['sum'],p['twostep']['mean'],p['solver']['sum'],p['solver']['mean'],
                          p['bound']['sum'],p['bound']['mean'],p['mean_delta_twostep_minus_solver'],
                          p['twostep_wins'],p['ties'],p['twostep_losses']])
    lines += h.table(['Ship','Frozen N','Two-step passes','Solver passes','Mutual N','Two-step sum W','Two-step mean W',
                      'Solver sum W','Solver mean W','Bound sum W','Bound mean W','Mean delta','Wins','Ties','Losses'],pair_rows)
    lines += ['', '### Two-step source evidence', '', '```json',json.dumps(summary['twostep_source_evidence'],indent=2,sort_keys=True),'```','',
              '[Full records, conditional pairs and provenance](test-twostep-small-v21/summary.json).','']
    return '\n'.join(lines)


def append_section(report, body, accepted_prefix):
    original=report.read_bytes()
    if original.count(MARKER)>1 or original.count(END_MARKER)>1:
        raise ValueError('Duplicate two-step markers')
    if MARKER in original:
        prefix,rest=original.split(MARKER,1)
        if END_MARKER not in rest or rest.split(END_MARKER,1)[1].strip():
            raise ValueError('Incomplete or nonterminal two-step section')
    elif END_MARKER in original:
        raise ValueError('Orphan two-step end marker')
    else:
        prefix=original
    if prefix != accepted_prefix.read_bytes():
        raise ValueError('Accepted prefix differs from preserved copy')
    report.write_bytes(prefix+MARKER+b'\n\n'+body.encode()+END_MARKER+b'\n')
    return hashlib.sha256(prefix).hexdigest()


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--runs-root',type=Path,default=ROOT/'experiments/mvp/runs')
    p.add_argument('--release-report',type=Path,default=Path(__file__).with_name('small-v21-release-frozen.json'))
    p.add_argument('--manifest',type=Path,default=ROOT/'experiments/mvp/corpus/manifest.json')
    p.add_argument('--accepted-prefix',type=Path,default=Path(__file__).with_name('approved-small-v21-report.md'))
    p.add_argument('--expected-count',type=int,default=15)
    args=p.parse_args()
    expected=v21.v2.released_inputs(args.release_report,args.manifest,args.expected_count)
    for e in expected:
        e['instance_id']=h.read_json(args.manifest.parent/e['path'])['id']
    summary=summarize(args.runs_root,expected)
    summary.update(release_report_sha256=h.digest(args.release_report),manifest_sha256=h.digest(args.manifest))
    attach_production_receipt(summary,args.runs_root/'test-twostep-small-v21'/'production-source-at-end.json')
    summary['accepted_prefix_sha256']=append_section(args.runs_root/'test-references.md',markdown(summary),args.accepted_prefix)
    destination=args.runs_root/'test-twostep-small-v21'
    destination.mkdir(parents=True,exist_ok=True)
    (destination/'summary.json').write_text(json.dumps(summary,indent=2,sort_keys=True,allow_nan=False)+'\n')
    print(json.dumps(summary['paired_mutual_pass'],sort_keys=True))


if __name__=='__main__':
    main()
