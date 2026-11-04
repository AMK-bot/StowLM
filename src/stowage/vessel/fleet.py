"""Build the synthetic feeder fleet from its auditable parameter records.

A TEU is one twenty-foot space: count physical odd-bay cells once, even when
those cells can instead support a forty- or forty-five-foot box. Stack and
hatch membership is copied explicitly rather than inferred from proximity.
Full builds include the production S11A permissible still-water shear-force
and bending-moment stations: these bound the hull's longitudinal load effects.
Their synthetic design assumptions are in ``experiments/strength/README.md``.
See ``params/fleet/README.md`` for the source audit and construction assumptions,
and ``stowage.model.vessel`` for the cell/stack and half-container mass model.

APPROXIMATION: the feeder arrangements are synthetic designs, not certified
loading manuals. Their assumptions and public reference URLs accompany each
Vessel in ``sources``. ``load_ship_params`` retains the complete input-only
metadata (high-cube flags, tank solids, buoyant envelope and hull form) that
cannot be represented by the frozen Vessel schema. Physics builders receive
that complete record, never a projection of the geometry models.

DG class lists come from the synthetic SOLAS II-2/19 Document of Compliance
(DoC): this declares which dangerous-goods classes each cargo space permits.
Stack class lists must agree with their declared DoC area. Schema 1.1 preserves
area ids, permitted packing forms and equipment assumptions as ``dg_doc_areas``,
including inherited document references and each area's synthetic assumptions.
Class authorization alone does not establish packing-form or equipment compliance.
Downflooding openings and lightship mass/centres/distribution are passed intact
to the hull builder; they are never replaced with former synthetic defaults.
D50 voyage_policy groups the built consumable tanks by their declared kinds,
records D57's common proportional remaining fraction and retains the sourced
10%-of-departure arrival default. The legacy order is documentation only.
No tank, contents or fixed mass is added.
Geometry-only builds have a diagnostic empty policy on their omitted tank catalog.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any, TypeVar

from pydantic import BaseModel

from stowage.conventions import canonical_vessel_id
from stowage.model import Vessel
from stowage.model.vessel import (
    BallastSystem, Bridge, Cell, DgDocArea, ExclusionRegion, FixedLoad, HatchCover, HeatSource,
    ImdgPlacementGeometry, Stack, TankPair,
)
from stowage.toml_cache import load_bytes as load_toml_bytes
from stowage.toml_cache import load_value as load_toml_value
from stowage.params import list_param_files, params_dir

_Model = TypeVar("_Model", bound=BaseModel)


def list_fleet() -> list[str]:
    """Return sorted feeder parameter names, without file extensions."""
    return list_param_files("fleet")


def _parameter_bytes(name: str) -> bytes:
    name = canonical_vessel_id(name)
    # Membership prevents names supplied by callers escaping the parameter dir.
    if name not in list_fleet():
        raise ValueError(f"unknown vessel {name!r}; available: {', '.join(list_fleet())}")
    return (params_dir("fleet") / f"{name}.toml").read_bytes()


def load_ship_params(name: str) -> dict[str, Any]:
    """Read a fresh, complete parameter tree, including construction metadata.

    Mutating this result does not affect future loads. Units and provenance are
    defined by ``params/fleet/README.md``; current fleet records use bare SI
    fields with inherited table provenance.
    """
    return load_toml_bytes(_parameter_bytes(name))


def _record(model: type[_Model], raw: dict[str, Any]) -> _Model:
    """Project a parameter record onto the frozen model's declared fields."""
    return model.model_validate({key: value for key, value in raw.items() if key in model.model_fields})


def _stack_records(ship_params: dict[str, Any]) -> list[Stack]:
    """Map supported DoC fields, rejecting contradictory declared permissions."""
    records = ship_params["stacks"]
    document = ship_params.get("dg_document")
    if document is not None:
        areas = {area["id"]: area for area in document["areas"]}
        if len(areas) != len(document["areas"]):
            raise ValueError("DG Document of Compliance has duplicate area ids")
        stacks = {record["id"]: record for record in records}
        membership: dict[str, str] = {}
        for area_id, area in areas.items():
            for sid in area["stack_ids"]:
                if sid not in stacks or sid in membership:
                    raise ValueError(f"DG area {area_id} has unknown or repeated stack {sid}")
                membership[sid] = area_id
        for record in records:
            sid = record["id"]
            area_id = record.get("dg_doc_area_id")
            if area_id not in areas or membership.get(sid) != area_id:
                raise ValueError(f"stack {sid} disagrees with DG Document of Compliance area membership")
            classes = record.get("dg_classes_allowed")
            if classes is None or set(classes) != set(areas[area_id]["classes_allowed"]):
                raise ValueError(f"stack {sid} disagrees with DG area {area_id} class permissions")
    # Preserve both the class list and the reciprocal schema 1.1 area reference.
    return [_record(Stack, record) for record in records]


def _source_notes(tree: Any) -> set[str]:
    notes: set[str] = set()
    if isinstance(tree, dict):
        for key, value in tree.items():
            if key in {"assumption", "url", "source_definition", "equipment_assumptions"} and isinstance(value, str):
                notes.add(value)
            notes.update(_source_notes(value))
    elif isinstance(tree, list):
        for value in tree:
            notes.update(_source_notes(value))
    return notes


def _doc_sources(record: dict[str, Any]) -> set[str]:
    """Keep source details and guidance, including citations inherited by areas."""
    sources = set(record.get("sources", [])) | _source_notes(record)
    sources.update(record.get("guidance_urls", []))
    source = record.get("source")
    if isinstance(source, str):
        sources.add(source)
    elif isinstance(source, dict):
        sources.add(json.dumps(source, sort_keys=True, ensure_ascii=False))
    return sources


def _dg_doc_areas(ship_params: dict[str, Any], parameter_source: str) -> list[DgDocArea]:
    document = ship_params.get("dg_document")
    if document is None:
        return []
    # Inherit the document evidence, without mixing other areas' local premises.
    inherited = _doc_sources({key: value for key, value in document.items() if key != "areas"})
    inherited.add(parameter_source + " #dg_document")
    return [_record(DgDocArea, {**area, "sources": sorted(inherited | _doc_sources(area))})
            for area in document["areas"]]


def _fixed_load_records(ship_params: dict[str, Any], parameter_source: str) -> tuple[list[FixedLoad], list[str]]:
    """Retain explicit non-cargo declarations without inferring unknown masses.

    FixedLoad has no aggregate provenance field. Keep classification evidence
    in Vessel.sources, bound to the parameter record and unchanged point mass.
    Per-box cargo has its separate typed provenance under D42.
    """
    loads, sources = [], []
    for index, raw in enumerate(ship_params.get("fixed_loads", [])):
        load = _record(FixedLoad, raw) if isinstance(raw, dict) else FixedLoad.model_validate(raw)
        if load.kind == "non_cargo":
            evidence = sorted(note for note in _doc_sources(raw) if note.strip())
            if not evidence:
                raise ValueError(f"non_cargo fixed_loads[{index}] requires classification provenance")
            sources.append("stowage.fixed_non_cargo:" + json.dumps(dict(
                parameter=f"{parameter_source} #fixed_loads[{index}]",
                kind="non_cargo", point_mass=load.as_point_mass(), evidence=evidence,
            ), sort_keys=True))
        loads.append(load)
    return loads, sources


def _exclusion_records(ship_params: dict[str, Any]) -> list[ExclusionRegion]:
    """Bind design roles to the actual visibility, opening and E8 sources.

    A named deckhouse is not an additional opaque fixed-load obstacle around
    its own conning eye. Its x/z projection nevertheless shares the windage
    design; newly declared transverse dimensions remain explicitly synthetic.
    """
    regions = [_record(ExclusionRegion, raw) for raw in ship_params.get("exclusion_regions", [])]
    projections = {row["id"]: row for row in ship_params.get("windage", {}).get("projected_rectangles", [])}
    openings = {row["id"]: row for row in ship_params.get("envelope", {}).get("openings", [])}
    for region in regions:
        if region.source_kind == "deckhouse":
            source = projections.get(region.source_id)
            if source is None or any(getattr(region.bounds, f"{axis}_{side}_m") != source[f"{axis}_{side}_m"]
                                     for axis in "xz" for side in ("min", "max")):
                raise ValueError(f"exclusion region {region.id} disagrees with the shared deckhouse projection")
        elif region.source_kind == "opening":
            source = openings.get(region.source_id)
            if (source is None or source.get("kind") != "engine_room_air_inlet"
                    or any(not getattr(region.bounds, f"{axis}_min_m") <= source[f"{axis}_m"]
                           <= getattr(region.bounds, f"{axis}_max_m") for axis in "xyz")):
                raise ValueError(f"exclusion region {region.id} must contain its actual air intake opening")
        elif region.source_kind in {"forecastle", "steering_gear", "machinery_space"}:
            source = ship_params.get("structures", {}).get(region.source_id)
            if source is None or any(getattr(region.bounds, f"{axis}_{side}_m") != source[f"{axis}_{side}_m"]
                                     for axis in "xyz" for side in ("min", "max")):
                raise ValueError(f"exclusion region {region.id} disagrees with its shared enclosed-work structure")
        # Vessel validates actual HeatSource bounds and the Bridge reference.
    return regions


def _heat_source_records(ship_params: dict[str, Any]) -> list[HeatSource]:
    """Retain the actual heated-tank section shape beside its outer envelope.

    The envelope remains available to existing conservative consumers. Recorded
    Gauss sections do not provide between-station geometry: a future distance
    consumer must not interpolate or replace them with invented clearances.
    """
    tanks = {tank["id"]: tank for tank in ship_params.get("tanks", [])}
    sources = []
    for raw in ship_params.get("heat_sources", []):
        record = dict(raw)
        if record.get("kind") == "heated_tank" and record.get("tank_id") is not None:
            tank = tanks.get(record["tank_id"])
            if tank is None or not tank.get("heated") or "geometry" not in tank:
                raise ValueError(f"heated source {record['id']} requires its actual heated tank geometry")
            if "actual_geometry" in record and record["actual_geometry"] != tank["geometry"]:
                raise ValueError(f"heated source {record['id']} geometry disagrees with its actual tank")
            record["actual_geometry"] = tank["geometry"]
        sources.append(_record(HeatSource, record))
    return sources


def load_vessel(name: str, *, with_hull: bool = True) -> Vessel:
    """Build a feeder, preserving explicit cell capabilities and local limits.

    With ``with_hull=True`` the full parameter tree is handed to task 2.1's
    hull and tank builders. Missing/unimplemented builders propagate their
    error: a geometry-only vessel is never silently substituted. Explicit
    ``with_hull=False`` omits hull and tanks and is suitable for geometry/move
    tests; physics checkers must reject its missing hull data. Whole-vessel
    JSON builds are cached by input, implementation and component version;
    set ``STOWAGE_VESSEL_CACHE=0`` to bypass disk and process caches.
    """
    name = canonical_vessel_id(name)
    payload = _parameter_bytes(name)
    from stowage.vessel.fleet_cache import cached_vessel
    from stowage.rules.catalogue import rule_freeze_v1_enabled

    # Activated manual witnesses participate in the D56 design envelope.
    # Retain the original OFF key while preventing cross-policy memo/disk hits.
    cache_slice = "full.rule-freeze-v1" if rule_freeze_v1_enabled() else "full"
    vessel = cached_vessel(name, slice_name=cache_slice, with_hull=with_hull,
                           params_bytes=payload, build=lambda: _build_vessel(payload, name, with_hull))
    # A long-lived older builder can publish stale JSON under current file hashes.
    # The explicit raw declaration remains authoritative, including its absence.
    if vessel.imdg_ship_type != load_toml_value(payload, "imdg_ship_type"):
        return _build_vessel(payload, name, with_hull)
    return vessel


def _build_vessel(payload: bytes, name: str, with_hull: bool) -> Vessel:
    from stowage.rules.catalogue import rule_freeze_v1_enabled

    ship_params = load_toml_bytes(payload)
    freeze_enabled = rule_freeze_v1_enabled()
    ballast_raw = ship_params.get("ballast_system")
    ballast_system = (_record(BallastSystem, ballast_raw)
                      if freeze_enabled and ballast_raw is not None else None)
    stacks = _stack_records(ship_params)
    parameter_source = f"params/fleet/{name}.toml sha256:{hashlib.sha256(payload).hexdigest()}"
    fixed_loads, fixed_sources = _fixed_load_records(ship_params, parameter_source)
    hull, tanks = None, []
    if with_hull:
        from stowage.vessel.hull import build_hull_model
        from stowage.vessel.tanks import build_tanks
        from stowage.checker.strength_limits import permissible_stations

        hull = build_hull_model(ship_params)
        hull.stations = permissible_stations(ship_params, hull)
        tanks = build_tanks(ship_params)
    from stowage.vessel.fleet_voyage import build_voyage_policy

    voyage_policy = build_voyage_policy(tanks, parameter_source=parameter_source,
                                        vessel_id=ship_params["id"])
    # Geometry-only builds deliberately omit tanks. Do not retain dangling tank
    # references or imply a complete thermal inventory without those sources.
    heat_sources = _heat_source_records(ship_params)
    placement_raw = ship_params.get("imdg_placement_geometry")
    placement = None if placement_raw is None else _record(ImdgPlacementGeometry, placement_raw)
    if not tanks:
        heat_sources = [source.model_copy(update={"tank_id": None}) for source in heat_sources]
        if placement is not None:
            placement = placement.model_copy(update={
                "heat_sources": [], "heat_map_complete": None, "heat_map_provenance": None,
            })
    tank_pairs = ([_record(TankPair, record) for record in ship_params.get("tank_pairs", [])]
                  if tanks else [])
    vessel = Vessel(
        id=ship_params["id"],
        name=ship_params["name"],
        nominal_teu=ship_params["nominal_teu"],
        cells=[_record(Cell, record) for record in ship_params["cells"]],
        stacks=stacks,
        dg_doc_areas=_dg_doc_areas(ship_params, parameter_source),
        hatch_covers=[_record(HatchCover, record) for record in ship_params["hatch_covers"]],
        heat_sources=heat_sources,
        exclusion_regions=_exclusion_records(ship_params),
        imdg_placement_geometry=placement,
        imdg_ship_type=ship_params.get("imdg_ship_type"),
        bridge=_record(Bridge, ship_params["bridge"]) if "bridge" in ship_params else None,
        hull=hull,
        tanks=tanks,
        tank_pairs=tank_pairs,
        ballast_system=ballast_system,
        voyage_policy=voyage_policy,
        fixed_loads=fixed_loads,
        fixed_load_cells=[_record(Cell, record) for record in ship_params.get("fixed_load_cells", [])],
        fixed_load_stacks=[_record(Stack, record) for record in ship_params.get("fixed_load_stacks", [])],
        fixed_load_hatch_covers=[_record(HatchCover, record)
                                for record in ship_params.get("fixed_load_hatch_covers", [])],
        sources=[parameter_source,
                 *fixed_sources,
                 *sorted(_source_notes({key: value for key, value in ship_params.items()
                                        if freeze_enabled or key != "ballast_system"}))],
    )
    if with_hull and tanks:
        from stowage.vessel.fleet_lashing_design import populate_lashing_design

        populate_lashing_design(vessel, ship_params, parameter_source=parameter_source)
    return vessel
