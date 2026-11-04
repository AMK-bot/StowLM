"""Fresh-process audit: reload stored finished plans and run the unchanged full checker on them."""
from pathlib import Path
import json, sys
ROOT = Path(__file__).resolve().parents[6]
sys.path[:0] = [str(ROOT/'src')]
from stowage.checker import check
from stowage.io import load_instance
from stowage.model.plan import MovePlan
out = []
for name in ('D192.medium-feeder_500-f60-high-s192200000', 'D192.medium-feeder_800-f60-low-s192200101'):
    d = json.loads((Path(__file__).parent/'results/quick-3'/f'{name}.json').read_text())
    i = load_instance(ROOT/d['input'])
    r = check(i, MovePlan.model_validate(d['plan']))
    row = dict(map=name, passed=bool(r.passed), lifts=float(r.count.weighted_nonproductive), loads=r.count.loads, boxes=len(i.load_list), violations=len(r.violations))
    print(row, flush=True); out.append(row)
(Path(__file__).parent/'results/audit.json').write_text(json.dumps(out, indent=1))
