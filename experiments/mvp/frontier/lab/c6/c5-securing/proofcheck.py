"""Controls for the capacity bound: its per-stack rows must hold on every plan the checker passed, at that plan's GM band."""
import json
from pathlib import Path
import sys

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from capacity import Capacity, ROOT, install, intervals, stack_witness
from alns import Problem
from chainlab import condition_terms
from stowage.checker import visibility
from stowage.checker.full import departure_condition
from stowage.model import Instance, MovePlan

install()
out = []
for path in sorted((HERE.parents[1]/'c5/c5-securing/results/frozen-48').glob('*.json')):
    row = json.loads(path.read_text())
    if not row.get('passed'):
        continue
    instance = Instance.model_validate_json((ROOT/row['input']).read_text())
    plan = MovePlan.model_validate(row['plan'])
    gm = visibility._loading_condition(instance, departure_condition(instance, plan)).gm_m
    band = next(b for b in intervals(instance.vessel.hull) if b[0] <= gm <= b[1])
    cap = Capacity(instance, Problem(instance), strict=True)
    worst = stack_witness(cap, band, row['assignment'])
    out.append(dict(instance=row['instance_id'], gm=gm, band=band, worst_row=worst))
    print(out[-1], flush=True)
    (HERE/'proof-controls.json').write_text(json.dumps(out, indent=1)+'\n')
print('all rows hold', all(r['worst_row'] <= 1+1e-9 for r in out), max(r['worst_row'] for r in out))
