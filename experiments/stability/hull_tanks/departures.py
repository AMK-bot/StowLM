"""Synthetic homogeneous KCS departures with the new hull-following liquids.

ASSUMPTION: this diagnostic carries every ballast tank at 20% and every HFO
tank at 50%, with diesel/fresh water empty. Identical cargo masses are reduced
by the actual liquid mass to retain the preceding dry benchmark's 90%-design
ship displacement. Production hull, lightship and cell centres are unchanged.
This is not a voyage approval or a full-checker result. The finite-angle tank
oracle and its upstream section-quadrature assumptions remain authoritative.

Run only after compute admission: complete production builds and shaped-liquid
curves can take several minutes. The JSON is written after each stage so a
stopped run can resume without repeating a completed cold check. A warm stage
resumed in another process is explicitly distinguished from same-process warm.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
import hashlib
import importlib.util
import json
import os
from pathlib import Path
from time import perf_counter

from stowage.checker.stability import check_stability
from stowage.checker.stability_loading import loading_condition
from stowage.checker.stability_tank_envelope import max_fsm
from stowage.model import Instance
from stowage.versions import component_versions
from stowage.vessel.fleet import load_ship_params
from stowage.vessel.tanks import TankGeometry

ROOT = Path(__file__).resolve().parent
_spec = importlib.util.spec_from_file_location('hull_tank_kcs_departures',ROOT.parent/'kcs_departures.py')
_benchmark = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_benchmark)


def build_departure(name):
    """Revalidate the complete production instance after adding actual liquids."""
    instance,summary = _benchmark.build_departure(name)
    payload=instance.model_dump(mode='python')
    masses={tank.id: tank.capacity_m3*tank.content_density_kg_m3*
            (.2 if tank.kind=='ballast' else .5 if tank.kind=='fuel_oil' else 0.)
            for tank in instance.vessel.tanks}
    reduction=sum(masses.values())/len(instance.containers)
    for box in payload['containers']:
        box['gross_mass_kg']-=reduction
        if not 2200 <= box['gross_mass_kg'] <= 32000:
            raise ValueError(f"adjusted homogeneous box mass outside 2.2–32t: {box['gross_mass_kg']}")
    payload['id']=f'hull_tank_homogeneous_departure_{name}'
    payload['onboard']['tank_masses_kg']=masses
    instance=Instance.model_validate(payload)
    summary.update(condition='all holds + lowest two deck tiers; homogeneous 20ft boxes; ballast20%, HFO50%, other liquids0%',
        assumptions=['ASSUMPTION: synthetic diagnostic, not an approved voyage condition',
                     'ASSUMPTION: every ballast tank20%, every HFO tank50%, diesel and fresh water empty',
                     'ASSUMPTION: uniform box masses reduced to preserve90% upright design displacement',
                     'ASSUMPTION: consumables use the checker whole-usable-range free-surface/transfer policy'],
        gross_mass_per_box_kg=instance.containers[0].gross_mass_kg,
        liquid_mass_kg=sum(masses.values()),tank_masses_kg=masses)
    return instance,summary


def tank_summary(instance,params):
    """Record actual upright soundings separately from regulatory range maxima."""
    rows=[]
    records={record['id']:record for record in params['tanks']}
    for tank in instance.vessel.tanks:
        solid=TankGeometry.from_params(records[tank.id],ship_params=params)
        volume=instance.onboard.tank_masses_kg.get(tank.id,0.)/tank.content_density_kg_m3
        volume=min(volume,solid.capacity_m3)
        actual=solid.sounding(volume)
        row=dict(id=tank.id,kind=tank.kind,shape=solid.shape,capacity_m3=solid.capacity_m3,
            actual_volume_m3=volume,actual_fraction=volume/solid.capacity_m3,
            actual_centroid_m=[actual.lcg_m,actual.tcg_m,actual.vcg_m],
            actual_fsm_m4=actual.fsm_m4,actual_fsm_kgm=actual.fsm_m4*tank.content_density_kg_m3)
        if tank.kind=='fuel_oil':
            maximum,at_volume=max_fsm(solid)
            row.update(range_fsm_m4=maximum,range_fsm_kgm=maximum*tank.content_density_kg_m3,
                       range_fsm_representative_volume_m3=at_volume,
                       range_endpoint_note='volume may denote one-sided slack-fill supremum')
        rows.append(row)
    condition=loading_condition(instance,instance.onboard,ship_params=params)
    return dict(tanks=rows,loading_mass_kg=condition.mass_kg,
        loading_centroid_m=[condition.lcg_m,condition.tcg_m,condition.kg_m],
        correction_fsm_kgm=condition.fsm_kgm,correction_fsc_m=condition.fsc_m,
        loading_errors=list(condition.errors),loading_assumptions=list(condition.assumptions))


def _save(path,result):
    path.parent.mkdir(parents=True,exist_ok=True)
    temporary=path.with_name(path.name+'.partial')
    temporary.write_text(json.dumps(result,indent=2,allow_nan=False)+'\n')
    temporary.replace(path)


@contextmanager
def force_direct():
    previous=os.environ.get('STOWAGE_STABILITY_DIRECT')
    os.environ['STOWAGE_STABILITY_DIRECT']='1'
    try:
        yield
    finally:
        if previous is None:
            os.environ.pop('STOWAGE_STABILITY_DIRECT',None)
        else:
            os.environ['STOWAGE_STABILITY_DIRECT']=previous


def run_check(instance):
    start=perf_counter()
    try:
        report=check_stability(instance,instance.onboard,call_index=0)
        return dict(seconds=perf_counter()-start,ran=report.ran,error=report.error,
            metrics=report.metrics,margins=report.margins,
            violation_ids=sorted({violation.rule_id for violation in report.violations}),
            violations=[violation.model_dump(mode='json') for violation in report.violations])
    except Exception as exc:
        return dict(seconds=perf_counter()-start,ran=False,error=f'{type(exc).__name__}: {exc}',
                    metrics={},margins={},violation_ids=[])


def run(name,output,direct_reference=False):
    started=perf_counter()
    params=load_ship_params(name)
    fingerprint=hashlib.sha256(json.dumps(params,sort_keys=True,default=str).encode()).hexdigest()
    result=json.loads(output.read_text()) if output.exists() else {}
    if result and (result.get('vessel')!=name or result.get('parameters_sha256')!=fingerprint
                   or result.get('component_versions')!=component_versions()):
        raise ValueError('existing output provenance differs; select a fresh output path')
    if result.get('complete') and (not direct_reference or 'direct_reference' in result):
        return result
    instance,summary=build_departure(name)
    if not result:
        result=dict(summary,parameters_sha256=fingerprint,component_versions=component_versions(),
                    status='instance_validated',complete=False)
        _save(output,result)
    if 'tank_geometry' not in result:
        result['tank_geometry']=tank_summary(instance,params)
        result['status']='tank_geometry_recorded'
        _save(output,result)
    if abs(result['tank_geometry']['loading_mass_kg']-summary['target_displacement_kg'])>1e-6:
        raise ValueError('liquid/cargo mass adjustment did not preserve target displacement')
    cold_here='cold' not in result
    if cold_here:
        result['cold']=run_check(instance)
        result['status']='cold_complete'
        _save(output,result)
    if 'warm' not in result:
        result['warm']=run_check(instance)
        result['warm']['cache_context']='same process after cold' if cold_here else 'resumed process; liquid caches may be cold'
        result['status']='warm_complete'
        _save(output,result)
    if direct_reference and 'direct_reference' not in result:
        with force_direct():
            result['direct_reference']=run_check(instance)
        result['status']='direct_reference_complete'
        _save(output,result)
    result.update(complete=True,status='complete',this_process_seconds=perf_counter()-started)
    _save(output,result)
    return result


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--vessel',required=True,choices=_benchmark.FLEET)
    parser.add_argument('--output',required=True,type=Path)
    parser.add_argument('--direct-reference',action='store_true')
    arguments=parser.parse_args()
    result=run(arguments.vessel,arguments.output,arguments.direct_reference)
    print(json.dumps({key:result.get(key) for key in ('vessel','status','complete','this_process_seconds')},indent=2))


if __name__=='__main__':
    main()
