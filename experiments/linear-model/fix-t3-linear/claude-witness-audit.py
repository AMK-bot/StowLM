"""Verify a frozen review point through public necessary rows after promotion.

The old empirical mean-error breach is asserted from immutable observations.
The physical equilibrium is recomputed, then every individually exact-feasible
row must be accepted by the current public necessary constraints. This does
not certify a complete departure or claim an independent holdout.
"""
import argparse
import hashlib
import importlib.util
import json
from pathlib import Path
import time

import numpy as np

HERE = Path(__file__).resolve().parent
parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('--fixture', type=Path, default=HERE/'claude-review-v2/feeder_500')
parser.add_argument('--output', type=Path, default=HERE/'claude-witness-audit.json')
args = parser.parse_args()
if args.output.exists(): parser.error('preserve existing audit; choose a new output')
from stowage.model import Instance
from stowage.linear import linear_constraints
from stowage.linear.margin_policy import qualify_policy, default_reserves
from stowage.linear.operations import public_loading_policy
from stowage.linear.reusable import coefficient_family, assemble_instance
from stowage.linear.physics import exact_condition, _linear_arrays
from stowage.linear.family_calibration import _validated_errors

started = time.monotonic()
review = json.loads((args.fixture/'necessary-review.json').read_text())
observed = review['states'][0]
instance = Instance.model_validate_json((args.fixture/'frozen-instance.json').read_text())
parameters = json.loads((args.fixture/'frozen-parameters.json').read_text())
numeric = json.loads((args.fixture/'frozen-numeric-model.json').read_text())
spec=importlib.util.spec_from_file_location('claude_witness_driver',HERE.parent/'epoch_requalification/driver.py')
driver=importlib.util.module_from_spec(spec);spec.loader.exec_module(driver)
before=driver.snapshot('feeder_500',parameters)
assert before==review['physical_inputs_start']==review['physical_inputs_end']
family = coefficient_family(instance, parameters)
errors = _validated_errors(numeric['errors'], len(family.template.station_x_m))
diagnostics = review['calibration']
certificate = qualify_policy(family,instance,diagnostics)
model = assemble_instance(family,instance,family.template.band,errors=errors,
    diagnostics=dict(diagnostics,margin_policy=certificate,loading_policy=public_loading_policy(instance)))
cells,tanks = (np.asarray(observed[key]) for key in ('cell_masses_kg','tank_masses_kg'))
exact = exact_condition(model,instance,cells,tanks,cell_vcg_m=np.asarray(observed['cell_vcg_m']))
affine = _linear_arrays(model,cells,tanks,instance=instance,solver=True)
for key in ('gm_m','trim_m','list_rad','draught_m','sf_n','bm_nm'):
    np.testing.assert_allclose(exact[key],observed['exact_'+key],rtol=1e-9,atol=1e-6)
old_excess = (observed['affine_draught_m']-observed['exact_draught_m']-
              review['old_reserves']['upper_unsafe']['draught_m'])
assert old_excess > 0., 'frozen reviewer point no longer demonstrates old mean-error breach'
current_reserves = default_reserves(model,direction='necessary')
mean_residual = affine['draught_m']-exact['draught_m']
assert current_reserves['upper_unsafe']['draught_m'] >= mean_residual
policy,band = public_loading_policy(instance),model.band
def inside(value,low,high): return bool(np.all(np.asarray(value)>=np.asarray(low)-1e-7) and np.all(np.asarray(value)<=np.asarray(high)+1e-7))
trim,draught=exact['trim_m'],exact['draught_m']
heel=min(model.max_list_rad,band.max_abs_heel_rad)
exact_rows = dict(gm=inside(exact['gm_m'],model.gm_min_m,np.inf),
    trim=inside(trim,max(policy['min_trim_m'],-band.max_abs_trim_m),min(policy['max_trim_m'],band.max_abs_trim_m)),
    list_port=inside(exact['list_rad'],-np.inf,heel),list_starboard=inside(exact['list_rad'],-heel,np.inf),
    draught=inside(draught,band.draught_min_m,band.draught_max_m),
    draught_aft=inside(draught+trim/2,-np.inf,model.hull_max_draught_m),
    draught_forward=inside(draught-trim/2,model.hull_min_draught_fwd_m,model.hull_max_draught_m),
    displacement=inside(exact['displacement_kg'],max(policy['min_displacement_kg'],band.displacement_min_kg),min(policy['max_displacement_kg'],band.displacement_max_kg)))
for i,x in enumerate(model.station_x_m):
    exact_rows[f'sf@{x:g}']=inside(exact['sf_n'][i],model.sf_min_n[i],model.sf_max_n[i])
    exact_rows[f'bm@{x:g}']=inside(exact['bm_nm'][i],model.bm_min_nm[i],model.bm_max_nm[i])
if model._cargo_mass_kg is not None:
    exact_rows['cargo_mass']=inside(cells.sum(),model._cargo_mass_kg,model._cargo_mass_kg)
for key,(low,high) in model._tank_mass_bounds_kg.items():
    exact_rows[f'tank_mass@{key}']=inside(tanks[model.tank_ids.index(key)],low,high)
table=linear_constraints(model,direction='necessary')
assert set(exact_rows)==set(table.names)
values=table.a_cell@cells+table.a_tank@tanks+table.const
accepted=(values>=table.lb-1e-7)&(values<=table.ub+1e-7)
failures=[name for name,passed in zip(table.names,accepted,strict=True) if exact_rows[name] and not passed]
assert not failures, failures
assert exact_rows['draught_aft'] and accepted[table.names.index('draught_aft')]
assert driver.snapshot('feeder_500',parameters)==before
result=dict(passed=True,old_mean_residual_excess_m=old_excess,current_mean_residual_m=mean_residual,
    current_mean_reserve_m=float(current_reserves['upper_unsafe']['draught_m']),
    exact_aft_m=draught+trim/2,exact_all=all(exact_rows.values()),
    individually_exact_pass_rows=sum(exact_rows.values()),necessary_rejects_exact_pass=failures,
    policy_sha256=certificate['sha256'],fixture_sha256=hashlib.sha256((args.fixture/'necessary-review.json').read_bytes()).hexdigest(),
    seconds=time.monotonic()-started,warning='Known training regression; individual row inclusion, not full-checker approval')
args.output.write_text(json.dumps(result,indent=2)+'\n')
print(json.dumps(result),flush=True)
