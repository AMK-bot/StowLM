"""Report full-check gates by fixed fill rung, and best primary-map gaps."""
from pathlib import Path
import argparse
import json
import statistics

HERE = Path(__file__).resolve().parent
LAB = HERE.parents[1]


def read_rows(run):
    return [dict(json.loads(p.read_text()), evidence=str(p))
            for p in Path(run).glob('*.json') if p.name != 'summary.json']


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('runs',nargs='+')
    ap.add_argument('--primary-runs',nargs='*',default=[])
    ap.add_argument('--bounds',default=str(HERE/'bound_ports_results.json'))
    args = ap.parse_args()
    manifest = json.loads((HERE/'MANIFEST.json').read_text())
    by_input = {m['input']:m for m in manifest}
    all_rows = [r for run in args.runs for r in read_rows(run)]
    portfolio_rows = all_rows+[r for run in args.primary_runs for r in read_rows(run)]
    latest = {}
    for r in all_rows:
        latest[r['input']] = r
    rungs = {}
    for rung in ('f60','f70'):
        wanted = [m for m in manifest if m['rung']==rung]
        measured = [latest[m['input']] for m in wanted if m['input'] in latest]
        paired = [r for r in measured if r['passed'] and by_input[r['input']]['v6_passed']]
        rungs[rung] = dict(selected=len(wanted),measured=len(measured),
            replay_legal=sum(bool(r.get('method_metadata',{}).get('replay_legal')) for r in measured),
            full_passes=sum(r['passed'] for r in measured),v6_passes=sum(m['v6_passed'] for m in wanted),
            mean_wall_s=statistics.mean(r['wall_s'] for r in measured) if measured else None,
            paired_v6_maps=len(paired),
            paired_mean_lifts=statistics.mean(r['lifts'] for r in paired) if paired else None,
            paired_v6_mean=statistics.mean(by_input[r['input']]['v6_lifts'] for r in paired) if paired else None)
    targets = (HERE/'train4.txt').read_text().splitlines()
    bounds = {r['input']:r for r in json.loads(Path(args.bounds).read_text())}
    references = read_rows(LAB/'c2/c2-layout/results/quick-medium-cached')+read_rows(LAB/'c2/c2-layout/results/extras-cached')
    primary = []
    for path in targets:
        candidates = [r for r in references+portfolio_rows if r['input']==path and r['passed']]
        best = min(candidates,key=lambda r:(r['lifts'],0 if '/c3/' in r['evidence'] else 1,r['wall_s'])) if candidates else None
        old = next(r for r in references if r['input']==path)
        b = bounds[path]
        primary.append(dict(instance_id=old['instance_id'],input=path,
            passed=best is not None,lifts=best['lifts'] if best else None,
            parts=best['parts'] if best else None,lower_bound=b['lower_bound'],
            gap=best['lifts']-b['lower_bound'] if best else None,
            relative_gap=(best['lifts']-b['lower_bound'])/best['lifts'] if best else None,
            c2_lifts=old['lifts'],v6_lifts=by_input[path]['v6_lifts'],
            evidence=best['evidence'] if best else None))
    report = dict(rungs=rungs,primary=primary,
        note='Rung rows use last supplied run for each map; primary uses best checked portfolio including C2/probes.')
    (HERE/'comparison.json').write_text(json.dumps(report,indent=2)+'\n')
    print(json.dumps(report,indent=2))


if __name__=='__main__':main()
