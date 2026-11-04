"""Measure hull/tank response errors independently of a booking's fixed loads.

For point cargo and background weights, the exact weight curve cancels from
the SF/BM residual. Only total mass, three mass moments, and the liquid weight
distribution remain. Synthetic point loads therefore span those state variables
without assuming any particular background booking. Their positions need not
be feasible container slots: this is calibration of the response, not a plan.
Instance assembly separately bounds physical box heights in each column.

The resulting directional maxima are empirical, not a proof over the continuous
domain. Both signs are retained. Source: Biran & López-Pulido (2014), ch. 2–4,
weight/buoyancy superposition and mass-moment equilibrium.
"""
from __future__ import annotations

from dataclasses import replace
from copy import deepcopy
import hashlib
import os
import json
from functools import lru_cache

import numpy as np

from .model import LinearErrors

_REFERENCE_EPOCH=None
_CALIBRATION_MEMORY={}
_DOMAIN_MEMORY={}
_REFERENCE_INPUTS_MEMORY={}


def synchronize_reference(snapshot):
    """Invalidate checker memos when external qualification artifacts change.

    The checker normally qualifies tables in its own process. Our calibration
    process may already have memoized a missing/older table before another
    process replaces it; a new disk key must never bind that stale reference.
    """
    global _REFERENCE_EPOCH
    epoch=hashlib.sha256(repr(snapshot).encode()).hexdigest()
    if epoch!=_REFERENCE_EPOCH:
        from stowage.checker.stability_liquid import _read_manifest
        from stowage.checker.stability_liquid_tables import _lookup
        _read_manifest.cache_clear()
        _lookup.cache_clear()
        _REFERENCE_EPOCH=epoch


@lru_cache(maxsize=8)
def _reference_solids(canonical_parameters, dependency):
    """Memoize geometry construction; paths/bytes are still checked every call."""
    from stowage.vessel.tanks import TankGeometry
    parameters=json.loads(canonical_parameters)
    return tuple((record['id'],TankGeometry.from_params(record,ship_params=parameters))
                 for record in parameters.get('tanks',[]))


def _reference_paths(canonical_parameters, dependency):
    from stowage.checker.stability_liquid import manifest_path
    from stowage.checker.stability_liquid_tables import _path
    parameters=json.loads(canonical_parameters)
    files=[manifest_path(parameters)]
    for _,solid in _reference_solids(canonical_parameters,dependency):
        if getattr(solid,'shape',None)=='hull_sections':
            path=_path(solid)
            files.extend((path,path.with_suffix('.qualified.json')))
    return tuple(files)


_reference_paths.cache_clear=_reference_solids.cache_clear

_DEFAULT_REFERENCE_PATHS = _reference_paths


@lru_cache(maxsize=16)
def _reference_manifest_path(canonical_parameters, dependency, resolver, runtime_identity):
    # The manifest name depends only on the physical inputs and implementation;
    # its bytes, existence, and alternative liquid grids are checked afresh.
    return resolver(json.loads(canonical_parameters))


def _prepared_reference_paths(canonical_parameters, dependency):
    """Avoid repeatedly projecting large parameters to a manifest filename."""
    if _reference_paths is not _DEFAULT_REFERENCE_PATHS:
        return _reference_paths(canonical_parameters, dependency)
    from stowage.checker import stability_liquid, stability_liquid_tables
    manifest_path=stability_liquid.manifest_path
    _path=stability_liquid_tables._path
    runtime=(str(stability_liquid.CACHE_DIRECTORY),
             stability_liquid.STABILITY_LIQUID_MANIFEST_VERSION,
             stability_liquid_tables.STABILITY_LIQUID_TABLES_VERSION)
    files=[_reference_manifest_path(canonical_parameters,dependency,manifest_path,runtime)]
    for _,solid in _reference_solids(canonical_parameters,dependency):
        if getattr(solid,'shape',None)=='hull_sections':
            path=_path(solid)
            files.extend((path,path.with_suffix('.qualified.json')))
    return tuple(files)


def reference_cache_identity(snapshot):
    """Numerical dispatch identity, excluding certificate dates and audit paths."""
    return {key:value for key,value in snapshot.items() if key!='files'}


def _liquid_numeric_digest(table):
    digest=hashlib.sha256()
    for name in ('fills','heels','trims','moments','capacity','empty_centroid','prismatic'):
        value=np.asarray(getattr(table,name),dtype='<f8')
        digest.update(name.encode());digest.update(repr(value.shape).encode());digest.update(value.tobytes())
    return digest.hexdigest()


def reference_inputs(parameters):
    """Bind actual qualified numerical dispatch and recheck all asset bytes.

    A qualification date or an unrelated global version is not a numerical
    dependency. Available grids and qualification state are: a missing/changed
    grid must never reuse a calibration made against a different predictor.
    Full file SHA values remain audit evidence, separate from the numerical key.
    """
    from .cache import _canonical
    from .dependencies import dependency_fingerprint
    method=int(os.environ.get('STOWAGE_STABILITY_FREE_SURFACE_METHOD','1'))
    direct=os.environ.get('STOWAGE_STABILITY_LIQUID_DIRECT')=='1'
    if method not in (1,2):raise ValueError('free-surface method must be 1 or 2')
    if direct or method==2:
        return {'method':method,'direct':direct,'files':{}}
    from stowage.checker import stability_liquid as liquid
    from stowage.checker import stability_liquid_tables as tables
    canonical=_canonical(parameters)
    dependency=dependency_fingerprint('liquid_reference')
    records=_reference_solids(canonical,dependency)
    paths=_reference_paths(canonical,dependency)
    files={str(path):hashlib.sha256(path.read_bytes()).hexdigest() if path.exists() else None
           for path in paths}
    synchronize_reference({'method':method,'direct':direct,'files':files})
    try:
        manifest=json.loads(paths[0].read_text())
        manifest_ok=(manifest['metadata']==liquid.metadata(parameters)
                     and liquid._valid_statistics(manifest['validation'])
                     and isinstance(manifest['tank_table_keys'],dict))
    except (OSError,ValueError,KeyError,TypeError):
        manifest={};manifest_ok=False
    dispatch=[]
    for tid,solid in records:
        if getattr(solid,'shape',None)!='hull_sections':continue
        table=tables.lookup(solid) if manifest_ok else None
        if table is not None and manifest['tank_table_keys'].get(tid)!=liquid._table_identity(solid,liquid.metadata(parameters)):
            table=None
        dispatch.append({'tank_id':tid,'qualified':table is not None,
                         'numeric_sha256':_liquid_numeric_digest(table) if table is not None else None})
    qualified=not dispatch or (manifest_ok and all(record['qualified'] for record in dispatch))
    return {'method':method,'direct':direct,'qualified':bool(qualified),
            'dispatch':dispatch,'files':files}


def reference_inputs_cached(parameters):
    """Reuse dispatch qualification only while every content input is unchanged.

    Resolve candidate paths and hash actual asset bytes on every call. Directory
    selection therefore remains fresh, even when another process adds a newer
    grid. This memo removes repeated parameter projection/qualification work;
    it never substitutes file statistics for content validation.
    """
    from .cache import _canonical
    from .dependencies import dependency_fingerprint
    method=int(os.environ.get('STOWAGE_STABILITY_FREE_SURFACE_METHOD','1'))
    direct=os.environ.get('STOWAGE_STABILITY_LIQUID_DIRECT')=='1'
    if method not in (1,2):raise ValueError('free-surface method must be 1 or 2')
    canonical=_canonical(parameters)
    dependency=dependency_fingerprint('liquid_reference')
    def assets():
        paths=() if direct or method==2 else _prepared_reference_paths(canonical,dependency)
        return {str(path):hashlib.sha256(path.read_bytes()).hexdigest()
                if path.exists() else None for path in paths}
    files=assets()
    key=(canonical,dependency,method,direct,id(reference_inputs),tuple(sorted(files.items())))
    cached=_REFERENCE_INPUTS_MEMORY.get(key)
    if cached is not None and cached[0] is reference_inputs:
        snapshot=deepcopy(cached[1])
        synchronize_reference(snapshot)
        return snapshot
    snapshot=reference_inputs(parameters)
    if snapshot.get('files')!=files or assets()!=files:
        raise ValueError('liquid-transfer assets changed during reference qualification; retry')
    synchronize_reference(snapshot)
    if len(_REFERENCE_INPUTS_MEMORY)>=16:
        _REFERENCE_INPUTS_MEMORY.pop(next(iter(_REFERENCE_INPUTS_MEMORY)))
    # Keep the callable alive as well as its id: monkeypatching cannot reuse a
    # memo through a recycled Python object address.
    _REFERENCE_INPUTS_MEMORY[key]=(reference_inputs,deepcopy(snapshot))
    return deepcopy(snapshot)


@lru_cache(maxsize=8)
def _parent_domain_bounds(name, parameter_bytes, fleet_sources):
    from stowage.vessel.fleet import load_vessel
    parent=load_vessel(name,with_hull=False)
    return (max([0.,*(stack.base_z_m+stack.max_height_m for stack in parent.stacks)]),
            min([0.,*(cell.lcg_m for cell in parent.cells)]),
            max([0.,*(cell.lcg_m for cell in parent.cells)]))


def domain_for(family, instance):
    """Compute the domain from actual limits, not a layout-free family key."""
    parent=instance.vessel
    parent_top=0.;parent_min=0.;parent_max=0.
    if getattr(parent,'slice_of',None):
        from pathlib import Path
        from .physics import _params_for
        from stowage.vessel import fleet
        name,_=_params_for(instance)
        # This is only a cheap parent-layout memo key, never a persistent physics
        # identity. Raw bytes/source changes rebuild bounds; resulting numeric
        # domain values alone enter calibration keys.
        files=sorted(Path(fleet.__file__).parent.glob('fleet*.py'))
        sources=tuple(hashlib.sha256(path.read_bytes()).digest() for path in files)
        parent_top,parent_min,parent_max=_parent_domain_bounds(name,fleet._parameter_bytes(name),sources)
    hull=parent.hull
    maximum=max([parent_top,hull.depth_m,hull.lightship_vcg_m,
                 *(stack.base_z_m+stack.max_height_m for stack in parent.stacks),
                 *(row.vcg_m for tank in parent.tanks for row in tank.table)])
    return {'kg_min_m':0.,'kg_max_m':float(maximum),
            'fixed_x_min_m':float(min(parent_min,0.,*(cell.lcg_m for cell in parent.cells))),
            'fixed_x_max_m':float(max(parent_max,hull.lpp_m,*(cell.lcg_m for cell in parent.cells))),
            'fixed_y_abs_max_m':float(hull.beam_m/2),
            'point_weight_cancellation':True,
            'height_policy':'per-instance conservative physical-cell upper arms'}


def calibrate_family(family, instance, *, samples=192, seed=3701):
    """Vary all liquid fills and total moments; never key on existing loads.

    Each synthetic dry load makes the requested *linear* trim/list/KG. Exact
    equilibrium is then solved with the actual tanks. Only states inside the
    declared geometric band are accepted; rejection counts and coverage remain
    visible. Fixed consumable fills vary as well as adjustable ballast fills.
    """
    from stowage.model import FixedLoad, ShipState
    from .physics import exact_condition,_linear_arrays,_geometry_from_params,_tank_point
    from .reusable import assemble_instance
    from .signed_errors import solver_signed_statistics
    base=family.template; hull=instance.vessel.hull; band=base.band
    domain=domain_for(family,instance)
    rng=np.random.default_rng(seed)
    metrics=('gm_m','trim_m','list_rad','draught_m','sf_n','bm_nm')
    absolute={key:[] for key in metrics}
    signed={key:[] for key in (*metrics,'draught_aft_m','draught_forward_m')}
    coverage={key:[] for key in ('displacement_kg','trim_m','list_rad','draught_m','kg_m')}
    usage={'table':0,'direct':0};attempts=0;last_failure='no accepted state'
    affine_gms=[];exact_heels=[]
    geometry=_geometry_from_params(family.parameters)
    tanks=sorted(instance.vessel.tanks,key=lambda tank:tank.id)
    empty=instance.model_copy(update={'containers':[],'load_list':[],'discharge_list':[],
        'onboard':ShipState(vessel_id=instance.vessel.id)})
    while len(absolute['gm_m'])<samples and attempts<max(400,samples*60):
        attempts+=1
        mass=float(rng.uniform(band.displacement_min_kg,band.displacement_max_kg))
        available=mass-hull.lightship_mass_kg
        if available<=1.:continue
        fills=rng.random(len(tanks))
        if attempts%8==0:fills[:]=0.
        elif attempts%9==0:fills[:]=1.
        values=fills*base.tank_capacity_kg
        if values.sum()>=available*.98:
            values*=float(rng.uniform(.1,.98))*available/max(float(values.sum()),1.)
        dry=available-float(values.sum())
        # Including negative-GM targets is deliberate: unsafe-side envelopes
        # must not be calibrated only on conditions already judged feasible.
        kg=float(rng.uniform(domain['kg_min_m'],domain['kg_max_m']))
        target_trim=float(rng.uniform(-.9,.9)*band.max_abs_trim_m)
        target_heel=float(rng.uniform(-.9,.9)*band.max_abs_heel_rad)
        c0,cx,cd=base.trim_line
        mx=(target_trim-c0-cd*mass)/cx
        gm=(base.km_line[0]+base.km_line[1]*mass)/mass-kg-base.tank_fsm_max_kgm.sum()/mass
        my=float(np.tan(target_heel)*gm*mass)
        mz=kg*mass
        reference_arms=np.array([base.tank_lcg_m,base.tank_tcg_m,base.tank_vcg_m]).T.copy()
        for i,tank in enumerate(tanks):
            if not tank.adjustable:reference_arms[i]=_tank_point(tank,values[i])
        moments=np.array([mx,my,mz])-values@reference_arms-hull.lightship_mass_kg*np.array(
            [hull.lightship_lcg_m,hull.lightship_tcg_m,hull.lightship_vcg_m])
        point=FixedLoad(x_m=float(moments[0]/dry),y_m=float(moments[1]/dry),
                        z_m=float(moments[2]/dry),mass_kg=dry)
        state=empty.model_copy(update={'vessel':empty.vessel.model_copy(update={'fixed_loads':[point]}),
            'onboard':ShipState(vessel_id=empty.vessel.id,tank_masses_kg={tank.id:float(values[i])
                              for i,tank in enumerate(tanks) if not tank.adjustable})})
        current=assemble_instance(family,state,band,errors=base.errors,
                                  diagnostics={'calibration_synthetic':True})
        cell=np.zeros(len(base.cell_ids));adjustable=values*base.tank_adjustable
        try:
            exact=exact_condition(current,state,cell,adjustable,geometry=geometry,
                                  cell_vcg_m=current.cell_vcg_m)
        except ValueError as exc:
            last_failure=str(exc);continue
        if not (band.displacement_min_kg<=exact['displacement_kg']<=band.displacement_max_kg
            and band.draught_min_m<=exact['draught_m']<=band.draught_max_m
            and abs(exact['trim_m'])<=band.max_abs_trim_m
            and abs(exact['list_rad'])<=band.max_abs_heel_rad):continue
        point_estimate=_linear_arrays(current,cell,adjustable,instance=state)
        affine=_linear_arrays(current,cell,adjustable,instance=state,solver=True)
        affine_gms.append(affine['gm_m']);exact_heels.append(exact['list_rad'])
        for key in metrics:
            absolute[key].append(np.abs(np.asarray(point_estimate[key])-exact[key]))
            signed[key].append(np.asarray(affine[key])-exact[key])
        for sign,key in ((1.,'draught_aft_m'),(-1.,'draught_forward_m')):
            signed[key].append(affine['draught_m']+sign*.5*affine['trim_m']
                               -exact['draught_m']-sign*.5*exact['trim_m'])
        for key in coverage:coverage[key].append(float(exact[key]))
        for name in usage:usage[name]+=exact.get(f'liquid_{name}_queries',0)
    if len(absolute['gm_m'])!=samples:
        raise ValueError(f'family calibration accepted {len(absolute["gm_m"])}/{samples} '
                         f'in {attempts} attempts: {last_failure}')
    maxima={key:np.max(values,axis=0) for key,values in absolute.items()}
    diagnostics={'samples':samples,'attempts':attempts,'seed':seed,
        'calibration_kind':'geometry-family total moments and all tank fills',
        'family_domain':domain,'liquid_usage':usage,
        'max':{key:np.asarray(value).tolist() for key,value in maxima.items()},
        'p99':{key:np.percentile(values,99,axis=0).tolist() for key,values in absolute.items()},
        'signed_errors':solver_signed_statistics(signed,exact_list_rad=exact_heels,
            affine_gm_m=affine_gms,gm_min_m=base.gm_min_m),
        'coverage':{key:{'min':min(values),'max':max(values)} for key,values in coverage.items()},
        'signed_residual_definition':'affine solver with maximum slack FSM minus exact',
        'warning':'empirical family envelope; exact checker remains mandatory'}
    return LinearErrors(*(maxima[key] for key in metrics)),diagnostics


def _calibration_certificate(key, errors, diagnostics):
    from datetime import datetime, timezone
    from .dependencies import LINEAR_CALIBRATION_VERSION
    from .cache import _canonical
    table={'max':{name:getattr(errors,name) for name in
                  ('gm_m','trim_m','list_rad','draught_m','sf_n','bm_nm')},
           'p99':diagnostics['p99'],'signed_errors':diagnostics['signed_errors']}
    return {'format':1,'inputs_hash':key,'builder_version':LINEAR_CALIBRATION_VERSION,
            'error_table':table,'error_table_sha256':hashlib.sha256(_canonical(table)).hexdigest(),
            'date_utc':datetime.now(timezone.utc).isoformat()}


def _validate_calibration_certificate(certificate,key,errors,diagnostics):
    from .dependencies import LINEAR_CALIBRATION_VERSION
    from .cache import _canonical
    if (not isinstance(certificate,dict) or certificate.get('format')!=1
            or certificate.get('inputs_hash')!=key
            or certificate.get('builder_version')!=LINEAR_CALIBRATION_VERSION
            or not isinstance(certificate.get('date_utc'),str)):
        raise ValueError('invalid calibration qualification certificate')
    expected={'max':{name:getattr(errors,name) for name in
                     ('gm_m','trim_m','list_rad','draught_m','sf_n','bm_nm')},
              'p99':diagnostics['p99'],'signed_errors':diagnostics['signed_errors']}
    _validated_errors(diagnostics['p99'],len(errors.sf_n))
    if (_canonical(certificate.get('error_table'))!=_canonical(expected)
            or certificate.get('error_table_sha256')!=hashlib.sha256(_canonical(expected)).hexdigest()):
        raise ValueError('calibration certificate measured error table mismatch')


def _validated_errors(values, station_count):
    """Reject malformed shared payloads before attaching them to any layout."""
    names=('gm_m','trim_m','list_rad','draught_m','sf_n','bm_nm')
    if not isinstance(values,dict) or set(values)!=set(names):
        raise ValueError('invalid shared calibration error fields')
    checked={}
    for name in names:
        value=np.asarray(values[name],dtype=float)
        expected=(station_count,) if name in ('sf_n','bm_nm') else ()
        if value.shape!=expected or not np.isfinite(value).all() or np.any(value<0):
            raise ValueError('invalid shared calibration errors: '+name)
        checked[name]=value if value.ndim else float(value)
    return LinearErrors(**checked)


def calibrated_family(family, instance, *, samples=192, force_rebuild=False):
    """Share numerical response errors across full/slice cell layouts.

    The disk payload contains only errors and measurement diagnostics, never a
    layout-shaped template. Policy certificates are attached afresh: enriching
    an empirical margin bank cannot require repeating unchanged hydrostatics.
    A stale known production epoch stops before calibration. Only the private
    forced path may collect unqualified research evidence; its certificate is
    unusable by default solver constraints until the bank is qualified.
    """
    from .cache import (family_calibration_key,response_calibration_identity,
                        load_payload,store_payload)
    before=reference_inputs_cached(family.parameters)
    synchronize_reference(before)
    reference=reference_cache_identity(before)
    response=response_calibration_identity(family)
    from .margin_policy import assert_policy_epoch
    assert_policy_epoch(family,response,allow_unqualified=force_rebuild)
    domain=domain_for(family,instance)
    key=family_calibration_key(family,reference,domain,samples=samples)
    cached=None
    if not force_rebuild:
        cached=_CALIBRATION_MEMORY.get(key)
        if cached is None:
            payload=load_payload(key,namespace='payload')
            if payload is not None:
                try:
                    raw=payload['diagnostics']
                    if (raw['samples']!=samples or raw['family_domain']!=domain
                            or raw['response_calibration_identity']!=response
                            or raw['reference_identity']!=reference):
                        raise ValueError('shared calibration identity mismatch')
                    errors=_validated_errors(payload['errors'],len(family.template.station_x_m))
                    if 'certificate' not in payload:
                        # Early D30 workers already measured these exact inputs
                        # before the inert certificate wrapper was introduced.
                        # Their checksummed payload, response/reference/domain,
                        # sample count and finite statistics are validated above
                        # and below; qualification adds no new measurements.
                        certificate=_calibration_certificate(key,errors,raw)
                        certificate['qualification_wrapper']='verified existing D30 measurement payload'
                        _validate_calibration_certificate(certificate,key,errors,raw)
                        payload['certificate']=certificate
                        store_payload(key,payload,namespace='payload')
                    _validate_calibration_certificate(payload['certificate'],key,errors,raw)
                    raw['calibration_certificate']=payload['certificate']
                    cached=(errors,raw)
                except (KeyError,TypeError,ValueError):
                    cached=None
    if cached is None:
        errors,raw=calibrate_family(family,instance,samples=samples)
        if reference_inputs(family.parameters)!=before:
            raise ValueError('liquid-transfer reference inputs changed during family calibration; retry')
        raw.update(family_calibration_cache_key=key,
                   coefficient_family_cache_key=family.cache_key,
                   response_calibration_identity=response,
                   reference_inputs=before,reference_identity=reference)
        certificate=_calibration_certificate(key,errors,raw)
        _validate_calibration_certificate(certificate,key,errors,raw)
        raw['calibration_certificate']=certificate
        payload={'certificate':certificate,'errors':{name:getattr(errors,name) for name in
                          ('gm_m','trim_m','list_rad','draught_m','sf_n','bm_nm')},
                 'diagnostics':raw}
        store_payload(key,payload,namespace='payload')
    else:
        errors,raw=cached
    for array in (errors.sf_n,errors.bm_nm):array.flags.writeable=False
    if len(_CALIBRATION_MEMORY)>=16:_CALIBRATION_MEMORY.pop(next(iter(_CALIBRATION_MEMORY)))
    _CALIBRATION_MEMORY[key]=(errors,deepcopy(raw))
    diagnostics=deepcopy(raw)
    diagnostics['calibration_origin_coefficient_family_cache_key']=raw['coefficient_family_cache_key']
    diagnostics['coefficient_family_cache_key']=family.cache_key
    diagnostics['calibration_reference_inputs']=raw['reference_inputs']
    diagnostics['reference_inputs']=before
    diagnostics['reference_identity']=reference
    from .margin_policy import qualify_policy
    diagnostics['margin_policy']=qualify_policy(family,instance,diagnostics,
        **({'allow_reference_qualification':True} if force_rebuild else {}))
    return errors,diagnostics
