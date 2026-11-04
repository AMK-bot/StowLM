"""Where the moves go: StowLM 8.2 vs two-step on the 9.1 test maps, by type (D174). Read-only; re-checks stored plans."""
import json, os, sys
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path
ROOT = Path(__file__).resolve().parents[3]
RUN = ROOT/'experiments/mvp/runs/test-91'

def parts(report):
    c = report.count
    future = list(c.future)
    get = lambda f, k: getattr(f, k) if not isinstance(f, dict) else f[k]
    return dict(relocations=c.relocations, restow_now=c.restow_lifts, hatch_now=c.hatch_cover_lifts,
                restow_later=sum(get(f, 'restow_lifts') for f in future),
                hatch_later=sum(get(f, 'hatch_cover_lifts') for f in future),
                total=c.weighted_nonproductive, passed=report.passed)

def one(iid):
    from stowage.checker import check
    from stowage.io import load_instance
    from stowage.model import PlannerResult
    summary = json.loads((RUN/'summary.json').read_text())
    tw = json.loads((RUN/'twostep-plans'/f'{iid}.json').read_text())
    inst = load_instance(ROOT/tw['row']['input'])
    out = dict(instance_id=iid, n_boxes=None)
    own = PlannerResult.model_validate(json.loads((ROOT/summary['chosen'][iid]).read_text()))
    out['stowlm'] = parts(check(inst, own.plan))
    two = PlannerResult.model_validate(tw['result'])
    out['twostep'] = parts(check(inst, two.plan))
    return out

if __name__ == '__main__':
    os.environ.setdefault('OMP_NUM_THREADS', '1')
    ids = sorted(p.stem for p in (RUN/'twostep-plans').glob('*.small.*.json'))
    summary = json.loads((RUN/'summary.json').read_text())
    ids = [i for i in ids if i in summary['chosen']]
    with ProcessPoolExecutor(6) as ex:
        rows = list(ex.map(one, ids))
    (Path(__file__).parent/'move_breakdown.json').write_text(json.dumps(rows, indent=1))
    keys = ['relocations', 'restow_now', 'hatch_now', 'restow_later', 'hatch_later', 'total']
    ok = [r for r in rows if r['stowlm']['passed'] and r['twostep']['passed']]
    print('maps', len(ok))
    for arm in ('stowlm', 'twostep'):
        print(arm, {k: round(sum(r[arm][k] for r in ok)/len(ok), 2) for k in keys})
    print('gap', {k: round(sum(r['stowlm'][k]-r['twostep'][k] for r in ok)/len(ok), 2) for k in keys})
