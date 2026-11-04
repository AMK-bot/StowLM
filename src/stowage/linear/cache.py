"""Content-addressed numerical cache; never deserialize executable objects.

Calibrated models depend on the entire instance and implementation, while the
separate ``vessel`` namespace can retain expensive geometry/matrix payloads
independently of cargo or tank contents. Keys include the builder's own version,
explicit numerical dependencies and canonical physical inputs. Global component
versions and unrelated rules do not invalidate physics (decision D30). A miss rebuilds;
corrupt, incomplete, obsolete or unsupported entries never become models.

An atomic JSON manifest points to an immutable, checksummed NPZ. Arrays load
with ``allow_pickle=False``. JSON and numeric arrays are the only stored types;
no Python object, class or callable is reconstructed from cache input.
"""
from __future__ import annotations

from dataclasses import fields, is_dataclass
from datetime import datetime, timezone
import hashlib
import io
import json
import math
import os
from pathlib import Path
import re
import tempfile
import zipfile
from functools import lru_cache

import numpy as np

_FORMAT = 2
_DEFAULT_ROOT = Path(__file__).resolve().parents[3] / "experiments/cache/linear"
_METADATA = ("measurement_diagnostics", "hull_length_m", "hull_max_draught_m",
             "hull_min_draught_fwd_m", "_fixed_tank_masses", "_reference_params",
             "_geometry_fingerprint", "_cargo_mass_kg", "_tank_mass_bounds_kg", "_nominal_height_m", "_manifest_ids", "_manifest_fingerprint",
             "_constraint_cell_vcg_m", "_family_response_sf", "_family_response_bm",
             "_family_buoyancy_sf", "_family_buoyancy_bm", "_family_lightship_sf", "_family_lightship_bm",
             "_fixed_loads_fingerprint", "_vessel_physics_fingerprint", "coefficient_certificate")
_FAMILY_ARRAYS = ("_family_response_sf", "_family_response_bm", "_family_buoyancy_sf",
                  "_family_buoyancy_bm", "_family_lightship_sf", "_family_lightship_bm")
_MAX_BYTES = 512 * 1024 * 1024


class _FrozenDict(dict):
    """Private parameter snapshots: memoization is safe only after freezing."""
    def _immutable(self, *args, **kwargs):
        raise TypeError('prepared linear parameters are immutable')
    __setitem__ = __delitem__ = clear = pop = popitem = setdefault = update = __ior__ = _immutable
    def __deepcopy__(self, memo):
        return self


class _FrozenList(list):
    def _immutable(self, *args, **kwargs):
        raise TypeError('prepared linear parameters are immutable')
    __setitem__ = __delitem__ = append = clear = extend = insert = pop = remove = reverse = sort = __iadd__ = __imul__ = _immutable
    def __deepcopy__(self, memo):
        return self


class _PreparedParameters(_FrozenDict):
    __slots__ = ('_canonical_bytes',)
    def __setattr__(self, name, value):
        raise TypeError('prepared linear parameters are immutable')


def prepare_parameters(parameters):
    """Freeze an independent fleet parse; retain its exact canonical bytes.

    Only this private type can skip repeated serialization. Ordinary mutable
    caller dictionaries still have their actual contents inspected every time.
    The resolver reads raw TOML bytes on every call before selecting a snapshot.
    """
    def freeze(value):
        if isinstance(value, dict):
            return _FrozenDict((key, freeze(item)) for key, item in value.items())
        if isinstance(value, list):
            return _FrozenList(freeze(item) for item in value)
        return value
    prepared = _PreparedParameters((key, freeze(item)) for key, item in parameters.items())
    object.__setattr__(prepared, '_canonical_bytes', _canonical(parameters))
    return prepared


def _jsonable(value):
    kind=type(value)
    if value is None or kind is bool or kind is int or kind is float or kind is str:
        return value
    if kind is dict:
        return {str(k):_jsonable(v) for k,v in value.items()}
    if kind is list or kind is tuple:
        return [_jsonable(v) for v in value]
    # Nonexact subclasses retain the historical model/dataclass priority.
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    if is_dataclass(value):
        return {f.name: _jsonable(getattr(value, f.name)) for f in fields(value)}
    if isinstance(value, np.ndarray):
        return value.tolist()
    if isinstance(value, np.generic):
        return value.item()
    if isinstance(value, dict):
        return {str(k): _jsonable(v) for k, v in value.items()}
    if isinstance(value, (tuple, list)):
        return [_jsonable(v) for v in value]
    return value


def _canonical(value):
    if type(value) is _PreparedParameters:
        return value._canonical_bytes
    return json.dumps(_jsonable(value), sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


def fixed_loads_fingerprint(instance):
    """Bind use of an assembled model to its complete background safety records.

    This strict operation guard deliberately retains classification, silhouettes
    and D42 securing evidence. Numerical cache identities use the separate
    point-mass projection below; reusing coefficients is not safety approval.
    """
    return hashlib.sha256(_canonical(instance.vessel.fixed_loads)).hexdigest()


def fixed_loads_numerical(instance):
    """The aggregate point masses consumed by linear constants and references.

    D42 box records describe the same aggregate mass, without adding another
    mass term. Their securing/visibility evidence and classification do not
    alter this numerical calculation. Retain the complete records separately
    through ``fixed_loads_fingerprint`` and the exact checker.
    """
    names = ('x_m', 'y_m', 'z_m', 'mass_kg')
    result = []
    for load in instance.vessel.fixed_loads:
        if isinstance(load, dict):
            values = [load[name] for name in names]
        else:
            # FixedLoad preserves this same legacy numerical iteration API.
            values = tuple(load)
        result.append({name: float(value) for name, value in zip(names, values, strict=True)})
    return result


def source_fingerprint():
    """Hash local source, including exact judges whose residuals calibrate us."""
    package = Path(__file__).resolve().parents[1]
    paths = set()
    for area in ("linear", "checker", "vessel", "model", "rules"):
        paths.update((package / area).rglob("*.py"))
    paths.update((package / "rules").rglob("*.toml"))
    paths.update(package / name for name in ("versions.py", "params.py", "conventions.py"))
    digest = hashlib.sha256()
    for path in sorted(paths):
        digest.update(str(path.relative_to(package)).encode())
        digest.update(path.read_bytes())
    return digest.hexdigest()


def _offset_sources(params):
    """Validate checksum-pinned offset bytes before consulting any cache.

    Matching a declared pin alone is insufficient: an overwritten/missing source
    must fail exactly as a cold geometry build does. Resolve relative paths with
    the same repository root as vessel.hull_offsets.read_offsets. This checks
    the bytes only; the official geometry parser still owns format validation.
    """
    form = params.get("hull_form", {}) if isinstance(params, dict) else {}
    if not isinstance(form, dict):
        return {}
    def plain(value):
        return value.get("value") if isinstance(value, dict) and "value" in value else value
    family = plain(form.get("family"))
    if family != "stowlm_offsets_v1" and "offsets_path" not in form:
        return {}
    from stowage.params import repo_root
    path = Path(plain(form.get("offsets_path", "")))
    if not path.is_absolute():
        path = repo_root() / path
    expected = plain(form.get("offsets_sha256"))
    raw = path.read_bytes()
    actual = hashlib.sha256(raw).hexdigest()
    if not isinstance(expected, str) or not re.fullmatch(r"[0-9a-fA-F]{64}", expected) or actual != expected.lower():
        raise ValueError(f"offset source checksum mismatch: {path}")
    # Location/provenance is not physics; validation above still reads the
    # resolved source on every hit. Identical pinned bytes share coefficients.
    return {"offsets_sha256": actual}


def _physical(value):
    """Discard explanatory provenance, retaining actual SI values and shapes."""
    return _physical_normalized(_jsonable(value))


def _physical_normalized(value):
    """Project a normalized tree without repeatedly copying its descendants."""
    # model_dump()/object-array tolist() can expose still-unnormalized
    # subclasses. Preserve the original model/dataclass priority for them,
    # including dict and scalar subclasses, before taking JSON fast paths.
    if type(value) not in (type(None), bool, int, float, str, dict, list):
        normalized = _jsonable(value)
        if normalized is not value:
            return _physical_normalized(normalized)
    if isinstance(value,dict):
        if any(not isinstance(key,str) for key in value):
            value={str(key):item for key,item in value.items()}
        if 'value' in value and 'unit' in value:return _physical_normalized(value['value'])
        return {key:_physical_normalized(item) for key,item in value.items()
                if key not in ('source','sources','assumption','provenance','component_versions','versions')}
    if isinstance(value,list):return [_physical_normalized(item) for item in value]
    # Object arrays and unusual scalar wrappers can expose another structured
    # value through tolist()/item(). Preserve the former recursive normalization
    # for those leaves without walking ordinary JSON subtrees again.
    if value is not None and not isinstance(value,(bool,int,float,str)):
        normalized=_jsonable(value)
        if normalized is not value:return _physical_normalized(normalized)
    return value


_DIMENSIONS=('lpp_m','beam_m','depth_m','design_draught_m','water_density_kg_m3')


def physical_parameters(params,artifact='coefficient'):
    """The subtrees consumed by HullGeometry/TankGeometry, without fleet labels."""
    # Fresh canonical bytes detect edits even when size/mtime and object
    # identity stay unchanged. Persist only immutable serialized projections;
    # a caller receives an independent tree on every hit.
    return json.loads(_physical_parameters_bytes(_canonical(params),artifact))


def _physical_parameter_bytes(params, artifact):
    return _physical_parameters_bytes(_canonical(params), artifact)


@lru_cache(maxsize=32)
def _physical_parameters_bytes(canonical,artifact):
    return _canonical(_project_physical_parameters(json.loads(canonical),artifact))


def _project_physical_parameters(params,artifact):
    params=_physical(params)
    if not isinstance(params,dict):return params
    if artifact=='integration':return {}  # Explicit Gauss x/weights/stations are all matrix inputs.
    if not any(name in params for name in ('hull','hull_form','tanks','envelope')):
        return params  # Small, explicit payload builders also use cache_key.
    result={name:params[name] for name in ('hull_form','envelope') if name in params}
    if isinstance(result.get('hull_form'),dict):
        result['hull_form']={name:value for name,value in result['hull_form'].items()
                             if name not in ('offsets_path','parent')}
    if 'hull' in params:
        result['hull']={name:params['hull'][name] for name in _DIMENSIONS if name in params['hull']}
    if artifact not in ('geometry','integration') and 'tanks' in params:
        names=('id','geometry') if artifact in ('fsm','fixed_tank') else ('id','kind','geometry','content_density_kg_m3','capacity_m3','adjustable')
        result['tanks']=[{name:tank[name] for name in names if name in tank} for tank in params['tanks']]
        # TankGeometry accepts geometry nested under ``geometry`` or directly
        # on the tank record. Normalize both for every solid-consuming cache,
        # including the inner FSM payload used when a family is rebuilt. Keep
        # all geometry data: bounds, section arrays and bounds-only hull-build
        # controls must not collapse to an ID-only key for flat records.
        for projected,tank in zip(result['tanks'],params['tanks'],strict=True):
            if 'geometry' not in tank:
                projected['geometry']={key:value for key,value in tank.items()
                    if key not in ('id','kind','content_density_kg_m3','capacity_m3',
                                   'adjustable','table','heated','x_aft_m','x_fore_m')}
    return result


def vessel_physics(vessel):
    """Project exactly the vessel values used by coefficient construction."""
    hull_keys=('lpp_m','beam_m','depth_m','design_draught_m','lightship_mass_kg','lightship_lcg_m',
        'lightship_tcg_m','lightship_vcg_m','lightship_distribution','min_gm_m','max_trim_m',
        'max_list_rad','max_draught_m','min_draught_fwd_m','min_draught_aft_m')
    # Project before model_dump: the discarded KN/hydrostatic/provenance trees
    # can be much larger than the coefficient inputs themselves.
    names=('cells','stacks','tanks','hull')
    if hasattr(type(vessel),'model_fields'):
        raw={name:getattr(vessel,name) for name in names}
        if raw['hull'] is not None:
            raw['hull']={name:getattr(raw['hull'],name) for name in (*hull_keys,'stations')
                         if hasattr(raw['hull'],name)}
        raw={name:([_jsonable(item) for item in value] if isinstance(value,(list,tuple))
                   else _jsonable(value)) for name,value in raw.items()}
    else:
        raw=_jsonable(vessel)
    if not isinstance(raw,dict):raise TypeError('vessel physical identity requires a model or mapping')
    def records(name,keys):return [{key:record[key] for key in keys if key in record} for record in raw.get(name,[])]
    hull=raw.get('hull') or {}
    hull={**{key:hull[key] for key in hull_keys if key in hull},
          'stations':[{key:row[key] for key in ('x_m','max_shear_n','min_shear_n','max_bending_nm','min_bending_nm') if key in row}
                      for row in hull.get('stations',[])]}
    return _physical(dict(cells=records('cells',('id','lcg_m','tcg_m')),
        stacks=records('stacks',('id','cell_ids','base_z_m')),
        tanks=records('tanks',('id','kind','content_density_kg_m3','capacity_m3','table','adjustable')),
        hull=hull))


def _rule_inputs(artifact):
    if artifact not in ('coefficient','instance'):return {}
    from stowage.rules.catalogue import get_rule
    return {'iscode.a2_2.initial_gm.minimum':float(get_rule('iscode.a2_2.initial_gm').parameters['minimum'].value)}


def _persistent_vessel_id(vessel_id):
    """Resolve labels to the original D30 spelling without changing any physics.

    Existing measured payloads and certificates retain their original hashes.
    Both public IDs therefore hash to the same historical namespace; this is
    only a label projection, with all numerical input coverage unchanged.
    """
    from stowage.conventions import VESSEL_ID_ALIASES, canonical_vessel_id
    canonical = canonical_vessel_id(vessel_id)
    root, separator, suffix = canonical.partition('.')
    historical = next((old for old, new in VESSEL_ID_ALIASES.items() if new == root), root)
    return historical + separator + suffix


def cache_key(kind, *, vessel_id, params, inputs, band=None, density=None,
              versions=None, source_hash=None):
    """D30 key: actual inputs, explicit physics closure, own builder versions.

    Legacy explicit ``versions`` accepts linear-owned test/override identifiers
    only; unrelated component versions are ignored. ``source_hash`` remains an
    explicit dependency override for isolated tests, never a production default.
    """
    offset_sources = _offset_sources(params)
    from .dependencies import cache_artifact,builder_versions,dependency_fingerprint
    artifact=cache_artifact(kind,inputs)
    own_versions=builder_versions(artifact)
    if versions is not None:
        own_versions.update({key:value for key,value in versions.items()
            if key=='linear' or key in own_versions})
    content = dict(format=_FORMAT, kind=kind, vessel_id=_persistent_vessel_id(vessel_id),
        builder_versions=own_versions, params_hash=hashlib.sha256(_physical_parameter_bytes(params,artifact)).hexdigest(),
        offset_sources=offset_sources if artifact!='integration' else {},
        dependencies=source_hash if source_hash is not None else dependency_fingerprint(artifact),
        rule_inputs=_rule_inputs(artifact),
        band=band, band_metadata={name: getattr(band, name) for name in
            ("_tank_mass_bounds_kg", "_cargo_mass_kg") if hasattr(band, name)},
        density=density, inputs=inputs)
    return hashlib.sha256(_canonical(content)).hexdigest()


def model_cache_key(instance, band, samples, geometry_params):
    """Includes loading state and the exact reference's free-surface method.

    Only calibrated models depend on that method: reusable hull geometry and
    influence matrices retain their method-independent vessel keys.
    """
    try:
        method = int(os.environ.get("STOWAGE_STABILITY_FREE_SURFACE_METHOD", "1"))
    except ValueError as exc:
        raise ValueError("STOWAGE_STABILITY_FREE_SURFACE_METHOD must be 1 or 2") from exc
    if method not in (1, 2):
        raise ValueError("STOWAGE_STABILITY_FREE_SURFACE_METHOD must be 1 or 2")
    state={'vessel':vessel_physics(instance.vessel),'fixed_loads':fixed_loads_numerical(instance),
        # D40 limits restrict instance rows; they do not alter reusable hull
        # derivatives. Keep them in the assembled-model identity explicitly.
        'loading_manual_endpoints':{name:getattr(instance.vessel.hull,name,None)
            for name in ('min_trim_m','max_trim_m','min_displacement_kg','max_displacement_kg')},
        'assembly_stacks':[{name:getattr(stack,name) for name in ('id','cell_ids','base_z_m','max_height_m','on_deck')}
                           for stack in instance.vessel.stacks],
        'containers':[_physical(box) for box in instance.containers],
        'onboard':_physical(instance.onboard),'load_list':instance.load_list,'discharge_list':instance.discharge_list,
        'port':{name:getattr(instance.port,name,None) for name in ('water_density_kg_m3','max_draught_m')},
        'rule_overrides':instance.rule_overrides}
    state['onboard']['vessel_id'] = _persistent_vessel_id(state['onboard']['vessel_id'])
    return cache_key("model", vessel_id=instance.vessel.id, params=geometry_params,
        inputs={"numerical_state": state, "samples": samples,
                "reference_free_surface_method": method}, band=band,
        density=instance.port.water_density_kg_m3)


def vessel_cache_key(vessel_id, geometry_params, *, inputs=None, band=None, density=None):
    """Key a reusable geometry/matrix payload without loading-condition state.

    Callers include resolution, station layout and response reference in inputs;
    band/density must be supplied when a matrix depends on them.
    """
    return cache_key("vessel", vessel_id=vessel_id, params=geometry_params,
                     inputs=inputs, band=band, density=density)


def coefficient_family_key(instance,geometry_params,band):
    """Hull/slice/tank response identity, excluding background loads/bookings.

    Fixed loads change instance constants, not the two-column buoyancy response.
    Complete cell/stack/tank/hull contracts stay in this key, including limits,
    lightship blocks and source pins. No calibration is implied by this key.
    """
    return cache_key('coefficient_family',vessel_id=instance.vessel.id,
        params=geometry_params,inputs={'vessel_physics':vessel_physics(instance.vessel)},
        band=band,density=instance.port.water_density_kg_m3)


def response_calibration_inputs(family):
    """Cell-free hydrostatic/tank response shared by full ships and slices."""
    model=family.template
    names=('water_density_kg_m3','station_x_m','tank_ids','tank_lcg_m','tank_tcg_m','tank_vcg_m',
        'tank_capacity_kg','tank_fsm_max_kgm','tank_adjustable','sf_per_kg_tank','bm_per_kg_tank',
        'const_mass_kg','const_moment_x_kgm','const_moment_y_kgm','const_moment_z_kgm',
        'km_line','trim_line','draught_line','gm_min_m')
    return {**{name:getattr(model,name) for name in names},
            **{name:getattr(family,name) for name in ('response_sf','response_bm','buoyancy_sf','buoyancy_bm','lightship_sf','lightship_bm')}}


def response_calibration_identity(family):
    from .dependencies import dependency_fingerprint,builder_versions
    # Array contents are freshly serialized, even if callers toggle writeable
    # flags. The large immutable physical parameter projection is reused.
    return _response_identity_bytes(_canonical(response_calibration_inputs(family)),
        _physical_parameter_bytes(family.parameters,'calibration'),
        _canonical(_offset_sources(family.parameters)),_canonical(family.template.band),
        _canonical(builder_versions('calibration')),dependency_fingerprint('calibration'))


@lru_cache(maxsize=32)
def _response_identity_bytes(response, parameters, offsets, band, versions, dependency):
    return hashlib.sha256(_canonical({'response':json.loads(response),
        'parameters':json.loads(parameters),'offset_sources':json.loads(offsets),
        'band':json.loads(band),'builder_versions':json.loads(versions),
        'dependencies':dependency})).hexdigest()


def family_calibration_key(family,reference_identity,domain,*,samples=192):
    return cache_key('family_calibration',vessel_id=family.parameters.get('id','physical-response'),
        params=family.parameters,inputs={'response_identity':response_calibration_identity(family),
            'samples':samples,'reference':reference_identity,'domain':domain},
        band=family.template.band,density=family.template.water_density_kg_m3)


def _path(key, namespace, root):
    if not re.fullmatch(r"[0-9a-f]{64}", key) or namespace not in ("model", "vessel", "payload", "geometry", "integration"):
        raise ValueError("invalid numerical cache key or namespace")
    return (Path(root) if root is not None else _DEFAULT_ROOT) / namespace


def _encode(value, arrays):
    if isinstance(value, np.ndarray):
        if value.dtype.kind not in "biuf" or not np.all(np.isfinite(value)):
            raise ValueError("cache arrays must be finite real numeric values")
        name = f"a{len(arrays)}"
        arrays[name] = value
        return {"array": name}
    if is_dataclass(value):
        value = {f.name: getattr(value, f.name) for f in fields(value)}
    if isinstance(value, dict):
        return {"dict": {str(k): _encode(v, arrays) for k, v in value.items()}}
    if isinstance(value, (tuple, list)):
        return {"list": [_encode(v, arrays) for v in value]}
    if isinstance(value, np.generic):
        value = value.item()
    if value is None or isinstance(value, (str, float, int, bool)):
        return {"scalar": value}
    raise ValueError("unsupported cache payload type")


def _decode(value, arrays):
    if not isinstance(value, dict) or len(value) != 1:
        raise ValueError("malformed cache payload")
    if "array" in value:
        array = arrays[value["array"]]
        if array.dtype.kind not in "biuf" or not np.all(np.isfinite(array)):
            raise ValueError("unsafe cache array")
        array.flags.writeable = False
        return array
    if "dict" in value:
        return {k: _decode(v, arrays) for k, v in value["dict"].items()}
    if "list" in value:
        return [_decode(v, arrays) for v in value["list"]]
    if "scalar" in value:
        item = value["scalar"]
        if item is None or isinstance(item, (str, int, float, bool)):
            return item
    raise ValueError("unsupported cache payload")


def _atomic(path, content):
    descriptor, temp_name = tempfile.mkstemp(prefix=".partial-", dir=path.parent)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_name, path)
    finally:
        Path(temp_name).unlink(missing_ok=True)


def store_payload(key, payload, *, namespace="payload", root=None):
    """Best-effort atomic write; cache availability never gates calculation."""
    try:
        folder = _path(key, namespace, root)
        arrays = {}
        encoded = _encode(payload, arrays)
        buffer = io.BytesIO()
        np.savez_compressed(buffer, **arrays)
        data = buffer.getvalue()
        if len(data) > _MAX_BYTES:
            return False
        checksum = hashlib.sha256(data).hexdigest()
        filename = f"{key}.{checksum}.npz"
        manifest = _canonical(dict(format=_FORMAT, key=key, file=filename,
                                   sha256=checksum, payload_sha256=hashlib.sha256(_canonical(encoded)).hexdigest(), payload=encoded))
        folder.mkdir(parents=True, exist_ok=True)
        _atomic(folder / filename, data)
        _atomic(folder / f"{key}.json", manifest)
        return True
    except (OSError, ValueError, TypeError, OverflowError):
        return False


def load_payload(key, *, namespace="payload", root=None):
    """Return None on every corrupt/incomplete/obsolete cache entry."""
    try:
        folder = _path(key, namespace, root)
        manifest_path = folder / f"{key}.json"
        if manifest_path.stat().st_size > 16*1024*1024:
            return None
        manifest = json.loads(manifest_path.read_text())
        if hashlib.sha256(_canonical(manifest["payload"])).hexdigest() != manifest["payload_sha256"]:
            return None
        checksum = manifest["sha256"]
        filename = f"{key}.{checksum}.npz"
        if (manifest["format"] != _FORMAT or manifest["key"] != key or
                manifest["file"] != filename or not re.fullmatch(r"[0-9a-f]{64}", checksum)):
            return None
        path = folder / filename
        if path.stat().st_size > _MAX_BYTES:
            return None
        data = path.read_bytes()
        if hashlib.sha256(data).hexdigest() != checksum:
            return None
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            if sum(info.file_size for info in archive.infolist()) > _MAX_BYTES:
                return None
            for info in archive.infolist():
                with archive.open(info) as member:
                    version = np.lib.format.read_magic(member)
                    if version == (1, 0):
                        shape, _, dtype = np.lib.format.read_array_header_1_0(member)
                    elif version == (2, 0):
                        shape, _, dtype = np.lib.format.read_array_header_2_0(member)
                    else:
                        return None
                    if dtype.kind not in "biuf" or any(n < 0 for n in shape):
                        return None
                    expected = math.prod(shape)*dtype.itemsize
                    if expected > _MAX_BYTES or member.tell()+expected != info.file_size:
                        return None
        with np.load(io.BytesIO(data), allow_pickle=False) as archive:
            arrays = {name: archive[name] for name in archive.files}
        return _decode(manifest["payload"], arrays)
    except (OSError, ValueError, TypeError, KeyError, OverflowError, zipfile.BadZipFile, EOFError, RecursionError):
        return None


def _coefficient_array_digest(model):
    """Bind the certificate to inert numeric tables, including hidden responses."""
    digest = hashlib.sha256()
    names = sorted({f.name for f in fields(model) if isinstance(getattr(model, f.name), np.ndarray)}
                   | set(_FAMILY_ARRAYS))
    for name in names:
        value = np.ascontiguousarray(getattr(model, name))
        digest.update(_canonical([name, value.dtype.str, value.shape]))
        digest.update(value.tobytes())
    return digest.hexdigest()


def store_model(key, model, *, root=None):
    if all(getattr(model, name, None) is not None for name in _FAMILY_ARRAYS):
        from .dependencies import LINEAR_COEFFICIENTS_VERSION
        # This attests tables/input binding, never accuracy or plan feasibility.
        certificate = {"status": "coefficients_only_not_calibrated", "inputs_hash": key,
                       "builder_version": LINEAR_COEFFICIENTS_VERSION,
                       "certified_at_utc": datetime.now(timezone.utc).isoformat(),
                       "table_arrays_sha256": _coefficient_array_digest(model)}
        object.__setattr__(model, "coefficient_certificate", certificate)
    # Keep one authoritative serialized copy of safety metadata. Duplicating
    # certificates in fields and metadata could hide a missing/tampered record.
    content = {f.name: getattr(model, f.name) for f in fields(model) if f.name not in _METADATA}
    metadata = {name: getattr(model, name) for name in _METADATA if hasattr(model, name)}
    band_metadata = {name: getattr(model.band, name) for name in
                     ("_tank_mass_bounds_kg", "_cargo_mass_kg") if hasattr(model.band, name)}
    return store_payload(key, {"fields": content, "metadata": metadata, "band_metadata": band_metadata},
                         namespace="model", root=root)


def load_model(key, *, root=None):
    from .model import LinearErrors, LinearModel, ValidityBand
    payload = load_payload(key, namespace="model", root=root)
    if payload is None:
        return None
    try:
        data = payload["fields"]
        data["band"] = ValidityBand(**data["band"])
        for name, value in payload.get("band_metadata", {}).items():
            if name not in ("_tank_mass_bounds_kg", "_cargo_mass_kg"):
                return None
            object.__setattr__(data["band"], name, value)
        data["errors"] = LinearErrors(**data["errors"])
        for name in ("cell_ids", "tank_ids", "km_line", "trim_line", "draught_line"):
            data[name] = tuple(data[name])
        model = LinearModel(**data)
        c, t, s = len(model.cell_ids), len(model.tank_ids), len(model.station_x_m)
        for name, shape in {"cell_lcg_m": (c,), "cell_tcg_m": (c,), "cell_vcg_m": (c,),
            "tank_lcg_m": (t,), "tank_tcg_m": (t,), "tank_vcg_m": (t,),
            "tank_capacity_kg": (t,), "tank_fsm_max_kgm": (t,), "tank_adjustable": (t,),
            "sf_per_kg_cell": (s,c), "bm_per_kg_cell": (s,c),
            "sf_per_kg_tank": (s,t), "bm_per_kg_tank": (s,t),
            "const_sf_n": (s,), "const_bm_nm": (s,), "sf_max_n": (s,), "sf_min_n": (s,),
            "bm_max_nm": (s,), "bm_min_nm": (s,)}.items():
            if getattr(model, name).shape != shape:
                return None
        if model.errors.sf_n.shape != (s,) or model.errors.bm_nm.shape != (s,):
            return None
        for name, value in payload["metadata"].items():
            if name not in _METADATA:
                return None
            object.__setattr__(model, name, value)
        if any(getattr(model, name, None) is not None for name in _FAMILY_ARRAYS):
            from .dependencies import LINEAR_COEFFICIENTS_VERSION
            if not all(getattr(model, name, None) is not None for name in _FAMILY_ARRAYS):
                return None
            certificate = getattr(model, "coefficient_certificate", None) or {}
            if (certificate.get("status") != "coefficients_only_not_calibrated"
                or certificate.get("inputs_hash") != key
                or certificate.get("builder_version") != LINEAR_COEFFICIENTS_VERSION
                or certificate.get("table_arrays_sha256") != _coefficient_array_digest(model)
                or datetime.fromisoformat(certificate["certified_at_utc"]).utcoffset() is None):
                return None
        return model
    except (TypeError, ValueError, KeyError, AttributeError):
        return None
