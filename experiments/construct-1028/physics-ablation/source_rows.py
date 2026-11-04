"""Read source row reserves/bands; no weakened or omitted safety constraints."""
import json, sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from cache_runtime import redirect_cache
redirect_cache(Path(__file__).resolve().parent)
from stowage.model import Instance
from stowage.linear import build_linear_model, linear_constraints
from stowage.linear.margin_policy import default_reserves
from stowage.solver.construct_physics import _contexts

out=[]
base=Path('experiments/solver-diag-1028/pilot-d58/generation/instances/random_yard')
for size,seed in [('tiny',110280000),('small',110380001)]:
    instance=Instance.model_validate_json((base/size/f'seed-{seed}.json').read_text())
    for phase,context,_ in _contexts(instance):
        model=build_linear_model(context)
        rows=linear_constraints(model,direction='conservative')
        margins=default_reserves(model,direction='conservative')
        fields=('gm_m','trim_m','draught_m','draught_aft_m','draught_forward_m')
        row=dict(instance_id=instance.id, phase=phase, band={key:getattr(model.band,key) for key in ('displacement_min_kg','displacement_max_kg','draught_min_m','draught_max_m','max_abs_trim_m')},
                 reserves={side:{key:float(margins[side][key]) for key in fields} for side in ('lower_unsafe','upper_unsafe')},
                 loading_policy=model.measurement_diagnostics['loading_policy'],hull_draft_max=model.hull_max_draught_m,
                 rows=[dict(name=name,lower=float(rows.lb[i]),upper=float(rows.ub[i]),constant=float(rows.const[i])) for i,name in enumerate(rows.names) if name in ('trim','draught','draught_aft','draught_forward')])
        out.append(row)
Path('experiments/construct-1028/physics-ablation/source-rows.json').write_text(json.dumps(out,indent=2))
print(json.dumps(out,indent=2))
