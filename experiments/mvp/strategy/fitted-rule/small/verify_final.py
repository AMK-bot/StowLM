"""Read-only numerical/provenance verification after all D217 trials finish.

Does not import or invoke the decoder, preparation, ranking, finisher or checker.
Exact Fraction arithmetic independently rebuilds reported paired comparisons.
"""
from datetime import datetime, timezone
from fractions import Fraction
import hashlib
import json
import math
from pathlib import Path

OUT = Path(__file__).resolve().parent
BASE = OUT.parent
ROOT = BASE.parents[3]
K1B = BASE.parent/'k1b'
KS = (1, 2, 4, 8, 16)


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def read_cohort(folder, expected):
    paths = sorted(folder.glob('*.json'))
    rows = [json.loads(p.read_text()) for p in paths]
    indexed = {r['instance_id']:r for r in rows}
    assert len(paths)==55 and len(indexed)==55 and set(indexed)==expected, folder
    return indexed, {str(p.relative_to(ROOT)):sha(p) for p in paths}


def frac(value):
    return Fraction(str(value))


def paired(mine, rival):
    assert len(mine)==55 and set(mine)==set(rival)
    ids = sorted(mine)
    mutual = [i for i in ids if mine[i] is not None and rival[i] is not None]
    a = [frac(mine[i]) for i in mutual]
    b = [frac(rival[i]) for i in mutual]
    sa, sb = sum(a, Fraction(0)), sum(b, Fraction(0))
    ma, mb = (sa/len(a), sb/len(b)) if a else (None, None)
    return dict(n_compared=55, mine_pass=sum(mine[i] is not None for i in ids),
                rival_pass=sum(rival[i] is not None for i in ids), mutual=len(mutual),
                mine_mean=float(ma) if ma is not None else None,
                rival_mean=float(mb) if mb is not None else None,
                delta=float(ma/mb-1) if mb else None,
                fewer=sum(x<y for x,y in zip(a,b)), equal=sum(x==y for x,y in zip(a,b)),
                more=sum(x>y for x,y in zip(a,b)),
                only_rule=sum(mine[i] is not None and rival[i] is None for i in ids),
                only_rival=sum(mine[i] is None and rival[i] is not None for i in ids),
                mutual_ids=mutual, exact_rule_sum=str(sa), exact_rival_sum=str(sb))


def same(actual, expected):
    if expected is None:
        assert actual is None, (actual, expected)
    elif isinstance(expected, (int,float)):
        assert isinstance(actual,(int,float)) and math.isclose(actual,expected,rel_tol=1e-12,abs_tol=1e-12), (actual, expected)
    else:
        assert actual==expected, (actual, expected)


def main():
    manifest = json.loads((OUT/'manifest.json').read_text())
    expected = {json.loads((ROOT/rel).read_text())['id'] for rel in manifest['inputs']}
    assert len(expected)==55
    assert all(sha(ROOT/rel)==digest for rel,digest in manifest['inputs'].items())
    assert sha(BASE/'frozen-source/fitted.json')==manifest['weights_sha256']
    curves, curve_sha = read_cohort(OUT/'curves',expected)
    greedy, greedy_sha = read_cohort(OUT/'greedy',expected)
    summary = json.loads((OUT/'summary.json').read_text())
    assert summary['pending'] is False
    done = json.loads((OUT/'curves-sharded/DONE.json').read_text())
    assert done['curves']==55 and done['exit_codes']==[0,0]
    report_path = BASE/'SMALL.md'
    text = report_path.read_text()
    judged, comparator_sha = {}, {}
    for label,folder in [('K1b',K1B),('K1r',K1B/'k1r'),('K1c masked alone',K1B/'k1c'),('null',K1B/'null')]:
        judged[label],digests = read_cohort(folder/'judged/model',expected)
        comparator_sha.update(digests)
    historical_curves = {}
    for label,folder in [('K1b',K1B/'kcurve/8.2'),('K1r',K1B/'kcurve/K1r')]:
        historical_curves[label],digests = read_cohort(folder,expected)
        comparator_sha.update(digests)
    assert len(comparator_sha)==330
    pool = {label:dict(samples=sum(r['n_samples'] for r in rows.values()),
                     complete=sum(r['n_complete'] for r in rows.values()))
            for label,rows in judged.items()}
    for label,value in [('K1b',792),('K1r',729),('null',668),('K1c masked alone',560)]:
        assert pool[label]==dict(samples=880,complete=value), (label,pool[label])
    comparisons = {}
    fmt = lambda x:'—' if x is None else f'{x:.2f}'
    for k in KS:
        point = str(k)
        mine = {i:r['points'][point]['moves'] for i,r in curves.items()}
        assert summary['curves'][point]['maps']==55
        same(summary['curves'][point]['passed'],sum(v is not None for v in mine.values()))
        pass_values = [frac(v) for v in mine.values() if v is not None]
        own_mean = float(sum(pass_values,Fraction(0))/len(pass_values)) if pass_values else None
        same(summary['curves'][point]['mean_moves'],own_mean)
        comparisons[point] = {}
        rivals = {label:{i:r['points'][point]['moves'] for i,r in rows.items()}
                  for label,rows in historical_curves.items()}
        if k==16:
            rivals['K1c masked alone'] = {i:r['moves'] for i,r in judged['K1c masked alone'].items()}
            rivals['K1c with K1b fallback'] = {
                i:min((v for v in (judged['K1b'][i]['moves'],judged['K1c masked alone'][i]['moves']) if v is not None),default=None)
                for i in expected}
        for label,rival in rivals.items():
            result = paired(mine,rival)
            comparisons[point][label] = result
            actual = summary['comparisons'][point][label]
            for field,value in actual.items():
                same(value,result[field])
            # Exact arithmetic has already verified these floats. Use the
            # published float for the final presentation rounding only.
            delta = '—' if actual['delta'] is None else f'{actual["delta"]:+.1%}'
            line = (f'| {k} | {label} | 55 | {result["mine_pass"]} / {result["rival_pass"]} | {result["mutual"]} | '
                    f'{fmt(actual["mine_mean"])} / {fmt(actual["rival_mean"])} | {delta} | '
                    f'{result["fewer"]} / {result["equal"]} / {result["more"]} |')
            assert line in text, ('printed paired row differs',line)
    gmine = {i:r['evaluation']['moves'] if r['evaluation']['passed'] else None for i,r in greedy.items()}
    gref = {i:r['points']['1']['moves'] for i,r in historical_curves['K1b'].items()}
    greedy_pair = paired(gmine,gref)
    gpasses = [frac(v) for v in gmine.values() if v is not None]
    gmean = float(sum(gpasses,Fraction(0))/len(gpasses))
    same(summary['greedy']['passed'],len(gpasses))
    same(summary['greedy']['mean_moves'],gmean)
    assert f'| Frozen fitted rule | 55/55 | 48 | 48 | {gmean:.2f} |' in text
    reference_values = [frac(v) for v in gref.values() if v is not None]
    reference_mean = float(sum(reference_values,Fraction(0))/len(reference_values))
    reference_complete = sum(r['points']['1']['eligible'] for r in historical_curves['K1b'].values())
    assert f'| Historical 8.2 k=1 | 55/55 | {reference_complete} | {len(reference_values)} | {reference_mean:.2f} |' in text
    k16_consistency = {label:[i for i in expected if judged[label][i]['moves'] != historical_curves[label][i]['points']['16']['moves']]
                       for label in ('K1b','K1r')}
    provenance = dict(verified_at=datetime.now(timezone.utc).isoformat(),
                      comparator_rows_sha256=comparator_sha, comparator_rows=330,
                      cohorts=55, weights_sha256=manifest['weights_sha256'],
                      primary_manifest_sha256=sha(OUT/'manifest.json'),
                      summary_sha256=sha(OUT/'summary.json'), small_md_sha256=sha(report_path),
                      curve_rows_sha256=curve_sha, greedy_rows_sha256=greedy_sha,
                      verifier_sha256=sha(Path(__file__)),
                      source_semantics='K1b/K1r use stored kcurve points at every k; K1c masked and fallback use judged/model costs at k16. Null rows bind the pooled completion table only.',
                      historical_execution_windows=True, separate_greedy_and_all16_stochastic_T1=True)
    (OUT/'comparator-provenance.json').write_text(json.dumps(provenance,indent=2,sort_keys=True)+'\n')
    verification = dict(verified=True,verified_at=provenance['verified_at'],
                        arithmetic='Independent exact Fraction sums and ratios; no decoder/evaluator/ranking imports or new trials',
                        maps=55, curve_points_verified=275, printed_paired_rows_verified=12,
                        comparator_provenance_sha256=sha(OUT/'comparator-provenance.json'),
                        pooled_historical_completion=pool, comparisons=comparisons,
                        greedy_vs_82_k1=greedy_pair,
                        historical_judged_vs_k16_discrepancies=k16_consistency,
                        source_files_unchanged=all(sha(ROOT/rel)==digest for rel,digest in manifest['code'].items()))
    assert verification['source_files_unchanged']
    assert not any(k16_consistency.values()), k16_consistency
    (OUT/'final-verification.json').write_text(json.dumps(verification,indent=2,sort_keys=True)+'\n')
    print(json.dumps(dict(verified=True,maps=55,curve_points=275,comparator_rows=330,paired_rows=12)))


if __name__=='__main__':
    main()
