"""Fresh-process audit: reload stored finished plans and run the unchanged full checker."""
from pathlib import Path
import hashlib, json, sys
HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[5]
sys.path[:0] = [str(ROOT/'src')]
from stowage.checker import check
from stowage.io import load_instance
from stowage.model.plan import MovePlan
out = []
for name in ('D192.medium-feeder_500-f60-high-s192200000', 'D192.medium-feeder_800-f60-low-s192200101', 'sim.small.40100034'):
    d = json.loads((HERE/'results/quick20-1'/f'{name}.json').read_text())
    if not d.get('plan'):
        print(name, 'no plan'); continue
    i = load_instance(ROOT/d['input'])
    r = check(i, MovePlan.model_validate(d['plan']))
    row = dict(map=name, passed=bool(r.passed), lifts=float(r.count.weighted_nonproductive), loads=r.count.loads, boxes=len(i.load_list), violations=len(r.violations), sha=hashlib.sha256((ROOT/d['input']).read_bytes()).hexdigest()==d['input_sha256'])
    print(row, flush=True); out.append(row)
(HERE/'results/audit.json').write_text(json.dumps(out, indent=1))
