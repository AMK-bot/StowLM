"""Qualified per-vessel dispatch for IS B3.1.9.1 liquid-transfer tables.

A tank grid interpolates its actual liquid mass moments, preserving the changing
LCG as well as transverse/vertical shifts needed by free-trim equilibrium. A
vessel manifest binds the tank grids to the tank/hull inputs and its own builder version. Missing/unqualified data and queries outside a grid retain
the direct tank geometry; interpolation never extrapolates. Sources: IMO
MSC.267(85) B3.1.9.1 and IACS UR L5 table-based loading-instrument validation.

APPROXIMATION: interpolation is used only after measured errors against direct
liquid geometry and loaded conditions meet one fifth of the L5 tolerances. This
is empirical qualification, not a uniform mathematical error bound.
"""
from dataclasses import replace
from collections import OrderedDict
from functools import lru_cache
import hashlib
import json
import math
from collections.abc import Mapping
from pathlib import Path

from datetime import datetime, timezone
from stowage.checker import stability_liquid_tables

CACHE_DIRECTORY = Path(__file__).resolve().parents[3] / 'experiments/cache/stability'


STABILITY_LIQUID_MANIFEST_VERSION = 1


def _physics_inputs(value):
    """Discard explanatory annotations while retaining nested numeric inputs."""
    prose={'assumption','assumptions','source','sources','provenance','confidence',
           'description','notes','comment','comments','derivation','lightship_derivation',
           'lightship_distribution','hull_offsets_ref'}
    if isinstance(value,Mapping):
        return {key:_physics_inputs(item) for key,item in value.items() if key not in prose}
    if isinstance(value,(list,tuple)):
        return [_physics_inputs(item) for item in value]
    return value


def metadata(params, *, vessel_hash=None):
    # Caller-supplied full-vessel hashes include slice fixed loads; they are not
    # dependencies of a tank interpolation grid or its qualification.
    inputs={k:_physics_inputs(params[k]) for k in ('hull','tanks','hull_form') if k in params}
    # The offset content checksum carries the source solid; relocating the same
    # bytes or changing its historical parent label does not change the solid.
    if isinstance(inputs.get('hull_form'),dict):
        inputs['hull_form'].pop('offsets_path',None)
        inputs['hull_form'].pop('parent',None)
    if 'envelope' in params:
        envelope=params['envelope']
        inputs['envelope']={k:_physics_inputs(envelope[k]) for k in
             ('deck_z_m','count_deckhouse','hatch_coamings','buoyant_x_min_m','buoyant_x_max_m')
             if k in envelope}
        # Coaming ids label solids but do not determine their buoyancy.
        for box in inputs['envelope'].get('hatch_coamings',[]):
            if isinstance(box,dict): box.pop('id',None)
    # Downflooding openings cap the curve used by a criterion, not the buoyant
    # geometry/table interpolation. Their coordinates do not enter this key.
    digest=hashlib.sha256(json.dumps(inputs,sort_keys=True).encode()).hexdigest()
    return dict(inputs_hash=digest,builder_version=STABILITY_LIQUID_MANIFEST_VERSION,
                tank_builder_version=stability_liquid_tables.STABILITY_LIQUID_TABLES_VERSION)


def manifest_path(params, *, vessel_hash=None):
    value=metadata(params,vessel_hash=vessel_hash)
    return _manifest_path(value)


def _manifest_path(value):
    key=hashlib.sha256(json.dumps(value,sort_keys=True).encode()).hexdigest()
    return CACHE_DIRECTORY/('liquid-vessel-'+key+'.json')


_TABLE_IDENTITIES=OrderedDict()


def _table_identity(solid, metadata_value):
    signature=hashlib.sha256(json.dumps(metadata_value,sort_keys=True).encode()).digest()
    key=(id(solid),signature)
    if key in _TABLE_IDENTITIES:
        _TABLE_IDENTITIES.move_to_end(key)
        return _TABLE_IDENTITIES[key][1]
    identity=stability_liquid_tables._identity(solid)
    _TABLE_IDENTITIES[key]=(solid,identity)
    while len(_TABLE_IDENTITIES)>256:
        _TABLE_IDENTITIES.popitem(last=False)
    return identity


@lru_cache(maxsize=24)
def _read_manifest(path, mtime_ns, size):
    return json.loads(Path(path).read_text())


def _valid_statistics(stats):
    try:
        samples=float(stats['samples'])
        return (math.isfinite(samples) and samples.is_integer() and samples>=4 and
                all(math.isfinite(float(stats[k])) and 0<=stats[k]<=.2 for k in ('gz','area','gm')))
    except (TypeError,ValueError,KeyError,OverflowError):
        return False


def attach_tables(condition, params, *, vessel_hash=None, require_manifest=True, _metadata_value=None):
    """Attach already qualified tables; never build geometry grids in a check.

    ``require_manifest=False`` is an explicit offline qualification hook, not an
    environment bypass in the production checker. Individual tank certificates
    remain mandatory in either case. ``_metadata_value`` is a private hot-path
    hook: the checker computes it once from the same canonical parameter payload
    that constructs its immutable cached hull. It must never be supplied from an
    unrelated or edited parameter tree. This process-local reuse does not alter
    persisted content keys, table qualification or certificate checks.
    """
    expected=metadata(params,vessel_hash=vessel_hash) if _metadata_value is None else _metadata_value
    manifest=None
    if require_manifest:
        try:
            path=_manifest_path(expected)
            stat=path.stat();manifest=_read_manifest(str(path),stat.st_mtime_ns,stat.st_size)
            if manifest['metadata'] != expected:
                return condition
            stats=manifest['validation']
            if not _valid_statistics(stats) or not isinstance(manifest.get('tank_table_keys'),Mapping):
                return condition
        except (OSError,ValueError,TypeError,KeyError):
            return condition
    tables={}
    for tank in (*condition.actual_tanks,*condition.tanks):
        if tank.id in tables or tank.geometry is None:
            continue
        table=stability_liquid_tables.lookup(tank.geometry)
        if table is not None:
            if manifest is not None and manifest['tank_table_keys'].get(tank.id) != _table_identity(tank.geometry,expected):
                continue
            tables[tank.id]=table
    result=replace(condition,liquid_tables=tables or None,cg_cache={},liquid_usage={},liquid_cg_groups=None)
    if tables:
        from stowage.checker.stability_liquid_batch import prepare
        result=replace(result,liquid_cg_groups=prepare(result))
    return result


def qualify_vessel(params, condition, statistics):
    """Write a manifest after per-tank and full-condition independent tests."""
    if not _valid_statistics(statistics):
        raise ValueError('vessel liquid qualification requires four samples within one fifth L5')
    keys={}
    from stowage.vessel.tanks import TankGeometry
    for raw in params.get('tanks',[]):
        solid=TankGeometry.from_params(raw,ship_params=params)
        if getattr(solid,'shape',None)=='hull_sections':
            if stability_liquid_tables.lookup(solid) is None:
                raise ValueError(f'tank {raw["id"]} is not independently qualified')
            keys[raw['id']]=stability_liquid_tables._identity(solid)
    path=manifest_path(params)
    path.parent.mkdir(parents=True,exist_ok=True)
    temporary=path.with_suffix('.tmp')
    temporary.write_text(json.dumps(dict(metadata=metadata(params),tank_table_keys=keys,
                                         validation=statistics,date=datetime.now(timezone.utc).isoformat()),sort_keys=True,indent=2)+'\n')
    temporary.replace(path)
    _read_manifest.cache_clear()
    return path


def migrate_legacy(params):
    """Rebind an existing measured vessel certificate after all grids migrate.

    Old full-parameter digests included fixed loads and component versions; the
    new certificate excludes these. Matching every old per-tank geometry/key
    lineage prevents evidence being transferred to different tank grids.
    """
    path=manifest_path(params)
    try:
        current=json.loads(path.read_text())
        if (current.get('metadata')==metadata(params) and _valid_statistics(current.get('validation'))
                and isinstance(current.get('tank_table_keys'),Mapping)):
            return 'reused'
    except (OSError,ValueError,TypeError):
        pass
    if STABILITY_LIQUID_MANIFEST_VERSION != 1 or stability_liquid_tables.STABILITY_LIQUID_TABLES_VERSION != 1:
        return 'missing'
    from stowage.vessel.tanks import TankGeometry
    keys={}; predecessors={}
    for raw in params.get('tanks',[]):
        solid=TankGeometry.from_params(raw,ship_params=params)
        if getattr(solid,'shape',None) != 'hull_sections': continue
        if stability_liquid_tables.lookup(solid) is None: return 'missing'
        key=stability_liquid_tables._identity(solid);keys[raw['id']]=key
        evidence=json.loads(stability_liquid_tables._path(solid).with_suffix('.qualified.json').read_text())
        old=evidence.get('legacy_evidence',{}).get('path')
        if old is None: return 'missing'
        predecessors[raw['id']]=Path(old).stem.removeprefix('liquid-')
    for candidate in CACHE_DIRECTORY.glob('liquid-vessel-*.json'):
        if candidate == path: continue
        try:
            old=json.loads(candidate.read_text())
            # Legacy manifests contain only a full-vessel digest. A matching
            # current base vessel proves the hull/loading qualification domain;
            # after migration, slice background edits never enter the new key.
            original_hash=hashlib.sha256(json.dumps(params,sort_keys=True).encode()).hexdigest()
            if old.get('metadata',{}).get('vessel_sha256') != original_hash: continue
            if old.get('tank_table_keys') != predecessors or not _valid_statistics(old.get('validation')): continue
            payload=dict(metadata=metadata(params),tank_table_keys=keys,validation=old['validation'],
                         date=datetime.now(timezone.utc).isoformat(),legacy_evidence=str(candidate))
            temporary=path.with_suffix('.tmp');temporary.write_text(json.dumps(payload,sort_keys=True,indent=2)+'\n');temporary.replace(path)
            _read_manifest.cache_clear();return 'migrated'
        except (OSError,ValueError,TypeError,KeyError): continue
    return 'missing'
