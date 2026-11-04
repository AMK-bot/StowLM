"""Eight prescribed interior-mass faces with an explicit feasible tank budget.

Requests are declared before any equilibrium: mass fraction .4, signed full
trim/list, dry-KG endpoints, tank mass requested at .8 of available mass and
distributed by actual tank mass capacities. There is no full-fill clipping,
held-out fit, reserve modification or change to any physical equation.
"""
from dataclasses import asdict
import hashlib
import importlib.util
from itertools import product
import json
from pathlib import Path
import resource
import signal
import time

import numpy as np

HERE=Path(__file__).resolve().parent
ROOT=HERE.parents[4]
OUTPUT=HERE/'claude-face-budget-pilot-artifacts'
if OUTPUT.exists(): raise ValueError('preserve existing face pilot evidence')
OUTPUT.mkdir()
BASE=HERE.parent.parent/'edge_grid.py'

def pin(path):
    path=Path(path).resolve()
    return {'path':str(path.relative_to(ROOT)), 'sha256':hashlib.sha256(path.read_bytes()).hexdigest()}

def write(path,value):
    temporary=path.with_suffix(path.suffix+'.tmp')
    temporary.write_text(json.dumps(value,indent=2,allow_nan=False)+'\n')
    temporary.replace(path)

spec=importlib.util.spec_from_file_location('claude_face_budget_immutable_base',BASE)
edge=importlib.util.module_from_spec(spec);spec.loader.exec_module(edge)
driver=edge.load_driver()
sources={name:pin(path) for name,path in {'base':BASE,'driver':driver.__file__,'pilot':__file__}.items()}
requests=[dict(state_id=f'face-budget-pilot-{index:02d}',kind='interior-mass-face',
    mass_fraction=.4,trim_fraction=trim,heel_fraction=heel,dry_kg_fraction=kg,
    requested_available_mass_fraction=.8,
    fill_pattern='explicit-total-mass-budget-uniform-by-mass-capacity')
    for index,(trim,heel,kg) in enumerate(product((-1.,1.),(-1.,1.),(0.,1.)))]
write(OUTPUT/'admission.json',dict(requests=requests,sources=sources,
    cpu=1,ram_budget_gb=2,gpu=False,estimate_wall_s=90,
    design='prespecified eight faces; no heldout fitting or affine filtering'))
started=time.monotonic();states=[];setup_seconds=None;before=after=None;status='running'
def timeout(*_): raise TimeoutError('face pilot reached declared90s budget')
signal.signal(signal.SIGALRM,timeout);signal.setitimer(signal.ITIMER_REAL,90.)
try:
    family,instance,_,diagnostics,before=driver.prepare('feeder_1700',HERE/'ordinary')
    from stowage.checker.stability_geometry import StabilityGeometry
    from stowage.linear.family_calibration import domain_for
    geometry=StabilityGeometry(family.parameters)
    domain=domain_for(family,instance)
    requested_mass=(family.template.band.displacement_min_kg+.4*(
        family.template.band.displacement_max_kg-family.template.band.displacement_min_kg))
    available=requested_mass-instance.vessel.hull.lightship_mass_kg
    tank_capacity=float(np.sum(family.template.tank_capacity_kg))
    requested_total_tank_mass=.8*available
    requested_fill=requested_total_tank_mass/tank_capacity
    for request in requests:
        request.update(requested_total_mass_kg=requested_mass,
            requested_available_mass_kg=available,
            requested_total_tank_mass_kg=requested_total_tank_mass,
            total_tank_capacity_kg=tank_capacity,
            explicit_uniform_fill_fraction=requested_fill)
    # This experiment-local override selects physical input fills only. It
    # never clips them or alters any exact root, solver or admission criterion.
    edge.tank_fractions=lambda request,count:np.full(count,request['explicit_uniform_fill_fraction'])
    write(OUTPUT/'explicit-requests.json',dict(requests=requests,
        tank_ids=list(family.template.tank_ids),
        tank_capacity_kg=family.template.tank_capacity_kg.tolist()))
    setup_seconds=time.monotonic()-started
    write(OUTPUT/'prepared.json',dict(physical_inputs=before,band=asdict(family.template.band),
        domain=domain,station_x_m=family.template.station_x_m.tolist(),
        response_identity=diagnostics['response_calibration_identity'],setup_seconds=setup_seconds))
    for request in requests:
        observed=edge.evaluate(request,family,instance,geometry,domain)
        actual=np.asarray(observed.get('all_tank_masses_kg',[]))
        if actual.size:
            actual_total=float(actual.sum())
            if not np.isclose(actual_total,requested_total_tank_mass,rtol=1e-12,atol=1e-6):
                raise ValueError('sampler changed the explicitly requested tank mass')
            observed['actual_total_tank_mass_kg']=actual_total
            observed['actual_tank_fill_fractions']=(actual/family.template.tank_capacity_kg).tolist()
            observed['actual_available_mass_fraction']=actual_total/available
        if observed.get('admitted'):
            observed['necessary_lower_trim_residual_m']=max(0.,observed['exact_trim_m']-observed['affine_trim_m'])
            observed['necessary_lower_aft_residual_m']=max(0.,
                observed['exact_draught_m']+.5*observed['exact_trim_m']-
                observed['affine_draught_m']-.5*observed['affine_trim_m'])
        states.append(observed)
        write(OUTPUT/(request['state_id']+'.json'),observed)
        print(json.dumps({'state_id':request['state_id'],'admitted':observed['admitted'],
            'seconds':observed['seconds'],
            'lower_trim_residual_m':observed.get('necessary_lower_trim_residual_m'),
            'lower_aft_residual_m':observed.get('necessary_lower_aft_residual_m'),
            'rejection_reason':observed.get('rejection_reason')}),flush=True)
    after=driver.snapshot('feeder_1700',family.parameters)
    if after!=before or any(pin(ROOT/record['path'])!=record for record in sources.values()):
        raise ValueError('face pilot numerical or sampler sources changed')
    status='completed'
except TimeoutError as error:
    status=str(error)
finally:
    signal.setitimer(signal.ITIMER_REAL,0.)
    admitted=[state for state in states if state.get('admitted')]
    result=dict(status=status,requested=len(requests),processed=len(states),admitted=len(admitted),
        requests=requests,states=states,sources=sources,physical_inputs_start=before,
        physical_inputs_end=after,physical_inputs_unchanged=before is not None and before==after,
        setup_seconds=setup_seconds,wall_seconds=time.monotonic()-started,
        peak_rss_bytes=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
        max_necessary_lower_trim_residual_m=max((state['necessary_lower_trim_residual_m'] for state in admitted),default=None),
        max_necessary_lower_aft_residual_m=max((state['necessary_lower_aft_residual_m'] for state in admitted),default=None),
        tank_budget=(None if setup_seconds is None else dict(requested_total_mass_kg=requested_mass,
            lightship_mass_kg=instance.vessel.hull.lightship_mass_kg,available_mass_kg=available,
            requested_available_mass_fraction=.8,total_tank_capacity_kg=tank_capacity,
            requested_total_tank_mass_kg=requested_total_tank_mass,actual_uniform_fill_fraction=requested_fill)),
        purpose='eight prescribed physical-domain budgeted faces, not an independent holdout or full checker certificate')
    write(OUTPUT/'summary.json',result)
    print(json.dumps({key:value for key,value in result.items() if key not in ('states','requests','sources','physical_inputs_start','physical_inputs_end')}),flush=True)
