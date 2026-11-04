"""Eight prescribed interior-mass faces; timing/evidence pilot only.

Requests are declared before any equilibrium: mass fraction .4, signed full
trim/list, dry-KG endpoints, empty liquids. No old holdout chooses a request
and no reserve or physical equation is changed.
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
ROOT=HERE.parents[3]
OUTPUT=HERE/'claude-face-pilot-artifacts'
if OUTPUT.exists(): raise ValueError('preserve existing face pilot evidence')
OUTPUT.mkdir()
BASE=HERE.parent/'edge_grid.py'

def pin(path):
    path=Path(path).resolve()
    return {'path':str(path.relative_to(ROOT)), 'sha256':hashlib.sha256(path.read_bytes()).hexdigest()}

def write(path,value):
    temporary=path.with_suffix(path.suffix+'.tmp')
    temporary.write_text(json.dumps(value,indent=2,allow_nan=False)+'\n')
    temporary.replace(path)

spec=importlib.util.spec_from_file_location('claude_face_immutable_base',BASE)
edge=importlib.util.module_from_spec(spec);spec.loader.exec_module(edge)
driver=edge.load_driver()
sources={name:pin(path) for name,path in {'base':BASE,'driver':driver.__file__,'pilot':__file__}.items()}
requests=[dict(state_id=f'face-pilot-{index:02d}',kind='interior-mass-face',
    mass_fraction=.4,trim_fraction=trim,heel_fraction=heel,dry_kg_fraction=kg,
    fill_fraction=0.,fill_pattern='uniform')
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
    setup_seconds=time.monotonic()-started
    write(OUTPUT/'prepared.json',dict(physical_inputs=before,band=asdict(family.template.band),
        domain=domain,station_x_m=family.template.station_x_m.tolist(),
        response_identity=diagnostics['response_calibration_identity'],setup_seconds=setup_seconds))
    for request in requests:
        observed=edge.evaluate(request,family,instance,geometry,domain)
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
        purpose='eight prescribed physical-domain faces, not an independent holdout or full checker certificate')
    write(OUTPUT/'summary.json',result)
    print(json.dumps({key:value for key,value in result.items() if key not in ('states','requests','sources','physical_inputs_start','physical_inputs_end')}),flush=True)
