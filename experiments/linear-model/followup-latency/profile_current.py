"""TIMING ONLY: current hull/tanks/geometry/limits; no qualification.

The live public preparation/assembly path runs with real reference file discovery
and hashing. Parameter resolution is pinned to current complete fleet parameters;
the calibration oracle is seeded with old measurements in process memory. Old
KN/Bonjean rows are inherited solely to form a HullModel; neither is consumed.
The production epoch assertion is disabled locally, and the resulting policy is
explicitly marked production_usable=False. Nothing here qualifies current
physics or emits a usable solver model; no raw calibration is persisted.
"""
import cProfile
import argparse
from copy import deepcopy
from dataclasses import fields, replace
import json
from pathlib import Path
import resource
import time
from unittest.mock import patch

import numpy as np
from stowage.linear import cache, physics, family_calibration as cal, margin_policy as policy
from stowage.linear.model import LinearErrors, build_linear_model
from stowage.linear.bands import band_for_instance
from stowage.linear.reusable import CoefficientFamily, coefficient_family, assemble_instance, _FAMILY_MEMORY, _ARRAYS
from stowage.model import Vessel, ShipState
from stowage.model.fixtures import build_instance
from stowage.vessel.fleet import load_vessel,load_ship_params
from stowage.vessel.tanks import build_tanks
from stowage.vessel import departure_tank_masses
from stowage.checker.strength_limits import permissible_stations
from stowage.conventions import make_container_id

out=Path(__file__).parent
parser=argparse.ArgumentParser();parser.add_argument('--ship',default='feeder_500')
parser.add_argument('--booking-count',type=int,default=0)
parser.add_argument('--variants',type=int,default=10)
args=parser.parse_args()
key='f032e5a421dc33e59995aabbb047b48529d6aaca3137c14d95235ee2f9f0b5b9'
template=cache.load_model(key)
assert template is not None
parameters=cache.prepare_parameters(load_ship_params(args.ship))
selected=None
for path in sorted(Path('experiments/cache/vessels').glob('feeder_500.full.hull.*json')):
    raw=json.loads(path.read_bytes())
    if (len(raw['cells'])==len(template.cell_ids)
            and sorted(t['id'] for t in raw['tanks'])==list(template.tank_ids)
            and raw['hull']['lightship_mass_kg']==template.const_mass_kg):
        vessel=Vessel.model_validate(raw)
        if cache.vessel_physics(vessel)==cache.vessel_physics(Vessel.model_validate(raw)):
            selected=path; break
assert selected is not None
old_vessel=vessel
vessel=load_vessel(args.ship,with_hull=False)
updates={name:value for name,value in parameters['hull'].items()
         if name in type(old_vessel.hull).model_fields and name not in ('stations','hydrostatics','cross_curves','bonjean')}
updates.update(parameters.get('operating_limits',{}))
updates['fp_x_m']=parameters['hull'].get('fp_x_m',parameters['hull']['lpp_m'])
updates['max_draught_m']=parameters.get('operating_limits',{}).get('max_draught_m',parameters['hull']['scantling_draught_m'])
hull=old_vessel.hull.model_copy(update=updates)
hull=hull.model_copy(update={'stations':permissible_stations(parameters,hull)})
vessel=vessel.model_copy(update={'hull':hull,'tanks':build_tanks(parameters)})
base=build_instance()
base=base.model_copy(update={'vessel':vessel,'onboard':ShipState(vessel_id=vessel.id,tank_masses_kg=departure_tank_masses(vessel)),
    'port':base.port.model_copy(update={'max_draught_m':vessel.hull.max_draught_m})})
family=coefficient_family(base,parameters)
if args.booking_count:
    count=len(vessel.cells) if args.booking_count<0 else args.booking_count
    available=family.template.band.displacement_max_kg-vessel.hull.lightship_mass_kg
    mass=.2*available/count
    assert mass>1000.
    exemplar=base.containers[0]
    boxes=[exemplar.model_copy(update={'id':make_container_id('QZXU',index+1),
        'length_ft':20,'iso_type':'22G1','height_m':2.591,'tare_kg':1000.,
        'gross_mass_kg':mass,'special_stowage':[]}) for index in range(count)]
    base=base.model_copy(update={'containers':boxes,'load_list':[box.id for box in boxes],
                                'discharge_list':[]})
old=json.loads(Path('experiments/linear-model/signed-family/d30/fresh/feeder_500-light.json').read_bytes())['calibration']
for metric in ('sf_n','bm_nm'):
    old['max'][metric]=[max(old['max'][metric])]*len(family.template.station_x_m)
    for side in old['signed_errors'].values():
        if isinstance(side,dict) and metric in side:
            side[metric]=[max(side[metric])]*len(family.template.station_x_m)
errors=LinearErrors(**{k:np.asarray(v) if k in ('sf_n','bm_nm') else v
                      for k,v in old['max'].items() if k in ('gm_m','trim_m','list_rad','draught_m','sf_n','bm_nm')})
reference=cal.reference_inputs_cached(parameters)
domain=cal.domain_for(family,base)
calkey=cache.family_calibration_key(family,cal.reference_cache_identity(reference),domain,samples=192)
old.update(family_domain=domain,reference_identity=cal.reference_cache_identity(reference),
    reference_inputs=reference,response_calibration_identity=cache.response_calibration_identity(family),
    family_calibration_cache_key=calkey,coefficient_family_cache_key=family.cache_key)
cal._CALIBRATION_MEMORY[calkey]=(errors,old)
original_qualify=policy.qualify_policy
def timing_policy(*args,**kwargs):
    cert=original_qualify(*args,allow_reference_qualification=True)
    cert['production_usable']=False
    cert['sha256']=policy._digest({k:v for k,v in cert.items() if k!='sha256'})
    return cert
def forbidden(*args,**kwargs):raise AssertionError('timing harness attempted calibration')
variants=[]
for index in range(args.variants+1):
    loads=[(vessel.hull.lightship_lcg_m,0.,3.,100000.+10000.*index)]
    v=vessel.model_copy(update={'fixed_loads':loads})
    variants.append(base.model_copy(update={'id':f'timing-only-{index}','vessel':v}))
rows=[]
with patch.object(physics,'_params_for',lambda instance:('feeder_500',parameters)), \
     patch.object(policy,'assert_policy_epoch',lambda *a,**k:None), \
     patch.object(policy,'qualify_policy',timing_policy), \
     patch.object(cal,'calibrate_family',forbidden):
    warm=build_linear_model(variants[0],band=band_for_instance(variants[0]))
    for i,instance in enumerate(variants[1:]):
        physics._CACHE.clear()
        start=time.perf_counter();band=band_for_instance(instance);mid=time.perf_counter()
        model=build_linear_model(instance,band=band)
        rows.append({'index':i,'band_seconds':mid-start,'assembly_seconds':time.perf_counter()-mid,
                     'total_seconds':time.perf_counter()-start})
        assert np.isclose(model.const_mass_kg,physics._fixed(instance)[:,0].sum())
        assert model.measurement_diagnostics['margin_policy']['production_usable'] is False
    profile=cProfile.Profile();physics._CACHE.clear()
    profile.enable();build_linear_model(variants[-1],band=band_for_instance(variants[-1]));profile.disable()
    suffix='-booking' if args.booking_count else ''
    profile.dump_stats(str(out/(args.ship+'-current'+suffix+'.pstats')))
    # This recomputes coefficients, never the KN table or 192-point calibration.
    start=time.perf_counter()
    fresh=coefficient_family(variants[-1],parameters,force_rebuild=True)
    errors,diag=cal.calibrated_family(fresh,variants[-1])
    fresh_model=assemble_instance(fresh,variants[-1],band,errors=errors,diagnostics=diag)
    for field in fields(model):
        a=getattr(model,field.name);b=getattr(fresh_model,field.name)
        if isinstance(a,np.ndarray):assert np.allclose(a,b,rtol=1e-10,atol=1e-7),field.name
        elif field.name not in ('errors',):assert a==b,field.name
    equivalence_seconds=time.perf_counter()-start
result={'scope':'current geometry/tanks/lightship timing ONLY; historical UNUSED KN rows retained solely for fixture construction; not qualification; production_usable=False',
 'ship':args.ship,
 'booking_count':len(base.load_list),
 'historical_vessel_cache':str(selected),'cells':len(vessel.cells),'tanks':len(vessel.tanks),
 'parameter_canonical_bytes':len(cache._canonical(parameters)),'rows':rows,
 'max_seconds':max(row['total_seconds'] for row in rows),'mean_seconds':sum(row['total_seconds'] for row in rows)/len(rows),
 'fresh_coefficient_numerical_equivalence':True,'equivalence_seconds':equivalence_seconds,
 'reference_inputs':reference,'no_calibration_calls':True,
 'peak_rss_mib':resource.getrusage(resource.RUSAGE_SELF).ru_maxrss/1024**2}
(out/(args.ship+'-current'+suffix+'.json')).write_text(json.dumps(result,indent=2)+'\n')
print(json.dumps({k:v for k,v in result.items() if k not in ('reference_inputs','rows')}))
