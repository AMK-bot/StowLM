"""Read pinned scheme-1 historical data; never qualify it for current physics.

This explicit archive namespace hydrates the original coefficient arrays for
historical row-regression fixtures. Production load_model remains unchanged and
rejects their builder-1.0 certificates. No version, hash or certificate is rebased.
"""
from __future__ import annotations

from datetime import datetime
import hashlib
import json
from pathlib import Path

import numpy as np

HERE=Path(__file__).resolve().parent
ARCHIVE_MANIFEST_SHA256='8d4e39b545ddc91d4914db3630c6f62e19589af9342c65c11840dfac9088f589'


def _manifest(archive_root):
    root=Path(archive_root).resolve()
    try:
        raw=(root/'manifest.json').read_bytes()
        if hashlib.sha256(raw).hexdigest()!=ARCHIVE_MANIFEST_SHA256:
            raise ValueError('historical archive manifest checksum mismatch')
        manifest=json.loads(raw)
        if (manifest['format']!=1 or manifest['historical_hash_scheme_version']!='1'
                or manifest['production_usable'] is not False):
            raise ValueError('historical archive identity changed')
        for name,expected in manifest['files'].items():
            path=(root/name).resolve()
            if not path.is_relative_to(root):raise ValueError('historical archive path escapes namespace')
            if hashlib.sha256(path.read_bytes()).hexdigest()!=expected:
                raise ValueError('historical archive input checksum mismatch: '+name)
        return root,manifest
    except (OSError,KeyError,TypeError) as exc:
        raise ValueError('historical archive missing or malformed input') from exc


def read_archive_inputs(ship,*,archive_root=HERE):
    """Return fresh pinned raw, bank and identity dictionaries for one old hull."""
    root,manifest=_manifest(archive_root)
    entry=manifest['ships'][ship]
    raw=json.loads((root/entry['raw']).read_text())
    bank=json.loads((root/manifest['bank']).read_text())
    diag=raw['diagnostics']
    for name in ('coefficient_family_cache_key','family_calibration_cache_key'):
        if diag[name]!=entry[name]:raise ValueError('historical raw identity mismatch')
    if (diag['response_calibration_identity']!=entry['response_identity']
            or entry['response_identity'] not in bank['entries']):
        raise ValueError('historical bank/raw response mismatch')
    return raw,bank,{**entry,'historical_hash_scheme_version':'1','production_usable':False}


def _immutable_arrays(value):
    if isinstance(value,np.ndarray):
        return np.frombuffer(value.tobytes(),dtype=value.dtype).reshape(value.shape)
    if isinstance(value,dict):return {k:_immutable_arrays(v) for k,v in value.items()}
    if isinstance(value,list):return [_immutable_arrays(v) for v in value]
    return value


def load_archive_model(ship,*,archive_root=HERE):
    """Hydrate and validate ORIGINAL builder-1.0 coefficients as archived data.

    The raw manifest/NPZ checksums and original table-array certificate are all
    mandatory. Missing data never triggers construction, cache repair or a live
    production loader. Diagnostics explicitly prevent default production rows.
    """
    from stowage.linear import cache
    from stowage.linear.model import LinearErrors,LinearModel,ValidityBand
    raw,_,identity=read_archive_inputs(ship,archive_root=archive_root)
    key=identity['coefficient_family_cache_key']
    payload=cache.load_payload(key,namespace='model',root=Path(archive_root)/'cache')
    if payload is None:raise ValueError('historical coefficient payload is missing or invalid')
    try:
        payload=_immutable_arrays(payload)
        data=payload['fields']
        data['band']=ValidityBand(**data['band'])
        for name,value in payload.get('band_metadata',{}).items():
            if name not in ('_tank_mass_bounds_kg','_cargo_mass_kg'):
                raise ValueError('historical band metadata changed')
            object.__setattr__(data['band'],name,value)
        data['errors']=LinearErrors(**data['errors'])
        for name in ('cell_ids','tank_ids','km_line','trim_line','draught_line'):
            data[name]=tuple(data[name])
        model=LinearModel(**data)
        for name,value in payload['metadata'].items():
            if name not in cache._METADATA:raise ValueError('historical model metadata changed')
            object.__setattr__(model,name,value)
        c,t,s=len(model.cell_ids),len(model.tank_ids),len(model.station_x_m)
        shapes={**{name:(c,) for name in ('cell_lcg_m','cell_tcg_m','cell_vcg_m')},
                **{name:(t,) for name in ('tank_lcg_m','tank_tcg_m','tank_vcg_m','tank_capacity_kg','tank_fsm_max_kgm','tank_adjustable')},
                **{name:(s,) for name in ('station_x_m','const_sf_n','const_bm_nm','sf_max_n','sf_min_n','bm_max_nm','bm_min_nm')},
                'sf_per_kg_cell':(s,c),'bm_per_kg_cell':(s,c),
                'sf_per_kg_tank':(s,t),'bm_per_kg_tank':(s,t)}
        for name in cache._FAMILY_ARRAYS:
            shapes[name]=(s,2) if name.startswith('_family_response') else (s,)
        for name,shape in shapes.items():
            values=getattr(model,name)
            if not isinstance(values,np.ndarray) or values.shape!=shape or not np.isfinite(values).all():
                raise ValueError('historical array shape/content changed: '+name)
        if model.errors.sf_n.shape!=(s,) or model.errors.bm_nm.shape!=(s,):
            raise ValueError('historical error array shape changed')
        certificate=model.coefficient_certificate
        if (certificate['status']!='coefficients_only_not_calibrated'
                or certificate['inputs_hash']!=key
                or certificate['builder_version']!=identity['coefficient_builder_version']
                or certificate['builder_version']!='1.0'
                or certificate['table_arrays_sha256']!=cache._coefficient_array_digest(model)
                or datetime.fromisoformat(certificate['certified_at_utc']).utcoffset() is None):
            raise ValueError('original historical coefficient certificate is invalid')
        # Preserve the original certificate verbatim. This extra diagnostic is
        # deliberately not a margin-policy certificate or a current epoch.
        diagnostics=dict(model.measurement_diagnostics,
            historical_archive=identity,
            family_calibration_cache_key=raw['diagnostics']['family_calibration_cache_key'])
        object.__setattr__(model,'measurement_diagnostics',diagnostics)
        return model
    except (KeyError,TypeError,AttributeError) as exc:
        raise ValueError('historical coefficient payload is malformed') from exc
