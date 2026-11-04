"""One-core no-acceptance ablation of conservative source rows and proxies."""
import json
import time
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from cache_runtime import redirect_cache
redirect_cache(Path(__file__).resolve().parent)
from stowage.model import Instance
from stowage.solver.construct import _build_assignment_model
from stowage.solver import construct_physics as p

path = Path('experiments/solver-diag-1028/pilot-d58/generation/instances/random_yard/small/seed-110380001.json')
instance = Instance.model_validate_json(path.read_text())
upper, forces = p._upper_design_gm, p._securing_envelopes
results = []
for name, upper_on, forces_on in [('source_only', False, False), ('upper_gm_only', True, False),
                                  ('force_only', False, True), ('both', True, True)]:
    start = time.monotonic()
    p._upper_design_gm = upper if upper_on else lambda *args: None
    p._securing_envelopes = forces if forces_on else lambda *args: None
    model = _build_assignment_model(instance, physics=False)
    physics = p.add_construct_physics(model.solver, instance, model.x, mode="conservative")
    build = time.monotonic()-start
    model.solver.SetTimeLimit(10000)
    status = model.solver.Solve()
    row = dict(name=name, instance_id=instance.id, status=status, build_s=build,
               total_s=time.monotonic()-start, variables=model.solver.NumVariables(),
               constraints=model.solver.NumConstraints())
    results.append(row)
    print(json.dumps(row), flush=True)
    Path('experiments/construct-1028/physics-ablation/results.json').write_text(json.dumps(results, indent=2))
p._upper_design_gm, p._securing_envelopes = upper, forces
