"""Historical row replay only: original arrays, observations and scheme-1 reserves.

No production epoch is accepted here. Saved affine values recover archived
instance constants; independently saved exact equilibria remain the row targets.
The live constraint algebra is cloned without its production-certificate lookup
and receives explicitly historical reserves. Production functions are untouched.
"""
from __future__ import annotations
import ast
from dataclasses import replace
from functools import lru_cache
import hashlib
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace as NS

import numpy as np

HERE=Path(__file__).resolve().parent
ROOT=next(p for p in HERE.parents if (p/'pyproject.toml').exists())
EVIDENCE=ROOT/'experiments/linear-model/epoch_requalification/production-1028b'
spec=importlib.util.spec_from_file_location('original_scheme1_payloads',HERE/'scheme1_payloads.py')
payloads=importlib.util.module_from_spec(spec);spec.loader.exec_module(payloads)
REPLAY_INPUTS_SHA256='603bd7a39e4ea01e082ba0d8d72e8994b79e230b3de5b2d002b09ccb9294eab7'
_PREPARED={}
_INPUTS={}
_PARAMETER_HASHES={}


def _ship(ship):
    return 'feeder_1000' if ship=='feeder_800' else ship


def _pins():
    data=(HERE/'replay-inputs.json').read_bytes()
    if hashlib.sha256(data).hexdigest()!=REPLAY_INPUTS_SHA256:
        raise ValueError('historical replay input manifest changed')
    return json.loads(data)['sources']


def read_source(path):
    path=Path(path).resolve();name=str(path.relative_to(ROOT));data=path.read_bytes()
    if hashlib.sha256(data).hexdigest()!=_pins().get(name):
        raise ValueError('historical replay input checksum mismatch: '+name)
    return json.loads(data)


def _loading(source):
    old=source['public_checker_comparison']['synthetic_reference_loading_policy']
    return dict(min_trim_m=old['iscode.loading.trim']['minimum'],
        max_trim_m=old['iscode.loading.trim']['maximum'],
        min_displacement_kg=old['iscode.loading.displacement']['minimum'],
        max_displacement_kg=old['iscode.loading.displacement']['maximum'])


def prepare(ship):
    ship=_ship(ship)
    if ship not in _PREPARED:
        from stowage.linear.model import LinearErrors
        template=payloads.load_archive_model(ship)
        raw,bank,identity=payloads.read_archive_inputs(ship)
        _INPUTS[ship]=(raw,bank,identity)
        diag=raw['diagnostics'];entry=bank['entries'][identity['response_identity']]
        light=read_source(EVIDENCE/'ordinary'/ship/'holdout-light.json')
        params=template._reference_params
        from stowage.linear.cache import _canonical
        _PARAMETER_HASHES[ship]=hashlib.sha256(_canonical(params)).hexdigest()
        limits=params['operating_limits'];hull=params['hull']
        instance=NS(vessel=NS(id=template.vessel_id,
            tanks=[NS(id=tid,adjustable=bool(template.tank_adjustable[i])) for i,tid in enumerate(template.tank_ids)],
            hull=NS(lpp_m=hull['lpp_m'],max_draught_m=limits.get('max_draught_m',hull['scantling_draught_m']),
                    min_draught_fwd_m=limits['min_draught_fwd_m'])),
            archived_loading_policy=_loading(light))
        errors=LinearErrors(**{k:np.asarray(v) if k in ('sf_n','bm_nm') else v for k,v in raw['errors'].items()})
        family=NS(template=template,parameters=params,cache_key=identity['coefficient_family_cache_key'])
        for name in ('response_sf','response_bm','buoyancy_sf','buoyancy_bm','lightship_sf','lightship_bm'):
            setattr(family,name,getattr(template,'_family_'+name))
        # This is a historical data view, deliberately not a policy certificate.
        historical=dict(historical_archive=identity,necessary=entry['necessary'])
        _PREPARED[ship]=(family,instance,errors,diag,raw['physical_inputs'],historical)
    return _PREPARED[ship]


def snapshot(ship,parameters):
    from stowage.linear.cache import _canonical
    raw,_,_=payloads.read_archive_inputs(_ship(ship))
    prepare(ship)
    if hashlib.sha256(_canonical(parameters)).hexdigest()!=_PARAMETER_HASHES[_ship(ship)]:
        raise ValueError('historical replay parameters differ from archived solid')
    return raw['physical_inputs']


@lru_cache(maxsize=6)
def _reserves(ship,direction):
    prepare(ship)
    raw,bank,identity=_INPUTS[ship]
    entry=bank['entries'][identity['response_identity']]
    necessary=direction=='necessary';record=entry['necessary'] if necessary else entry
    physical=record['physical_optimistic'] if necessary else record['physical_unsafe']
    signed=raw['diagnostics']['signed_errors'];transfer=record['reference_transfer']
    result={}
    for side in ('lower_unsafe','upper_unsafe'):
        observed=signed[side.replace('unsafe','conservatism')] if necessary else signed[side]
        result[side]={metric:1.1*(np.maximum(np.asarray(value),np.asarray(observed[metric]))
            +np.asarray(transfer[side][metric])) for metric,value in physical[side].items()}
    boundary=record['boundary_list']
    field='measured_optimistic_kgm' if necessary else 'measured_unsafe_kgm'
    result['list_moment_kgm']={side:1.1*(boundary[field][side]+boundary['reference_transfer_kgm'][side])
                             for side in ('lower_unsafe','upper_unsafe')}
    return result


def historical_reserves(model,*,direction):
    identity=model.measurement_diagnostics.get('historical_archive')
    if not identity or identity['historical_hash_scheme_version']!='1' or identity['production_usable'] is not False:
        raise ValueError('historical rows require an explicit nonproduction archive model')
    if identity!=_INPUTS[model._archive_ship][2]:
        raise ValueError('historical row identity differs from pinned archive')
    return _reserves(model._archive_ship,direction)


@lru_cache(maxsize=1)
def _row_algebra():
    from stowage.linear import operations
    path=Path(operations.__file__)
    node=next(n for n in ast.parse(path.read_bytes()).body if isinstance(n,ast.FunctionDef) and n.name=='constraints')
    imports=[n for n in node.body if isinstance(n,ast.ImportFrom) and n.module=='margin_policy']
    if len(imports)!=1 or [n.name for n in imports[0].names]!=['default_reserves']:
        raise ValueError('constraint certificate import changed; review archival adapter')
    node.body.remove(imports[0])
    namespace=dict(vars(operations),default_reserves=historical_reserves)
    exec(compile(ast.Module(body=[node],type_ignores=[]),str(path),'exec'),namespace)
    return namespace['constraints']


def constraints(model,*,direction,margin_scale=1.):
    return _row_algebra()(model,margin_scale,direction=direction)


def _model(prepared,observed,band,cell_z,constraint_z,cargo_mass,loading):
    from stowage.linear.physics import _linear_arrays
    family,instance,errors,diag,_,historical=prepared;template=family.template
    c=np.asarray(observed['cell_masses_kg']);t=np.asarray(observed['tank_masses_kg'])
    mass=float(observed['mass_kg']);restoring=float(observed['affine_restoring_kgm'])
    transverse=float(observed['transverse_moment_kgm'])
    a,b=template.km_line;c0,cx,cd=template.trim_line
    if cx==0:raise ValueError('archived trim model cannot identify longitudinal moment')
    const_mass=float(mass-sum(c)-sum(t))
    mx=float((observed['affine_trim_m']-c0-cd*mass)/cx-template.cell_lcg_m@c-template.tank_lcg_m@t)
    my=float(transverse-template.cell_tcg_m@c-template.tank_tcg_m@t)
    mz=float(a+b*mass-sum(template.tank_fsm_max_kgm)-restoring-constraint_z@c-template.tank_vcg_m@t)
    model=replace(template,band=band,errors=errors,cell_vcg_m=np.asarray(cell_z),
        const_mass_kg=const_mass,const_moment_x_kgm=mx,const_moment_y_kgm=my,const_moment_z_kgm=mz,
        const_sf_n=np.asarray(observed['affine_sf_n'])-template.sf_per_kg_cell@c-template.sf_per_kg_tank@t,
        const_bm_nm=np.asarray(observed['affine_bm_nm'])-template.bm_per_kg_cell@c-template.bm_per_kg_tank@t)
    metadata=dict(measurement_diagnostics=dict(diag,historical_archive=historical['historical_archive'],loading_policy=loading),
        _archive_ship=_ship(template.vessel_id),_constraint_cell_vcg_m=np.asarray(constraint_z),
        _cargo_mass_kg=cargo_mass,_tank_mass_bounds_kg=band._tank_mass_bounds_kg,
        hull_length_m=instance.vessel.hull.lpp_m,hull_max_draught_m=instance.vessel.hull.max_draught_m,
        hull_min_draught_fwd_m=instance.vessel.hull.min_draught_fwd_m)
    for name,value in metadata.items():object.__setattr__(model,name,value)
    _check_affine(model,observed)
    return model


def _check_affine(model,observed):
    from stowage.linear.physics import _linear_arrays
    c=np.asarray(observed['cell_masses_kg']);t=np.asarray(observed['tank_masses_kg'])
    got=_linear_arrays(model,c,t,solver=True)
    # Only floating-point arithmetic is inverted, never a fitted regression.
    # Independent retained affine predictions must match every reconstructed
    # quantity. These checks never consume or modify an exact observation.
    for key in ('gm_m','trim_m','draught_m','sf_n','bm_nm','list_rad'):
        if 'affine_'+key in observed:
            np.testing.assert_allclose(got[key],observed['affine_'+key],rtol=64*np.finfo(float).eps,atol=1e-7)
    mass=model.const_mass_kg+sum(c)+sum(t)
    np.testing.assert_allclose(mass,observed['mass_kg'],rtol=8*np.finfo(float).eps,atol=1e-7)
    np.testing.assert_allclose(mass*got['gm_m'],observed['affine_restoring_kgm'],rtol=64*np.finfo(float).eps,atol=1e-7)
    transverse=model.const_moment_y_kgm+model.cell_tcg_m@c+model.tank_tcg_m@t
    np.testing.assert_allclose(transverse,observed['transverse_moment_kgm'],rtol=64*np.finfo(float).eps,atol=1e-7)


def edge_model(prepared,observed):
    family,instance,_,diag,_,_=prepared;point=observed['fixed_point'];domain=diag['family_domain']
    if point['mass_kg']>0 and not (domain['kg_min_m']<=point['z_m']<=domain['kg_max_m']
            and domain['fixed_x_min_m']<=point['x_m']<=domain['fixed_x_max_m']
            and abs(point['y_m'])<=domain['fixed_y_abs_max_m']):
        raise ValueError('positive fixed load centroid is outside the family calibration domain')
    return _model(prepared,observed,family.template.band,family.template.cell_vcg_m,
        family.template.cell_vcg_m,0.,instance.archived_loading_policy)


def _ordinary_arms(template,manifest):
    std=manifest['standard_20ft_count'];hc=manifest['hc_40ft_count']
    arms={};upper={}
    for stack in template._reference_params['stacks']:
        count=0 if stack['on_deck'] and manifest['hc_under_deck_only'] else hc
        heights=sorted([2.896]*(2*count)+[2.591]*std,reverse=True)
        if not heights:raise ValueError('archived manifest has no eligible cargo heights')
        mean=sum(heights)/len(heights);largest=heights[0];smallest=heights[-1]
        for j,cid in enumerate(stack['cell_ids']):
            arms[cid]=stack['base_z_m']+(j+.5)*mean
            support=sum(heights[:j])+max(0,j-len(heights))*largest
            upper[cid]=stack['base_z_m']+min(support+largest/2,stack['max_height_m']-smallest/2)
    return np.array([arms[cid] for cid in template.cell_ids]),np.array([upper[cid] for cid in template.cell_ids])


def ordinary_model(ship,level,source=None):
    from stowage.linear.model import ValidityBand
    path=EVIDENCE/'ordinary'/_ship(ship)/f'holdout-{level}.json'
    saved=read_source(path)
    if source is not None and source!=saved:raise ValueError('ordinary source differs from pinned archive')
    prepared=prepare(ship);template=prepared[0].template
    arms,upper=_ordinary_arms(template,saved['manifest'])
    band=ValidityBand(**saved['band'])
    model=_model(prepared,saved['list_row_samples'][0],band,arms,upper,
                 saved['manifest']['cargo_mass_kg'],_loading(saved))
    for observed in saved['list_row_samples']:_check_affine(model,observed)
    return model
