"""One-core assignment/source-row family feasibility screen (no acceptance)."""
import json
import time
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from cache_runtime import redirect_cache
redirect_cache(Path(__file__).resolve().parent)
from stowage.model import Instance
from stowage.solver.construct import _build_assignment_model
from stowage.solver.construct_physics import add_construct_physics

instance = Instance.model_validate_json(Path('experiments/solver-diag-1028/pilot-d58/generation/instances/random_yard/small/seed-110380001.json').read_text())
model = _build_assignment_model(instance, physics=False)
physics = add_construct_physics(model.solver, instance, model.x, mode="conservative", securing_proxy=False)
s = model.solver
saved = [(row,row.lb(),row.ub()) for row in s.constraints() if row.name().startswith('construct/')]

def family(name):
    name = name.rsplit('/',1)[-1]
    if name == 'gm': return 'gm'
    if name.startswith('list'): return 'list'
    if name == 'trim': return 'trim'
    if name.startswith(('sf@','bm@')): return 'strength'
    return 'mass_draft'

results = []
for label, keep in [('assignment',set()), ('gm',{'gm'}), ('list',{'list'}), ('trim',{'trim'}),
                     ('strength',{'strength'}), ('mass_draft',{'mass_draft'}),
                     ('all',{'gm','list','trim','strength','mass_draft'})]:
    for row, lo, hi in saved:
        row.SetBounds(lo if family(row.name()) in keep else -s.infinity(),
                      hi if family(row.name()) in keep else s.infinity())
    s.SetTimeLimit(10000)
    start = time.monotonic()
    status = s.Solve()
    result = dict(label=label,status=status,solve_s=time.monotonic()-start)
    if status in (s.FEASIBLE,s.OPTIMAL):
        result['ballast'] = physics.ballast_condition().tank_masses_kg
    results.append(result)
    print(json.dumps(result),flush=True)
    Path('experiments/construct-1028/physics-ablation/families.json').write_text(json.dumps(results,indent=2))
