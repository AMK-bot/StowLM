"""Cargo visibility using SOLAS V/22.1.1–1.2 (MSC.99(73), chapter V).

A sightline from the bridge eye tangent to a stack roof meets the sea at
``s = h_eye * x_obstacle / (h_eye - h_obstacle)``. The furthest intersection
within ten degrees either side of ahead must be within min(2*LOA, 500 m)
forward of the bow. We transform the real box corners into the equilibrium
waterplane, clip their projected roofs to the angular wedge, and evaluate all
vertices: a linear-fractional function attains its maximum at a vertex. No
angular sampling can miss a narrow obstruction. Roofs at eye level give an
unbounded blind distance. V/22.1.2 limits sectors to 10 degrees individually
(5 in the forward view), 20 total, and at least 5 clear degrees between.

APPROXIMATION: Operational interpretation of V/22.1.2: a bearing is blind when
cargo hides any sea surface beyond horizontal range R from the conning eye,
where R is eye-to-bow distance plus min(2*LOA, 500 m). The primary text says sea surface, not horizon, but
does not prescribe this distance threshold for .1.2. This interpretation thus
counts below-eye cargo with long blind distances, while excluding the ordinary
near-ship shadow of low deck cargo. It requires maritime/flag review before
claiming certified compliance. A point (x,y,h) hides sea beyond R iff
h_eye*hypot(x,y) + R*h > R*h_eye (including h >= h_eye). Exact intersections
of box-face edges with this cone yield the angular union, without sampling.

Loading equilibrium is computed directly with task 2.1's HullGeometry, including
finite-angle tank centroids, rather than calling the stability checker. Missing
hull offsets are a hard missing-data result, never a guessed even-keel condition.
Metrics: blind_distance_m, limit_m, draught_aft_m, draught_fwd_m, trim_m,
list_rad, max_blind_sector_rad, total_blind_sector_rad, min_clear_sector_rad.

APPROXIMATION: This screens planned cargo, individual D42 background boxes,
covers and explicit non-cargo fixed-load silhouettes in the supplied loading
condition, not certification under every voyage draught/trim/cargo condition.
Forecastle, breakwater, masts, cranes and other fixed obstructions must be
represented by explicit non-cargo fixed-load silhouettes. Cargo requires its
per-box support geometry; every non-cargo load requires a silhouette. Missing
geometry is hard missing data, including for stores and hold cargo. An
explicit submerged or aft envelope can demonstrate
no obstruction in this condition; its point CG alone cannot. Silhouettes add
no mass, and the shared equilibrium helper still accepts point-mass inputs.
The caller must evaluate subsequent
conditions (including bunker/fresh-water consumption) separately.
APPROXIMATION: The centreline conning eye is declared 2 m aft of Bridge.x_m,
the bridge-front coordinate, pending an explicit vessel conning-position field.
Zero setback understated the forward blind distance for elevated cargo; this
synthetic setback does not establish an approved bridge layout. Bridge.eye_z_m is accepted as the absolute
eye elevation; no extra 1.8 m is added to its supplied value.
ISO boxes are opaque rectangular solids (including declared OOG), without gaps.
Hatch-cover footprints are the union of their deck-cell rectangles and their
roof height is the declared stack base_z_m (which already includes the cover).
Bridge windows, pillars, wings and viewing arcs are absent from the model;
V/22.1.3–1.9 design certification is outside this cargo-dependent check. Sources:
https://wwwcdn.imo.org/localresources/en/KnowledgeCentre/IndexofIMOResolutions/MSCResolutions/MSC.99(73).pdf
and https://www.amsa.gov.au/about/regulations-and-standards/marine-notice-14-2022-ensuring-navigation-bridge-visibility
"""
from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from itertools import product
import math
from pathlib import PurePosixPath
from types import SimpleNamespace

import numpy as np

from stowage.conventions import CONTAINER_LENGTH_M, format_slot
from stowage.checker.slots import cells_of_slot, parse_slot
from stowage.frozen import frozen
from stowage.model import Instance, PartReport, ShipState, Violation
from stowage.model.vessel import Cell, FixedLoad, HatchCover, Stack, Vessel, _validate_geometry_refs
from stowage.vessel.fleet import load_ship_params
from stowage.vessel.hydrostatics import HullGeometry
from stowage.vessel.tanks import TankGeometry
from stowage.checker.visibility_range import range_blind_intervals


# SOLAS V/22.1.1, reproduced in IRS Main Rules July 2026 Pt.5 Ch.31
# §3.1.4.3, printed p.6 / PDF p.2439: "from the conning position".
# APPROXIMATION: synthetic centreline conning position 2 m aft of the bridge
# front, not vessel-approved layout data. The former zero setback was lenient:
# for an elevated roof, moving the eye aft increases bow-relative blind distance.
CONNING_EYE_SETBACK_M = 2.0


@dataclass(frozen=True)
class LoadingCondition:
    mass_kg: float
    lcg_m: float
    tcg_m: float
    kg_m: float
    gm_m: float
    t_aft_m: float
    t_fwd_m: float
    heel_rad: float
    geometry: HullGeometry


@lru_cache(maxsize=8)
def _geometry(name: str):
    params = load_ship_params(name)
    return HullGeometry.from_params(params), params


@dataclass(frozen=True)
class CargoGeometry:
    """One physical box, whether a decision box or non-decision background."""

    placement: object
    container: object
    physical: tuple
    background: bool
    x_m: float  # envelope centre, not necessarily its declared cargo CG
    y_m: float
    bottom: float
    top: float
    cg_m: tuple[float, float, float] | None


def _cargo_catalogs(vessel):
    """Union supports, rechecking the mutable non-decision catalogs locally.

    Do not revalidate the whole Instance: unrelated CSC/policy evidence belongs
    to its owning checker. Fixed support catalogs have the model's reciprocal
    membership contract, and cannot shadow a decision cell or stack.
    """
    catalogs = []
    for field, cls in (('fixed_load_cells', Cell), ('fixed_load_stacks', Stack),
                       ('fixed_load_hatch_covers', HatchCover)):
        items = [cls.model_validate(raw.model_dump() if isinstance(raw, cls) else raw)
                 for raw in getattr(vessel, field)]
        result = {item.id: item for item in items}
        if len(result) != len(items):
            raise ValueError(f'duplicate {field} ids')
        catalogs.append(result)
    fixed_cells, fixed_stacks, fixed_covers = catalogs
    _validate_geometry_refs(fixed_cells, fixed_stacks, fixed_covers, field='fixed_load_geometry')
    cells = {c.id: c for c in vessel.cells}
    stacks = {s.id: s for s in vessel.stacks}
    if cells.keys() & fixed_cells.keys() or stacks.keys() & fixed_stacks.keys():
        raise ValueError('fixed_load support catalogs must be disjoint from decision geometry')
    return cells | fixed_cells, stacks | fixed_stacks


def _all_cargo_geometry(instance, ship):
    """Validated joint support order and actual envelopes for every cargo box.

    D42 box records describe the aggregate FixedLoad mass; callers must never
    add their mass again. Evaluated placements can occupy unoccupied upper
    fixed cells, so background and planned boxes share one support-height map.
    """
    vessel = instance.vessel
    cells, stacks = _cargo_catalogs(vessel)
    fixed_cell_ids = {cell.id if isinstance(cell, Cell) else cell['id']
                      for cell in vessel.fixed_load_cells}
    boxes = {box.id: box for box in instance.containers}
    records = [(p, boxes[p.container_id], False, None) for p in ship.placements]
    for index, load in enumerate(_fixed_loads(vessel)):
        if load.mass_kg > 0 and load.kind in ('unknown', 'other'):
            raise ValueError(f'fixed_loads[{index}] positive mass has unknown cargo kind')
        if load.kind != 'cargo':
            continue
        if load.cargo_boxes is None:
            raise ValueError(f'fixed_loads[{index}] cargo_boxes required; silhouette cannot replace per-box evidence')
        for j, box in enumerate(load.cargo_boxes):
            if any(cid not in fixed_cell_ids for cid in box.cell_ids):
                raise ValueError(f'fixed_loads[{index}].cargo_boxes references unknown fixed_load_cells')
            first = parse_slot(box.cell_ids[0])
            slot = format_slot(first.bay if box.length_ft == 20 else first.bay+1, first.row, first.tier)
            identifier = f'fixed-load-{index}-box-{j}'
            adapter = SimpleNamespace(id=identifier,length_ft=box.length_ft,height_m=box.height_m,
                                      gross_mass_kg=box.gross_mass_kg,oog=None,background=True)
            records.append((SimpleNamespace(slot=slot,container_id=identifier), adapter, True,
                            (box.x_m,box.y_m,box.z_m)))
    occupied, seen_ids, result = {}, set(), []
    tops = {sid: stack.base_z_m for sid,stack in stacks.items()}
    for p, box, background, cg in sorted(records,key=lambda record:(parse_slot(record[0].slot).tier,record[0].slot)):
        if box.id in seen_ids:
            raise ValueError(f'duplicate cargo placement {box.id}')
        seen_ids.add(box.id)
        physical = tuple(cells[cid] for cid in cells_of_slot(p.slot))
        if len(physical) != (1 if box.length_ft == 20 else 2):
            raise ValueError('cargo length disagrees with physical supports')
        for cell in physical:
            if cell.id in occupied:
                raise ValueError(f'cargo boxes overlap physical cell {cell.id}')
            if not getattr(cell, f'accepts_{box.length_ft}'):
                raise ValueError(f'cargo cell {cell.id} requires accepts_{box.length_ft}')
            sequence = stacks[cell.stack_id].cell_ids if cell.stack_id in stacks else sorted(
                c.id for c in cells.values() if c.stack_id == cell.stack_id)
            position = sequence.index(cell.id)
            if position and sequence[position-1] not in occupied:
                raise ValueError(f'unsupported cargo cell {cell.id}; lower cell is empty')
        floors = [tops.get(cell.stack_id,cell.vcg_base_m) for cell in physical]
        if max(floors)-min(floors) > 1e-6:
            raise ValueError('paired cargo supports must have equal actual height')
        bottom = floors[0]
        height = box.height_m + (box.oog.over_height_m if box.oog else 0.)
        top = bottom+height
        x = math.fsum(cell.lcg_m/len(physical) for cell in physical)
        y = math.fsum(cell.tcg_m/len(physical) for cell in physical)
        if not all(math.isfinite(value) for value in (x,y,bottom,top)):
            raise ValueError('cargo support envelope must be finite')
        if cg is not None:
            half_length = CONTAINER_LENGTH_M[box.length_ft]/2
            if any(not lo-1e-6 <= value <= hi+1e-6 for value,lo,hi in
                   zip(cg,(x-half_length,y-1.219,bottom),(x+half_length,y+1.219,top))):
                raise ValueError('background cargo CG is outside its supported box envelope')
        result.append(CargoGeometry(p,box,physical,background,x,y,bottom,top,cg))
        for cell in physical:
            occupied[cell.id] = box.id
            tops[cell.stack_id] = top
    return tuple(result)


def _cargo_geometry(instance, ship):
    """Planned boxes only: background mass already enters via FixedLoad.

    Without per-box cargo, preserve the equilibrium point-mass compatibility
    contract even for legacy unknown-kind loads. Checker entry points enforce
    classification before using the shared joint geometry.
    """
    if any(load.cargo_boxes is not None for load in _fixed_loads(instance.vessel)):
        for record in _all_cargo_geometry(instance,ship):
            if not record.background:
                yield (record.placement,record.container,record.x_m,record.y_m,record.bottom,record.top)
        return
    vessel = instance.vessel
    cells = {c.id:c for c in vessel.cells}
    boxes = {c.id:c for c in instance.containers}
    tops = {s.id:s.base_z_m for s in vessel.stacks}
    for p in sorted(ship.placements,key=lambda p:(p.slot[-2:],p.slot)):
        box = boxes[p.container_id]
        physical = [cells[cid] for cid in cells_of_slot(p.slot)]
        bottom = max(tops.get(cell.stack_id,cell.vcg_base_m) for cell in physical)
        top = bottom+box.height_m+(box.oog.over_height_m if box.oog else 0.)
        for cell in physical:
            tops[cell.stack_id] = top
        yield p,box,math.fsum(c.lcg_m/len(physical) for c in physical),math.fsum(c.tcg_m/len(physical) for c in physical),bottom,top


def _fixed_loads(vessel: Vessel) -> tuple[FixedLoad, ...]:
    """Normalise even unvalidated model_copy/direct-assignment legacy records.

    The model's legacy tuple contract supplies kind='unknown', silhouette=None;
    do not infer cargo geometry from its four point-mass values. Invalid
    records raise ValueError for the checker's hard missing-data report.
    """
    return tuple(FixedLoad.model_validate(raw.model_dump() if isinstance(raw, FixedLoad) else raw)
                 for raw in vessel.fixed_loads)


def _loading_condition(instance: Instance, ship: ShipState) -> LoadingCondition:
    """Shared numerical loading condition for visibility and IRS lashing."""
    vessel, hull = instance.vessel, instance.vessel.hull
    if hull is None or not hull.hull_offsets_ref:
        raise ValueError('HullGeometry offsets reference is required')
    ref = PurePosixPath(hull.hull_offsets_ref.split('#')[0])
    if str(ref.parent) != 'params/fleet' or ref.suffix != '.toml':
        raise ValueError('unsupported hull offsets reference; expected params/fleet/<name>.toml#hull_form')
    geometry, params = _geometry(ref.stem)
    if not np.allclose([geometry.lpp_m, geometry.beam_m, geometry.depth_m],
                       [hull.lpp_m, hull.beam_m, hull.depth_m], rtol=1e-9):
        raise ValueError('hull offsets dimensions disagree with Vessel.hull')
    if ship.covers_off:
        raise ValueError('open or relocated hatch covers invalidate the closed HullGeometry envelope')
    masses = [(hull.lightship_lcg_m, hull.lightship_tcg_m, hull.lightship_vcg_m, hull.lightship_mass_kg)]
    # A silhouette describes the same fixed load; it never adds another mass.
    masses.extend((load.x_m, load.y_m, load.z_m, load.mass_kg)
                  for load in _fixed_loads(vessel))
    for _, box, x, y, bottom, top in _cargo_geometry(instance, ship):
        # APPROXIMATION: cargo CG is at geometric half-height; VGM has no
        # internal cargo CG. Asymmetric OOG uses the expanded envelope centre
        # for mass as well as wind; IRS lashing separately uses its 0.45H.
        if box.oog:
            x += (box.oog.over_length_fore_m-box.oog.over_length_aft_m)/2
            y += (box.oog.over_width_port_m-box.oog.over_width_starboard_m)/2
        masses.append((x, y, (bottom+top)/2, box.gross_mass_kg))
    tank_models = {t.id: t for t in vessel.tanks}
    raw_tanks = {t['id']: t for t in params.get('tanks', [])}
    liquids = []
    for tid, mass in ship.tank_masses_kg.items():
        if not mass:
            continue
        if tid not in tank_models or tid not in raw_tanks:
            raise ValueError(f'missing tank geometry: {tid}')
        tank = tank_models[tid]
        solid = TankGeometry.from_params(raw_tanks[tid])
        if not math.isclose(solid.capacity_m3, tank.capacity_m3, rel_tol=1e-9) or not np.allclose(
                [solid.x_min_m, solid.x_max_m], [tank.x_aft_m, tank.x_fore_m], rtol=1e-9):
            raise ValueError(f'tank geometry disagrees with Vessel tank: {tid}')
        volume = mass / tank.content_density_kg_m3
        liquid = solid.liquid(volume)
        liquids.append((solid, volume, mass, tank.content_density_kg_m3))
        masses.append((*liquid.centroid_m, mass))
    total = sum(m[3] for m in masses)
    base = masses[:-len(liquids)] if liquids else masses
    previous = None
    for _ in range(30):
        cg = np.sum([np.array(m[:3])*m[3] for m in masses], axis=0)/total
        eq = geometry.equilibrium(total, *cg, instance.port.water_density_kg_m3)
        pose = np.array([eq.t_aft_m, eq.t_fwd_m, eq.heel_rad])
        if previous is not None and np.max(np.abs(pose-previous)) < 1e-7:
            break
        previous = pose
        trim = math.atan2(eq.trim_m, geometry.lpp_m)
        masses = base + [(*s.liquid(v, eq.heel_rad, trim).centroid_m, m) for s,v,m,_ in liquids]
    else:
        raise ValueError('tank-centroid/equilibrium iteration did not converge')
    # APPROXIMATION: initial roll-period GM uses upright KB+BM-KG with the
    # equilibrium waterplane inertia and tangent FSC at the loaded list/trim;
    # it is not a finite-angle derivative of GZ for substantially listed ships.
    fsm = sum(s.liquid(v, eq.heel_rad, math.atan2(eq.trim_m, geometry.lpp_m)).fsm_m4*rho for s,v,_,rho in liquids)
    gm = eq.submerged.vcb_m + eq.submerged.bm_t_m - cg[2] - fsm/total
    return LoadingCondition(total, *map(float,cg), float(gm), eq.t_aft_m, eq.t_fwd_m, eq.heel_rad, geometry)


def _clip(poly, fn):
    if not poly:
        return []
    result = []
    for p,q in zip(poly, poly[1:]+poly[:1]):
        a,b = fn(p),fn(q)
        if a >= 0:
            result.append(p)
        if (a >= 0) != (b >= 0):
            result.append(p + a/(a-b)*(q-p))
    return result


def _merge(intervals):
    result = []
    for lo,hi in sorted(intervals):
        if result and lo <= result[-1][1]+1e-12:
            result[-1] = (result[-1][0], max(hi,result[-1][1]))
        else:
            result.append((lo,hi))
    return result


@frozen('checker.check_visibility', owner='3.3', implemented=True)
def check_visibility(instance: Instance, ship: ShipState, *, call_index: int) -> PartReport:
    """Bridge visibility of one loading condition, at numerical equilibrium."""
    vessel = instance.vessel
    try:
        if vessel.bridge is None or vessel.hull is None:
            raise ValueError('bridge and hull data are required')
        fixed_loads = _fixed_loads(vessel)
        # SOLAS V/22.1.1–.2: see the reproduced requirements in local
        # data/raw/irs/main_rules_july_2026.txt, Pt.5 Ch.31 §§3.1.4.3–.4,
        # printed p.6 / PDF p.2439: "other obstructions outside of the
        # wheelhouse forward of the beam". Unknown extent cannot prove the
        # required clear view. Neither kind nor CG proves non-obstruction;
        # explicit envelopes below water or aft still use the same geometry.
        for index, load in enumerate(fixed_loads):
            if load.kind != 'cargo' and load.silhouette is None:
                raise ValueError(f'fixed_loads[{index}] ({load.kind}) silhouette is required; '
                                 'unknown obstruction geometry cannot certify visibility')
        cargo_geometry = _all_cargo_geometry(instance,ship)
        c = _loading_condition(instance, ship)
        bridge, hull = vessel.bridge, vessel.hull
        n = c.geometry.waterplane_normal(c.t_aft_m, c.t_fwd_m, c.heel_rad)
        along = np.array([1.,0.,0.])-n*n[0]
        along /= np.linalg.norm(along)
        across = np.cross(n,along)
        eye = np.array([bridge.x_m-CONNING_EYE_SETBACK_M,0.,bridge.eye_z_m])
        height = float(n@eye-n[2]*c.t_aft_m)
        if height <= 0 or bridge.bow_x_m <= eye[0]:
            raise ValueError('bridge eye must be above sea and aft of bow')
        bow_z = c.t_aft_m+(c.t_fwd_m-c.t_aft_m)*bridge.bow_x_m/hull.lpp_m
        bow = float(along@(np.array([bridge.bow_x_m,0.,bow_z])-eye))
        obstacles = []
        lengths = {20:6.058,40:12.192,45:13.716}
        for record in cargo_geometry:
            p,b,x,y,bottom,top = (record.placement,record.container,record.x_m,
                                record.y_m,record.bottom,record.top)
            if not record.background and int(p.slot[-2:]) < 80:
                continue
            extra = b.oog
            fore = extra.over_length_fore_m if extra else 0.
            aft = extra.over_length_aft_m if extra else 0.
            port = extra.over_width_port_m if extra else 0.
            starboard = extra.over_width_starboard_m if extra else 0.
            obstacles.append((x+(fore-aft)/2,y+(port-starboard)/2,bottom,top,
                              lengths[b.length_ft]+fore+aft,
                              2.438+port+starboard,p.slot,None if record.background else b.id))
        # SOLAS V/22.1.1–.2, IRS Pt.5 Ch.31 §§3.1.4.3–.4, printed p.6 /
        # PDF p.2439: "other obstructions outside of the wheelhouse forward
        # of the beam". D42 cargo uses its individual supported box envelopes;
        # the aggregate legacy silhouette neither replaces nor enlarges them.
        # Non-cargo is exempt from securing, not visibility: require its
        # declared silhouette and evaluate it even when aft or submerged.
        for load in fixed_loads:
            if load.kind == 'cargo':
                continue
            envelope = load.silhouette
            obstacles.append(((envelope.x_min_m+envelope.x_max_m)/2,
                              (envelope.y_min_m+envelope.y_max_m)/2,
                              envelope.z_min_m,envelope.z_max_m,
                              envelope.x_max_m-envelope.x_min_m,
                              envelope.y_max_m-envelope.y_min_m,None,None))
        # Covers with no cargo still obstruct near sea-surface sightlines.
        cells, stacks = _cargo_catalogs(vessel)
        for stack in stacks.values():
            if stack.on_deck and stack.hatch_cover_id and stack.cell_ids:
                cell = cells[stack.cell_ids[0]]
                obstacles.append((cell.lcg_m,cell.tcg_m,stack.base_z_m,stack.base_z_m,6.058,2.438,None,None))
        blind = 0.
        sectors = []
        involved_slots, involved_boxes = [],[]
        wedge = math.tan(math.radians(10))
        # SOLAS V/2.4: "Length of a ship means its length overall."
        # MSC.142(77), Annex printed p.2 / PDF p.3 (official IMO resolution):
        # https://wwwcdn.imo.org/localresources/en/KnowledgeCentre/IndexofIMOResolutions/MSCResolutions/MSC.142(77).pdf
        limit = min(2*hull.loa_m,500.)
        range_boundary = bow+limit
        for x,y,bottom,top,length,width,slot,boxid in obstacles:
            def transform(xx,yy,zz):
                p = np.array([xx,yy,zz])-eye
                return np.array([along@p, across@p, n@p+height])
            roof = [transform(xx,yy,top) for xx,yy in [(x-length/2,y-width/2),(x+length/2,y-width/2),(x+length/2,y+width/2),(x-length/2,y+width/2)]]
            forward = _clip(roof,lambda p:p[0]-1e-9)
            cone = _clip(_clip(forward,lambda p:wedge*p[0]-p[1]),lambda p:wedge*p[0]+p[1])
            for p in cone:
                if p[2] <= 0:
                    continue
                dist = math.inf if p[2] >= height-1e-10 else height*p[0]/(height-p[2])-bow
                blind = max(blind,dist)
            # SOLAS V/22.1.2, IRS Pt.5 Ch.31 §3.1.4.4, printed p.6 /
            # PDF p.2439: "forward of the beam obstructing the view of the sea
            # surface". Use horizontal range from the conning eye; using only
            # forward projection misses near-beam obstructions. The finite
            # threshold itself remains D43's labelled operational interpretation.
            vertices = [transform(xx,yy,zz) for xx,yy,zz in product(
                (x-length/2,x+length/2),(y-width/2,y+width/2),(bottom,top))]
            obstacle_sectors = range_blind_intervals(vertices,height,range_boundary)
            sectors.extend(obstacle_sectors)
            if (cone or obstacle_sectors) and slot:
                involved_slots.append(slot)
                if boxid is not None:
                    involved_boxes.append(boxid)
        merged = _merge(sectors)
        maximum = max((hi-lo for lo,hi in merged),default=0.)
        total = sum(hi-lo for lo,hi in merged)
        clear = min((b[0]-a[1] for a,b in zip(merged,merged[1:])),default=math.pi)
        metrics = dict(blind_distance_m=blind,limit_m=limit,draught_aft_m=c.t_aft_m,draught_fwd_m=c.t_fwd_m,trim_m=c.t_aft_m-c.t_fwd_m,list_rad=c.heel_rad,max_blind_sector_rad=maximum,total_blind_sector_rad=total,min_clear_sector_rad=clear,
                       conning_eye_x_m=float(eye[0]),conning_eye_setback_m=CONNING_EYE_SETBACK_M)
        violations = []
        def fail(message,measured,allowed,unit):
            violations.append(Violation(rule_id='solas.v22.visibility',severity='hard',call_index=call_index,containers=involved_boxes,slots=involved_slots,message=message,measured=measured,limit=allowed,unit=unit))
        if blind > limit+1e-8:
            fail('SOLAS V/22.1.1: cargo obscures the sea surface too far forward of the bow.',blind,limit,'m')
        for lo,hi in merged:
            sector_limit = math.radians(5 if lo < math.radians(10) and hi > -math.radians(10) else 10)
            if hi-lo > sector_limit+1e-10:
                fail('SOLAS V/22.1.2: individual cargo blind sector exceeds the permitted angle.',hi-lo,sector_limit,'rad')
        if total > math.radians(20)+1e-10:
            fail('SOLAS V/22.1.2: total cargo blind sectors exceed twenty degrees.',total,math.radians(20),'rad')
        if clear < math.radians(5)-1e-10:
            fail('SOLAS V/22.1.2: clear angle between cargo blind sectors is less than five degrees.',clear,math.radians(5),'rad')
        return PartReport(name='visibility',metrics=metrics,margins={'blind_distance_m':limit-blind,'total_blind_sector_rad':math.radians(20)-total,'min_clear_sector_rad':clear-math.radians(5)},violations=violations)
    except (ValueError,KeyError,TypeError,FileNotFoundError,ArithmeticError) as exc:
        message = str(exc)
        return PartReport(name='visibility',ran=False,error=message,violations=[Violation(rule_id='checker.missing_vessel_data',severity='hard',call_index=call_index,message=message)])
