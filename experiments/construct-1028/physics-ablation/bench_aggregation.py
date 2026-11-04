"""One-core joint model build measurement, no MIP solve or acceptance."""
import json, os, resource, sys, time
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from cache_runtime import redirect_cache
redirect_cache(Path(__file__).resolve().parent/'aggregation-runtime')
from stowage.model import Instance
from stowage.solver.construct import _build_assignment_model

paths = sys.argv[1:] or [
 'experiments/solver-diag-1028/pilot-d58/generation/instances/random_yard/tiny/seed-110280000.json',
 'experiments/solver-diag-1028/pilot-d58/generation/instances/random_yard/small/seed-110380003.json']
results=[]
for path in paths:
    instance=Instance.model_validate_json(Path(path).read_text())
    start=time.monotonic()
    model=_build_assignment_model(instance,num_workers=1)
    seconds=time.monotonic()-start
    physics=model.physics
    proto=None
    # Counts rather than serialising the model: a binary protobuf dump itself
    # would temporarily duplicate its memory, obscuring the build measurement.
    cells=len(physics.cargo_mass_vars)
    row_count=len(physics.row_names)
    peak=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    peak_gb=peak/(1024**3 if sys.platform=='darwin' else 1024**2)
    result=dict(input=path,instance_id=instance.id,boxes=len(instance.load_list),
                binary_assignments=len(model.x),cargo_mass_cells=cells,
                total_variables=model.solver.NumVariables(),total_constraints=model.solver.NumConstraints(),
                physical_rows=row_count,global_row_cargo_term_upper_bound=cells*row_count,
                former_global_x_term_upper_bound=len(model.x)*row_count,
                build_s=seconds,process_peak_rss_gb=peak_gb)
    results.append(result)
    print(json.dumps(result),flush=True)
    Path('experiments/construct-1028/physics-ablation/aggregation-bench.json').write_text(json.dumps(results,indent=2))
