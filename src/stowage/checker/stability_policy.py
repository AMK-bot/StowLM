"""Reviewed canonical policy projection, without unused manual table builds.

The public hull builder derives five manual limits before constructing its
hydrostatic/KN/Bonjean tables. This module repeats that unchanged prefix only
for explicitly reviewed complete construction identities. Unknown parameter,
source, library or runtime-provider identities retain the public fleet resolver.
No result verdict or mutable evaluated vessel is retained here.
"""
from __future__ import annotations

from functools import lru_cache
import hashlib
import json
import math
from pathlib import Path
import types


_QUALIFICATION = Path(__file__).with_name('stability_policy_contract.json')


def _code_payload(code):
    def constant(value):
        if isinstance(value, types.CodeType):
            return _code_payload(value)
        if isinstance(value, tuple):
            return ['tuple', [constant(item) for item in value]]
        if isinstance(value, frozenset):
            return ['frozenset', sorted((constant(item) for item in value), key=repr)]
        return [type(value).__name__, repr(value)]
    return dict(bytecode=code.co_code.hex(), constants=[constant(value) for value in code.co_consts],
        names=code.co_names, varnames=code.co_varnames, freevars=code.co_freevars,
        cellvars=code.co_cellvars, args=code.co_argcount, positional=code.co_posonlyargcount,
        keyword=code.co_kwonlyargcount, flags=code.co_flags,
        exceptions=code.co_exceptiontable.hex())


@lru_cache(maxsize=128)
def implementation_digest(code):
    """Code identity independent of checkout filename and debug line numbers."""
    return hashlib.sha256(json.dumps(_code_payload(code), sort_keys=True).encode()).hexdigest()


def runtime_providers():
    from stowage.vessel import fleet, fleet_cache, hull, fleet_lashing_design, hydrostatics
    from stowage.model import HullModel, Vessel, VesselFieldProvenance
    functions = dict(
        load_vessel=fleet.load_vessel, build_vessel=fleet._build_vessel,
        parameter_bytes=fleet._parameter_bytes, cached_vessel=fleet_cache.cached_vessel,
        construction_key=fleet_cache._cache_key, construction_sources=fleet_cache._source_files,
        hull_builder=hull.build_hull_model, plain=hull._plain,
        field_evidence=hull._field_evidence, load_line=hull._load_line_draught,
        lashing_design=fleet_lashing_design.populate_lashing_design,
        hull_geometry=hydrostatics.HullGeometry,
        public_hull_geometry=hull.HullGeometry,
        public_hull_model=hull.HullModel,
        public_field_provenance=hull.VesselFieldProvenance,
        public_vessel_model=fleet.Vessel, cached_vessel_model=fleet_cache.Vessel,
        geometry_from_params=getattr(hydrostatics.HullGeometry.from_params, '__func__',
                                     hydrostatics.HullGeometry.from_params),
        geometry_init=hydrostatics.HullGeometry.__init__,
        submerged=hydrostatics.HullGeometry.submerged, axes=hydrostatics.HullGeometry._axes,
        geometry_dependencies=hydrostatics._submerged_dependencies,
        hull_model=HullModel, hull_model_order=HullModel.validate_table_order,
        vessel_model=Vessel, vessel_model_geometry=Vessel.validate_geometry,
        field_provenance=VesselFieldProvenance,
        policy_projection=_project_reviewed_prefix, projection_dispatch=projected_policy,
        contract_reader=_contract, policy_providers=runtime_providers,
        policy_provider_identity=provider_identity, implementation_record=provider_record,
        provider_defaults=_provider_defaults, implementation_code=_code_payload,
        implementation_hash=getattr(implementation_digest,'__wrapped__',implementation_digest))
    return functions


def _provider_defaults(function):
    return repr((getattr(function,'__defaults__',None),getattr(function,'__kwdefaults__',None)))


def provider_identity():
    """Fresh runtime providers and reviewed-contract bytes for the LRU key.

    Keeping the function/code objects also prevents recycled object identities
    from aliasing an older baseline after a provider replacement.
    """
    try:
        payload = _QUALIFICATION.read_bytes()
    except OSError:
        payload = b''
    functions = runtime_providers()
    return payload, tuple((name, function, getattr(function, '__code__', None), _provider_defaults(function))
                          for name, function in functions.items())


def provider_record(function):
    code = getattr(function, '__code__', None)
    return dict(module=getattr(function, '__module__', None),
                name=getattr(function, '__qualname__', None),
                code=None if code is None else implementation_digest(code),
                defaults=_provider_defaults(function))


@lru_cache(maxsize=4)
def _contract(payload):
    try:
        value = json.loads(payload)
    except (ValueError, TypeError):
        return {}
    return value if isinstance(value, dict) else {}


def projected_policy(root, parent_digest, construction_identity, identity):
    """Return five computed values only for a completely reviewed contract.

    The construction key retains every public builder source, parameter byte,
    auxiliary input and library/schema dependency. Matching it is deliberately
    conservative: even an unexplained nonnumerical source edit requires the
    public resolver until reviewed again. No policy values are in the pin.
    """
    from stowage.vessel import fleet, hull, hydrostatics
    # The public builder resolves its own imported class binding. A rebind must
    # invalidate warmed baselines and retain that public builder's behavior.
    if hull.HullGeometry is not hydrostatics.HullGeometry:
        return None
    contract = _contract(identity[0])
    reviewed = contract.get('constructions', {}).get(root, {})
    if (contract.get('format') != 1 or reviewed.get('identity') != construction_identity
            or reviewed.get('parameter_sha256') != parent_digest):
        return None
    functions = runtime_providers()
    if {name:provider_record(function) for name, function in functions.items()} != contract.get('providers'):
        return None
    if hydrostatics._submerged_dependencies() != hydrostatics._ORIGINAL_SUBMERGED_DEPENDENCIES:
        return None
    # Re-read rather than trusting caller-owned parsed parameters or a filename.
    # A source change between outer validation and construction fails closed.
    payload = fleet._parameter_bytes(root)
    if hashlib.sha256(payload).hexdigest() != parent_digest:
        raise ValueError('canonical fleet parameter source changed during policy construction')
    import tomllib
    ship_params = tomllib.loads(payload.decode('utf-8'))
    if ship_params.get('id') != root:
        raise ValueError('canonical fleet parameter root disagrees with its declared identity')
    return _project_reviewed_prefix(ship_params)


def _project_reviewed_prefix(ship_params):
    """Exact policy prefix of the source-pinned public build_hull_model.

    This is not a separate approximation: the same validators, geometry,
    load-line definition and polygon integration compute the same five values.
    The unneeded table remainder is never evidence for these manual fields.
    """
    from stowage.vessel.hull import _plain, _field_evidence, _load_line_draught
    from stowage.vessel.hydrostatics import HullGeometry
    params = _plain(ship_params)
    h = params['hull']
    supplied = {**h, **params.get('operating_limits', {})}
    required = ('min_draught_fwd_m', 'min_trim_m', 'max_trim_m', 'max_list_rad', 'min_gm_m',
                'bilge_keel', 'bilge_keel_area_m2', 'windage_area_m2', 'windage_lever_m')
    missing = [key for key in required if key not in supplied]
    if missing:
        raise ValueError('missing vessel operational/windage inputs: '+', '.join(missing))
    if params['hull_form']['family'] != 'stowlm_offsets_v1':
        raise ValueError('production hull assembly requires checksum-pinned offsets; '
                         'the former analytic family is for geometry regression tests only')
    if supplied['min_gm_m'] < .15:
        raise ValueError('min_gm_m must be at least the IS Code 0.15 m minimum')
    if supplied['max_list_rad'] < 0:
        raise ValueError('list limit must be a nonnegative magnitude')
    for key in ('min_trim_m', 'max_trim_m', 'bilge_keel_area_m2'):
        value = supplied[key]
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
            raise ValueError(f'{key} must be a finite number')
    if supplied['min_trim_m'] > supplied['max_trim_m']:
        raise ValueError('min_trim_m must not exceed max_trim_m')
    if supplied['bilge_keel_area_m2'] < 0:
        raise ValueError('bilge_keel_area_m2 must be nonnegative')
    # Keep provenance validation, including the lightship mass declaration.
    for key in ('min_trim_m', 'max_trim_m', 'bilge_keel_area_m2', 'lightship_mass_kg'):
        _field_evidence(ship_params, key)
    summer_draught = _load_line_draught(params)
    geometry = HullGeometry.from_params(params)
    density = float(h.get('water_density_kg_m3', 1025.))
    light_mass = float(h['lightship_mass_kg'])
    maximum_mass = geometry.submerged(summer_draught, summer_draught, 0., density).displacement_kg
    return (float(supplied['min_trim_m']), float(supplied['max_trim_m']),
            light_mass, float(maximum_mass), float(supplied['bilge_keel_area_m2']))
