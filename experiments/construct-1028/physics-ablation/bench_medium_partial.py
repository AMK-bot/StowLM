"""Record partial model resource use when source qualification fails closed."""
import json, resource, sys, time
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from cache_runtime import redirect_cache
redirect_cache(Path(__file__).resolve().parent/'aggregation-runtime')
from stowage.model import Instance
from stowage.solver.construct import _build_assignment_model
from stowage.solver.construct_physics import add_construct_physics
path=Path('experiments/construct-1028/necessary/epochs/generation-attempt1/generation/instances/simulated_yard/medium/seed-120482000.json')
instance=Instance.model_validate_json(path.read_text())
start=time.monotonic()
model=_build_assignment_model(instance,num_workers=1,physics=False)
error=None
try:
    physics=add_construct_physics(model.solver,instance,model.x)
except ValueError as exc:
    error=str(exc)
peak=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss/(1024**3 if sys.platform=='darwin' else 1024**2)
y=sum(variable.name().startswith('construct_cargo_mass@') for variable in model.solver.variables())
result=dict(input=str(path),instance_id=instance.id,boxes=len(instance.load_list),binary_assignments=len(model.x),
            cargo_mass_cells=y,total_variables=model.solver.NumVariables(),total_constraints=model.solver.NumConstraints(),
            source_error=error,qualified_physics_built=error is None,build_s=time.monotonic()-start,peak_rss_gb=peak,
            qualification_bypassed=False,accepted_stow=False)
Path('experiments/construct-1028/physics-ablation/aggregation-bench-medium-partial.json').write_text(json.dumps(result,indent=2))
print(json.dumps(result),flush=True)
