"""Evidence-bearing placement assessments for IMDG 42-24 SW1 and SW2.

Source: IMO MSC.556(108), 7.1.2 definitions and 7.1.5 stowage codes:
https://wwwcdn.imo.org/localresources/en/KnowledgeCentre/IndexofIMOResolutions/MSCResolutions/MSC.556(108).pdf

``geometry_from_vessel`` consumes the named model component catalog and typed
design declarations. An indexed shared-solid adapter preserves older fixtures;
``geometry_from_facts`` parses their equivalent JSON or Pydantic objects. The
assessors accept ``PlacementGeometryInterface`` directly. A map's
completeness evidence must cover the entire vessel: for SW2 all accommodation,
air intakes, machinery spaces and enclosed work areas; for SW1 all potentially
heated structures, including tanks, pipes and machinery bulkheads. A missing map
is unknown, whereas a documented complete empty inventory is affirmative data.

The caller constructs the CTU envelope at the actual placement, including its
actual floor from the heights below, external length/width/height and any OOG
extension. ``Cell.vcg_base_m`` above the bottom tier is nominal and cannot prove
actual clearance. The geometry is axis aligned, as are the vessel's heat-source
regions and container placements; no point-centre approximation is used.

These assessors return True (proved compliant), False (proved violation), or
None (insufficient evidence). They never supply shipment protection flags.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from math import isclose, isfinite, sqrt
from typing import Literal, Protocol

from pydantic import BaseModel

from stowage.conventions import cells_of_slot, parse_slot
from stowage.rules.imdg.scenarios import BindingContext, provenance_matches


@dataclass(frozen=True)
class PlacementEvidence:
    """Public/synthetic source and clause tied to a named assessed scenario."""

    document: str
    clause: str
    scenario_id: str

    def __post_init__(self):
        if any(not isinstance(value, str) or not value.strip()
               for value in (self.document, self.clause, self.scenario_id)):
            raise ValueError('placement evidence requires document, clause and scenario_id')


@dataclass(frozen=True)
class BoxEnvelope:
    """Closed three-dimensional region in the vessel's metre coordinates."""

    x_min_m: float
    x_max_m: float
    y_min_m: float
    y_max_m: float
    z_min_m: float
    z_max_m: float

    def __post_init__(self):
        for axis in 'xyz':
            low, high = getattr(self, f'{axis}_min_m'), getattr(self, f'{axis}_max_m')
            if (isinstance(low, bool) or isinstance(high, bool)
                    or not isinstance(low, (float, int)) or not isinstance(high, (float, int))
                    or not isfinite(low) or not isfinite(high) or low > high):
                raise ValueError('envelope bounds must be finite, numeric and ordered')


ExclusionKind = Literal['accommodation', 'air_intake', 'machinery_space', 'enclosed_work_area']
_EXCLUSION_KINDS = frozenset(('accommodation', 'air_intake', 'machinery_space', 'enclosed_work_area'))


@dataclass(frozen=True)
class ExclusionRegion:
    id: str
    kind: ExclusionKind
    bounds: BoxEnvelope

    def __post_init__(self):
        if not self.id or self.kind not in _EXCLUSION_KINDS or not isinstance(self.bounds, BoxEnvelope):
            raise ValueError('invalid living-quarters exclusion region')


@dataclass(frozen=True)
class HeatRegion:
    """Vessel heat-source geometry plus assessed IMDG temperature applicability.

    ``liable_to_exceed_55_c`` concerns a surface's potential temperature during
    the planned voyage, not an instantaneous reading. Exactly 55 C is not above
    the threshold. The existing E8 heat-clearance parameter is not used here.
    Bound vessel sources retain ``source_kind`` and ``actual_geometry``. Their
    outer bounds are enclosing envelopes: section samples can prove a close
    witness but cannot prove unsampled space clear. Legacy standalone facts
    without either field explicitly describe rectangular solids by bounds.
    """

    id: str
    bounds: BoxEnvelope
    liable_to_exceed_55_c: bool | None = None
    provenance: PlacementEvidence | None = None
    actual_geometry: object | None = None
    source_kind: str | None = None

    def __post_init__(self):
        if not self.id or not isinstance(self.bounds, BoxEnvelope):
            raise ValueError('invalid heat source region')
        _check_bool(self.liable_to_exceed_55_c)


class PlacementGeometryInterface(Protocol):
    heat_sources: Sequence[HeatRegion]
    heat_map_complete: bool | None
    heat_map_provenance: PlacementEvidence | None
    living_quarters_regions: Sequence[ExclusionRegion]
    living_quarters_map_complete: bool | None
    living_quarters_map_provenance: PlacementEvidence | None


@dataclass(frozen=True)
class PlacementGeometry:
    heat_sources: tuple[HeatRegion, ...] = ()
    heat_map_complete: bool | None = None
    heat_map_provenance: PlacementEvidence | None = None
    living_quarters_regions: tuple[ExclusionRegion, ...] = ()
    living_quarters_map_complete: bool | None = None
    living_quarters_map_provenance: PlacementEvidence | None = None

    def __post_init__(self):
        _check_bool(self.heat_map_complete)
        _check_bool(self.living_quarters_map_complete)
        for regions in (self.heat_sources, self.living_quarters_regions):
            ids = [region.id for region in regions]
            if len(ids) != len(set(ids)):
                raise ValueError('duplicate geometry region id')


@dataclass(frozen=True)
class SunlightAssessment:
    """Placement/voyage-specific SW1 sunlight facts, separate from heat mapping.

    Uncontained packages on deck require shade. For cargo inside a CTU, an
    assessor must establish whether the goods and voyage require precautions;
    if so, exposure reduction must be evidenced for this actual placement.
    This CTU condition applies under deck as well: a hatchless hold need not be
    sheltered. A CTU's closure or underdeck placement proves none of these facts.
    """

    package_inside_ctu: bool | None = None
    precautions_required: bool | None = None
    exposure_reduced: bool | None = None
    shaded: bool | None = None
    provenance: PlacementEvidence | None = None

    def __post_init__(self):
        for value in (self.package_inside_ctu, self.precautions_required, self.exposure_reduced, self.shaded):
            _check_bool(value)


def _check_bool(value):
    if value is not None and not isinstance(value, bool):
        raise ValueError('assessment flags must be bool or None')


def envelope_distance(a: BoxEnvelope, b: BoxEnvelope) -> float:
    """Shortest Euclidean distance between closed 3D envelopes, metres."""
    return sqrt(sum(max(0.0, getattr(a, f'{axis}_min_m') - getattr(b, f'{axis}_max_m'),
                        getattr(b, f'{axis}_min_m') - getattr(a, f'{axis}_max_m')) ** 2
                    for axis in 'xyz'))


def _below(distance: float, required: float) -> bool:
    # Avoid a 5.3 - 2.9 floating-point subtraction rejecting exact 2.4 m.
    return distance < required and not isclose(distance, required, rel_tol=0, abs_tol=1e-9)


def assess_sw2(envelope: BoxEnvelope, geometry: PlacementGeometryInterface | None, *,
               context: BindingContext | None = None, registry=None) -> bool | None:
    """SW2: minimum 3 m to each of the four living/work-area region types."""
    if geometry is None or not provenance_matches(geometry.living_quarters_map_provenance, context, registry):
        return None
    if any(_below(envelope_distance(envelope, region.bounds), 3.0)
           for region in geometry.living_quarters_regions):
        return False
    return True if geometry.living_quarters_map_complete is True else None


def _assess_structures(envelope, geometry, context, registry):
    if geometry is None or not provenance_matches(geometry.heat_map_provenance, context, registry):
        return None
    unknown = geometry.heat_map_complete is not True
    for source in geometry.heat_sources:
        if source.provenance is not None and not provenance_matches(source.provenance, context, registry):
            return None
        if source.actual_geometry is not None:
            from stowage.rules.imdg.heat_geometry import heat_source_clearance

            clearance = heat_source_clearance(envelope, source.actual_geometry,
                                               source_bounds=source.bounds)
        elif not _below(envelope_distance(envelope, source.bounds), 2.4):
            # An enclosing envelope proves this far bound even when it cannot
            # describe the source's occupied solid near the CTU.
            clearance = True
        else:
            clearance = False if source.source_kind is None else None
        if clearance is True:
            continue
        if not isinstance(source.provenance, PlacementEvidence) or source.liable_to_exceed_55_c is None:
            unknown = True
        elif source.liable_to_exceed_55_c is True:
            if clearance is False:
                return False
            if clearance is None:
                unknown = True
    return None if unknown else True


def _assess_sunlight(on_deck, sunlight, context, registry):
    if sunlight is None or not provenance_matches(sunlight.provenance, context, registry):
        return None
    if sunlight.package_inside_ctu is False:
        if on_deck is False:
            return True
        return sunlight.shaded if on_deck is True else None
    if sunlight.package_inside_ctu is True:
        if sunlight.precautions_required is False:
            return True
        if sunlight.precautions_required is True:
            return sunlight.exposure_reduced
    return None


def assess_sw1(envelope: BoxEnvelope, geometry: PlacementGeometryInterface | None, *,
               on_deck: bool | None, sunlight: SunlightAssessment | None = None,
               context: BindingContext | None = None, registry=None) -> bool | None:
    """SW1: 2.4 m from >55 C liable surfaces AND applicable sunlight protection."""
    structure = _assess_structures(envelope, geometry, context, registry)
    sun = _assess_sunlight(on_deck, sunlight, context, registry)
    if structure is False or sun is False:
        return False
    return True if structure is True and sun is True else None


def _evidence(value):
    if value is None or isinstance(value, PlacementEvidence):
        return value
    if not isinstance(value, Mapping):
        raise ValueError('evidence must be an object')
    return PlacementEvidence(**value)


def _bounds(value):
    if isinstance(value, BoxEnvelope):
        return value
    if not isinstance(value, Mapping):
        raise ValueError('bounds must be an object')
    return BoxEnvelope(**value)


def _regions(value):
    if not isinstance(value, (list, tuple)):
        raise ValueError('regions must be a list')
    return value


def geometry_from_facts(value, *, heat_sources=None) -> PlacementGeometry | None:
    """Parse mapping/dataclass/Pydantic geometry facts; malformed is unknown.

    When ``heat_sources`` is supplied, it is the actual ``Vessel.heat_sources``
    inventory. Every source id must match; any provided bounds must match too.
    The JSON heat-source rows may then contain only id, temperature applicability
    and provenance. Thus an override cannot move/remove the vessel's sources.
    """
    try:
        if isinstance(value, BaseModel):
            value = value.model_dump(mode='python')
        if isinstance(value, PlacementGeometry):
            geometry = value
        elif isinstance(value, Mapping):
            hot = []
            vessel_sources = None if heat_sources is None else {source.id: source for source in heat_sources}
            for source in _regions(value.get('heat_sources', [])):
                source = dict(source)
                if vessel_sources is not None:
                    vessel_source = vessel_sources[source['id']]
                    actual_bounds = BoxEnvelope(**{f'{axis}_{side}_m': getattr(vessel_source, f'{axis}_{side}_m')
                                                   for axis in 'xyz' for side in ('min', 'max')})
                    if 'bounds' in source and _bounds(source['bounds']) != actual_bounds:
                        return None
                    source['bounds'] = actual_bounds
                hot.append(HeatRegion(**{**source, 'bounds': _bounds(source['bounds']),
                                         'provenance': _evidence(source.get('provenance'))}))
            excluded = tuple(ExclusionRegion(**{**dict(region), 'bounds': _bounds(region['bounds'])})
                             for region in _regions(value.get('living_quarters_regions', [])))
            geometry = PlacementGeometry(
                heat_sources=tuple(hot), heat_map_complete=value.get('heat_map_complete'),
                heat_map_provenance=_evidence(value.get('heat_map_provenance')),
                living_quarters_regions=excluded,
                living_quarters_map_complete=value.get('living_quarters_map_complete'),
                living_quarters_map_provenance=_evidence(value.get('living_quarters_map_provenance')),
            )
        else:
            return None
        # SW2-only facts need not provide a heat map. If any heat assessment is
        # claimed, its inventory must bind to every actual vessel heat source.
        if heat_sources is not None and (geometry.heat_sources or geometry.heat_map_complete is not None
                                         or geometry.heat_map_provenance is not None):
            sources_by_id = {source.id: source for source in heat_sources}
            actual = {source.id: BoxEnvelope(**{f'{axis}_{side}_m': getattr(source, f'{axis}_{side}_m')
                                               for axis in 'xyz' for side in ('min', 'max')})
                      for source in heat_sources}
            declared = {source.id: source.bounds for source in geometry.heat_sources}
            if actual != declared:
                return None
            bound = []
            for source in geometry.heat_sources:
                physical = sources_by_id[source.id]
                if isinstance(physical, BaseModel):
                    physical = type(physical).model_validate(physical.model_dump(mode='python'))
                shape = getattr(physical, 'actual_geometry', None)
                if source.actual_geometry is not None:
                    supplied = (source.actual_geometry.model_dump(mode='python')
                                if isinstance(source.actual_geometry, BaseModel) else source.actual_geometry)
                    existing = shape.model_dump(mode='python') if isinstance(shape, BaseModel) else shape
                    if supplied != existing:
                        return None
                kind = getattr(physical, 'kind', None)
                if source.source_kind is not None and source.source_kind != kind:
                    return None
                bound.append(replace(source, actual_geometry=shape, source_kind=kind))
            geometry = replace(geometry, heat_sources=tuple(bound))
        return geometry
    except (TypeError, ValueError, KeyError, AttributeError):
        return None


def sunlight_from_facts(value) -> SunlightAssessment | None:
    """Parse mapping/dataclass/Pydantic sunlight facts, retaining unknowns."""
    try:
        if isinstance(value, BaseModel):
            value = value.model_dump(mode='python')
        if isinstance(value, SunlightAssessment):
            return value
        if not isinstance(value, Mapping):
            return None
        return SunlightAssessment(**{**value, 'provenance': _evidence(value.get('provenance'))})
    except (TypeError, ValueError):
        return None


def _object_bounds(value):
    return BoxEnvelope(**{f'{axis}_{side}_m': getattr(value, f'{axis}_{side}_m')
                          for axis in 'xyz' for side in ('min', 'max')})


def _geometry_from_indexed_vessel(vessel, metadata, *, context: BindingContext | None = None,
                                 registry=None) -> PlacementGeometry | None:
    """Bind IMDG map declarations to structures used by the rest of the vessel.

    Metadata has the existing heat-map fields, but source rows supply only id,
    liability, provenance and optional ``tank_id`` linking a heated tank whose
    physical source id differs. ``living_quarters_sources`` rows contain ``id``,
    ``kind`` and ``fixed_load_index``. They classify existing non-cargo fixed
    silhouettes (also used by visibility); cargo silhouettes cannot be used.
    All engine-room heat-source envelopes are included as machinery spaces.
    Independently drawn exclusion bounds cannot override these shared solids.

    This fallback is used only when the model has no named exclusion catalog.
    A bridge point or 2D windage rectangle cannot establish a 3D accommodation
    extent: a complete indexed inventory must contain a shared solid consistent
    with the bridge's known x/z coordinates. Heated tanks retain their E8 source
    geometry and longitudinal extent. Every non-cargo fixed silhouette must be
    classified when claiming a complete indexed SW2 catalog, so an existing
    deckhouse cannot be silently omitted.
    A completeness declaration is a vessel-design declaration, not a location
    clearance. All declaration provenance must resolve to the assessed scope.
    APPROXIMATION: synthetic role declarations classify shared non-cargo solids
    in legacy fixtures without named component roles; their bounds are always the
    same physical solids consumed by visibility, never separately drawn regions.
    """
    try:
        if isinstance(metadata, BaseModel):
            metadata = metadata.model_dump(mode='python')
        if not isinstance(metadata, Mapping) or context is None or vessel.id != context.vessel_id:
            return None
        # No second map may quietly coexist with the component-bound catalog.
        if 'living_quarters_regions' in metadata:
            return None
        for prefix in ('heat', 'living_quarters'):
            if any(key in metadata for key in (prefix + '_map_complete', prefix + '_map_provenance')):
                if not provenance_matches(metadata.get(prefix + '_map_provenance'), context, registry):
                    return None
        heat_metadata = dict(metadata)
        heat_metadata.pop('living_quarters_sources', None)
        tank_links = {}
        if 'heat_sources' in metadata:
            heat_metadata['heat_sources'] = []
            for raw in _regions(metadata['heat_sources']):
                raw = dict(raw)
                if 'tank_id' in raw:
                    tank_id = raw.pop('tank_id')
                    if not isinstance(tank_id, str) or tank_id in tank_links:
                        return None
                    tank_links[tank_id] = raw['id']
                heat_metadata['heat_sources'].append(raw)
        geometry = geometry_from_facts(heat_metadata, heat_sources=vessel.heat_sources)
        if geometry is None:
            return None
        for source in geometry.heat_sources:
            if source.provenance is not None and not provenance_matches(source.provenance, context, registry):
                return None
        actual_heat = {source.id: source for source in vessel.heat_sources}
        actual_tanks = {tank.id: tank for tank in vessel.tanks}
        if any(tank_id not in actual_tanks or not actual_tanks[tank_id].heated for tank_id in tank_links):
            return None
        assess_heat = any(key in metadata for key in ('heat_sources', 'heat_map_complete', 'heat_map_provenance'))
        # SW2 needs machinery geometry, not thermal tank-applicability evidence.
        # Validate tank/source links only when this record claims a heat map.
        for tank in vessel.tanks if assess_heat else ():
            if not tank.heated:
                continue
            source = actual_heat.get(tank_links.get(tank.id, tank.id))
            if (source is None or source.kind != 'heated_tank'
                    or source.x_min_m != tank.x_aft_m or source.x_max_m != tank.x_fore_m):
                return None
        regions = []
        represented_non_cargo = set()
        for raw in _regions(metadata.get('living_quarters_sources', [])):
            if not isinstance(raw, Mapping) or set(raw) != {'id', 'kind', 'fixed_load_index'}:
                return None
            index = raw['fixed_load_index']
            if type(index) is not int or index < 0:
                return None
            component = vessel.fixed_loads[index]
            if component.kind != 'non_cargo' or component.silhouette is None:
                return None
            represented_non_cargo.add(index)
            regions.append(ExclusionRegion(raw['id'], raw['kind'], _object_bounds(component.silhouette)))
        if geometry.living_quarters_map_complete is True:
            for index, component in enumerate(vessel.fixed_loads):
                if component.kind != 'cargo' and (component.kind != 'non_cargo'
                                                  or component.silhouette is None
                                                  or index not in represented_non_cargo):
                    return None
        for source in vessel.heat_sources:
            if source.kind == 'engine_room':
                regions.append(ExclusionRegion(source.id, 'machinery_space', _object_bounds(source)))
        bridge = getattr(vessel, 'bridge', None)
        if geometry.living_quarters_map_complete is True and bridge is not None:
            # Necessary consistency only: Bridge exposes x and eye-z, not a
            # complete deckhouse solid or a transverse conning coordinate.
            # Inclusive boundaries accommodate x at the bridge front.
            if not any(region.kind in {'accommodation', 'enclosed_work_area'}
                       and region.bounds.x_min_m <= bridge.x_m <= region.bounds.x_max_m
                       and region.bounds.z_min_m <= bridge.eye_z_m <= region.bounds.z_max_m
                       for region in regions):
                return None
        return replace(geometry, living_quarters_regions=tuple(regions))
    except (AttributeError, IndexError, KeyError, TypeError, ValueError):
        return None


def _map_metadata(metadata, provision):
    """Remove irrelevant maps and typed defaults, preserving explicit unknowns."""
    if isinstance(metadata, BaseModel):
        metadata = metadata.model_dump(mode='python')
    if not isinstance(metadata, Mapping) or provision not in {None, 'SW1', 'SW2'}:
        return None
    result = dict(metadata)
    for prefix, sources, code in (('heat', 'heat_sources', 'SW1'),
                                  ('living_quarters', 'living_quarters_sources', 'SW2')):
        keys = (sources, prefix + '_map_complete', prefix + '_map_provenance')
        defaults = (not result.get(sources) and result.get(keys[1]) is None and result.get(keys[2]) is None)
        if provision is not None and provision != code or defaults:
            for key in keys:
                result.pop(key, None)
    return result


def _named_regions(vessel):
    """Recheck mutable named design records against their actual model sources."""
    from stowage.model.vessel import ExclusionRegion as ModelExclusionRegion

    result = []
    roles = {'deckhouse': 'accommodation', 'bridge': 'enclosed_work_area',
             'heat_source': 'machinery_space', 'opening': 'air_intake',
             'forecastle': 'enclosed_work_area', 'steering_gear': 'enclosed_work_area',
             'machinery_space': 'machinery_space'}
    heat = {source.id: source for source in vessel.heat_sources}
    if len(heat) != len(vessel.heat_sources):
        raise ValueError('duplicate actual heat-source ids')
    for raw in vessel.exclusion_regions:
        region = ModelExclusionRegion.model_validate(raw.model_dump(mode='python') if isinstance(raw, BaseModel) else raw)
        if region.kind != roles[region.source_kind]:
            raise ValueError('named source role conflicts with its component')
        bounds = _object_bounds(region.bounds)
        if region.source_kind == 'heat_source':
            actual = heat.get(region.source_id)
            if actual is None or actual.kind != 'engine_room' or _object_bounds(actual) != bounds:
                raise ValueError('machinery region is detached from the actual engine-room solid')
        elif region.source_kind == 'bridge':
            bridge = getattr(vessel, 'bridge', None)
            if (bridge is None or region.source_id != 'bridge'
                    or not bounds.x_min_m <= bridge.x_m <= bounds.x_max_m
                    or not bounds.z_min_m <= bridge.eye_z_m <= bounds.z_max_m):
                raise ValueError('bridge enclosure is detached from the actual bridge')
        elif region.source_kind == 'deckhouse' and region.source_id != 'deckhouse':
            raise ValueError('accommodation is detached from the shared deckhouse')
        elif region.source_kind in {'forecastle', 'steering_gear'} and region.source_id != region.source_kind:
            raise ValueError('work area is detached from its named shared structure')
        elif region.source_kind == 'machinery_space':
            cores = [source for source in vessel.heat_sources if source.kind == 'engine_room']
            if region.source_id != 'machinery_space' or not cores or any(
                    getattr(bounds, f'{axis}_min_m') > getattr(core, f'{axis}_min_m')
                    or getattr(bounds, f'{axis}_max_m') < getattr(core, f'{axis}_max_m')
                    for core in cores for axis in 'xyz'):
                raise ValueError('machinery/service room must enclose the actual hot core')
        result.append(ExclusionRegion(region.id, region.kind, bounds))
    ids = {region.id for region in result}
    if len(ids) != len(result):
        raise ValueError('duplicate named exclusion ids')
    # An actual engine-room source cannot disappear merely because the declared
    # named inventory forgot its machinery role. Its bounds remain shared.
    represented_heat = {region.source_id for region in vessel.exclusion_regions
                        if region.source_kind == 'heat_source'}
    for source in vessel.heat_sources:
        if source.kind == 'engine_room' and source.id not in represented_heat:
            if source.id in ids:
                raise ValueError('engine-room id conflicts with another named exclusion')
            result.append(ExclusionRegion(source.id, 'machinery_space', _object_bounds(source)))
            ids.add(source.id)
    return tuple(result)


def _named_inventory(vessel, metadata, regions):
    """Resolve declared references; completeness is synthetic design evidence.

    The named design is shared with the model, never a second drawing of the
    ship. Added non-cargo fixed loads also need explicit indexed references;
    inheriting a complete original design cannot certify those additions.
    """
    by_id = {region.id: region for region in regions}
    actual_ids = {region.id for region in vessel.exclusion_regions}
    represented, indexed = set(), set()
    extra = []
    for source in metadata.living_quarters_sources:
        if source.region_id is not None:
            region = by_id.get(source.region_id)
            if (region is None or source.region_id not in actual_ids
                    or region.kind != source.kind or source.region_id in represented):
                raise ValueError('named exclusion reference is missing, repeated or has a mismatched role')
            represented.add(source.region_id)
        else:
            index = source.fixed_load_index
            if type(index) is not int or index < 0 or index in indexed:
                raise ValueError('invalid indexed non-cargo source')
            component = vessel.fixed_loads[index]
            if component.kind != 'non_cargo' or component.silhouette is None or source.id in by_id:
                raise ValueError('indexed source must reference an additional shared non-cargo solid')
            indexed.add(index)
            region = ExclusionRegion(source.id, source.kind, _object_bounds(component.silhouette))
            extra.append(region)
            by_id[source.id] = region
    if metadata.living_quarters_map_complete is True:
        if represented != actual_ids:
            raise ValueError('complete synthetic inventory must reference every named exclusion')
        if any(load.kind != 'cargo' and (load.kind != 'non_cargo' or load.silhouette is None
                                        or index not in indexed)
               for index, load in enumerate(vessel.fixed_loads)):
            raise ValueError('complete design cannot clear unclassified additional fixed loads')
    return (*regions, *extra)


def geometry_from_vessel(vessel, metadata, *, context: BindingContext | None = None,
                         registry=None, provision: str | None = None) -> PlacementGeometry | None:
    """Consume actual named vessel geometry, with the indexed legacy fallback.

    ``provision='SW1'`` or ``'SW2'`` binds only evidence relevant to that rule;
    unknown/foreign evidence for the other map cannot deny a valid assessment.
    Unclaimed typed fields (None/empty) are unknown, not a second assessment.
    With no provision specified, named maps bind independently; legacy indexed
    fixtures retain their combined map-validation behavior.

    A nonempty ``Vessel.exclusion_regions`` catalog is authoritative: its typed
    ``imdg_placement_geometry`` declaration must match this input, and a legacy
    indexed or independent map cannot replace it. The named catalog supplies
    shared design geometry without inserting the own wheelhouse as an opaque
    FixedLoad around its conning eye. Explicit completeness False remains False;
    catalog adoption never promotes it. A complete declaration is honoured only
    when its references cover every named region and every additional non-cargo
    component. The fleet's complete catalogue is labelled synthetic design; it
    is not a completeness claim about a real ship.

    Design evidence retains exact external scenario registration for the current
    shipment/entry/vessel/voyage/slot. A design scenario id is not automatically
    converted into operational evidence, and shipment declarations are untouched.
    """
    try:
        normalized = _map_metadata(metadata, provision)
        if normalized is None or context is None or vessel.id != context.vessel_id:
            return None
        named = getattr(vessel, 'exclusion_regions', ())
        if not isinstance(named, (list, tuple)):
            return None
        if not named:
            return _geometry_from_indexed_vessel(vessel, normalized, context=context, registry=registry)

        from stowage.model.vessel import ImdgPlacementGeometry

        actual_metadata = getattr(vessel, 'imdg_placement_geometry', None)
        if actual_metadata is None:
            return None
        # Revalidate mutable typed records and compare declarations before
        # filtering irrelevant evidence. An override cannot make False complete.
        actual_raw = actual_metadata.model_dump(mode='python') if isinstance(actual_metadata, BaseModel) else actual_metadata
        supplied_raw = metadata.model_dump(mode='python') if isinstance(metadata, BaseModel) else metadata
        actual = ImdgPlacementGeometry.model_validate(actual_raw)
        supplied = ImdgPlacementGeometry.model_validate(supplied_raw)
        if actual != supplied:
            return None
        regions = _named_regions(vessel)
        thermal = {key: value for key, value in normalized.items() if key.startswith('heat_')}
        geometry = None
        if thermal:
            actual_heat = {source.id: source for source in vessel.heat_sources}
            consistent = all(source.id in actual_heat
                             and source.liable_to_exceed_55_c == actual_heat[source.id].liable_to_exceed_55_c
                             and source.tank_id == actual_heat[source.id].tank_id
                             for source in supplied.heat_sources)
            if consistent:
                geometry = _geometry_from_indexed_vessel(vessel, thermal, context=context, registry=registry)
        if provision == 'SW1':
            return geometry
        if provision != 'SW1':
            evidence = normalized.get('living_quarters_map_provenance')
            if not provenance_matches(evidence, context, registry):
                # Default callers can consume whichever map was independently
                # bound. An unbound SW2 map stays unknown, without poisoning
                # separately registered physical heat evidence (or vice versa).
                return geometry
            complete = normalized.get('living_quarters_map_complete')
            _check_bool(complete)
            regions = _named_inventory(vessel, supplied, regions)
            bridge = getattr(vessel, 'bridge', None)
            if complete is True and bridge is not None:
                if not any(region.kind in {'accommodation', 'enclosed_work_area'}
                           and region.bounds.x_min_m <= bridge.x_m <= region.bounds.x_max_m
                           and region.bounds.z_min_m <= bridge.eye_z_m <= region.bounds.z_max_m
                           for region in regions):
                    return None
            geometry = replace(geometry or PlacementGeometry(), living_quarters_regions=regions,
                               living_quarters_map_complete=complete,
                               living_quarters_map_provenance=_evidence(evidence))
        return geometry
    except (AttributeError, IndexError, KeyError, TypeError, ValueError):
        return None


def covered_under_deck(vessel, slot: str, covers_off=()) -> bool | None:
    """Derive shelter from actual, reciprocally associated secured hatch covers.

    Every physical support of a long CTU must be in a hold stack listed by its
    actual cover. An under-deck address alone supplies no shelter evidence.
    Missing or inconsistent catalogs are unknown; an explicitly removed cover
    or a deck position is exposed.
    """
    try:
        if parse_slot(slot).on_deck:
            return False
        cells = {cell.id: cell for cell in vessel.cells}
        stacks = {stack.id: stack for stack in vessel.stacks}
        covers = {cover.id: cover for cover in vessel.hatch_covers}
        if len(cells) != len(vessel.cells) or len(stacks) != len(vessel.stacks) or len(covers) != len(vessel.hatch_covers):
            return None
        exposed = False
        for cell_id in cells_of_slot(slot):
            stack = stacks[cells[cell_id].stack_id]
            cover = covers.get(stack.hatch_cover_id)
            if stack.on_deck or cover is None or stack.id not in cover.hold_stack_ids:
                return None
            if cover.id in covers_off:
                exposed = True
        return not exposed
    except (AttributeError, KeyError, TypeError, ValueError):
        return None


def sunlight_from_vessel(vessel, slot: str, declaration, *, covers_off=(),
                         context: BindingContext | None = None, registry=None) -> SunlightAssessment | None:
    """Combine declared goods/voyage applicability with actual vessel shelter.

    Input fields are ``package_inside_ctu``, ``precautions_required`` and
    ``provenance``. Per-slot shading/exposure outcome flags are rejected.
    ASSUMPTION: for goods/voyages requiring reduced sunlight exposure, accept
    only shelter under a secured, associated hatch cover. Exposed/deck positions
    are conservatively rejected; crediting geometric shade on deck requires a
    future voyage solar-path/occlusion model, not a generator assertion.
    """
    try:
        if isinstance(declaration, BaseModel):
            declaration = declaration.model_dump(mode='python')
        if (not isinstance(declaration, Mapping)
                or set(declaration) - {'package_inside_ctu', 'precautions_required', 'provenance'}
                or context is None or slot != context.slot or vessel.id != context.vessel_id):
            return None
        parsed = sunlight_from_facts(declaration)
        if parsed is None or not provenance_matches(parsed.provenance, context, registry):
            return None
        # Applicability is established before seeking protection geometry. No
        # shade/exposure conclusion is invented when the relevant clause does
        # not apply: CTUs with declared no precautions, or uncontained packages
        # under deck, require no hatch-shelter evidence for this sunlight check.
        if (parsed.package_inside_ctu is True and parsed.precautions_required is False
                or parsed.package_inside_ctu is False and not parse_slot(slot).on_deck):
            return parsed
        shelter = covered_under_deck(vessel, slot, covers_off)
        if shelter is None:
            return None
        return replace(parsed, shaded=shelter, exposure_reduced=shelter)
    except (AttributeError, TypeError, ValueError):
        return None


def any_admissible_vertical_placement(envelope: BoxEnvelope, geometry: PlacementGeometryInterface | None,
                                     bottom_min_m: float, bottom_max_m: float, *, provision: str,
                                     on_deck: bool | None = None, sunlight: SunlightAssessment | None = None,
                                     context: BindingContext | None = None, registry=None) -> bool:
    """Optimistic necessary screen: retain if ANY admissible vertical envelope can pass.

    This is not a final checker verdict. Unknown geometry/evidence retains the
    candidate. For fixed x/y and fixed height, each exclusion produces one open
    forbidden interval in bottom-z. Reject only if the union of known violation
    intervals covers the entire range; retain any gap, including its endpoints.
    The screen slightly shrinks these intervals for numerical conservatism,
    preserving checker-safe gaps at its floating-point tolerance.
    A union of broader admissible ranges is conservative for discrete heights.
    The full checker must still evaluate the actual supported box envelope.
    """
    try:
        if (not isfinite(bottom_min_m) or not isfinite(bottom_max_m) or bottom_min_m > bottom_max_m
                or geometry is None or provision not in {'SW1', 'SW2'}):
            return True
        height = envelope.z_max_m - envelope.z_min_m
        regions = geometry.living_quarters_regions if provision == 'SW2' else geometry.heat_sources
        evidence = geometry.living_quarters_map_provenance if provision == 'SW2' else geometry.heat_map_provenance
        if not provenance_matches(evidence, context, registry):
            return True
        if provision == 'SW1':
            sun = _assess_sunlight(on_deck, sunlight, context, registry)
            if sun is not True:
                return sun is not False
        # _below allows a 1e-9 radius tolerance. Use a still smaller forbidden
        # radius so round-off can only retain extra candidates, never mask a
        # checker-safe height. The final checker keeps the actual threshold.
        required = (3.0 if provision == 'SW2' else 2.4) - 2e-9
        forbidden = []
        for region in regions:
            if provision == 'SW1':
                if region.provenance is not None and not provenance_matches(region.provenance, context, registry):
                    return True
                if region.liable_to_exceed_55_c is not True or region.provenance is None:
                    continue
                if region.actual_geometry is not None or region.source_kind is not None:
                    # A sampled tank envelope is an enclosing bound, not a
                    # continuously occupied solid. It cannot supply a forbidden
                    # vertical interval. Keep possible heights for the actual
                    # shape assessor unless the declared source is rectangular.
                    shape = region.actual_geometry
                    name = shape.get('shape') if isinstance(shape, Mapping) else getattr(shape, 'shape', None)
                    if name != 'rectangular_prism':
                        continue
                    from stowage.rules.imdg.heat_geometry import heat_source_distance

                    if heat_source_distance(envelope, shape, source_bounds=region.bounds) is None:
                        continue
            bounds = region.bounds
            horizontal_squared = sum(max(0.0, getattr(envelope, f'{axis}_min_m') - getattr(bounds, f'{axis}_max_m'),
                                         getattr(bounds, f'{axis}_min_m') - getattr(envelope, f'{axis}_max_m')) ** 2
                                     for axis in 'xy')
            if horizontal_squared >= required ** 2:
                continue
            vertical_gap = sqrt(required ** 2 - horizontal_squared)
            forbidden.append((bounds.z_min_m - height - vertical_gap, bounds.z_max_m + vertical_gap))
        cursor = bottom_min_m
        for low, high in sorted(forbidden):
            if high <= cursor:
                continue
            if low >= cursor:
                return True
            cursor = high
            if cursor > bottom_max_m:
                return False
        return True
    except (AttributeError, TypeError, ValueError):
        return True
