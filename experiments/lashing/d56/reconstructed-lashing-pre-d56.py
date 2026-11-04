"""IRS container securing loads, with an explicitly synthetic securing arrangement.

The July 2026 IRS Main Rules Pt.3 Ch.5 §§2.1.2–2.1.3 and 2.2.3
supply sinusoidal ship motions and combined accelerations (gravity components
of roll/pitch are already included). The July 2025 IRS-G-DES-19 Guidelines
§§3.3–3.6 supply wind, end-frame stiffness, equivalent joint forces and limits.
A spring model shares sideways force between the flexible container ends and
lashing rods: a stiffer rod attracts more load and relieves racking below it.
This is an engineering screening calculation, not an IRS-approved cargo manual.

APPROXIMATION: synthetic standard arrangement: no bridge, rigid hatch supports,
opposed crossed 28 mm knob-end rods at each end frame to the top of tier 2
(tier 1 for a single box), horizontal reach 2.438 m and zero pretension. Rod
plus turnbuckle effective E is 175 GPa (two tiers), 140 GPa (one tier), from
LR Pt.3 Ch.14 Table 14.9.4, not solid-steel E. Rod assembly SWL=250 kN
(TEC BR-31C; turnbuckle working rating follows the public ATSB gear class); twistlock SWL tension/shear/compression=250/210/1000 kN
(Silverback 10973-S). Apply IRS §3.6.6.3's 85% to working, never breaking,
loads. This is a synthetic gear selection, not an approved combination of products.
No wind shelter is credited; wind is max(table, 1 kPa full expanded area).
D42 background boxes share the same graph and IRS calculations as planned
boxes, using their declared CG. Missing box records or ambiguous positive fixed
loads fail closed; only explicitly non-cargo loads need no securing. Fixed-load
aggregates enter equilibrium once, independently of box geometry. Every box receives
full wind exposure, with no adjacent-stack shelter credit.
Both end stiffnesses and motion directions are checked. Corner shortening,
hatch deformation, clearances, green seas and whipping remain unrepresented.

APPROXIMATION: 45-foot and OOG frames use the standard IRS strength limits and
end stiffnesses as synthetic ratings, not manufacturer certification. A 45-foot
box has its load-bearing corners at the 40-foot positions. OOG cargo has CG
at the expanded envelope's horizontal centre and 0.45 expanded height; its
wind acts at envelope centre. Static eccentricity is included in post loads.
ISO support spans stay fixed while the wind envelope expands. Effective end
force fractions follow the eccentric CG along the ISO support span.

APPROXIMATION: 40/45-over-two-20 deck support uses logical ISO corner alignment
(adapters absorb nominal cell-coordinate spacing), with no intermediate support
under the long box: its outer end reactions enter only the corresponding outer
20-foot end frames. Inner 20-foot frames carry their own loads. Paired supports
must be complete and at equal actual height. A per-box longitudinal reaction
recursion preserves force and moment, including upper horizontal loads applied
at each lower box's roof. Transverse end-frame chains are independent elastic
springs; transverse coupling through side walls is omitted. 20-over-long boxes
and mixed 20/long hold stacks fail closed (IRS §§2.4.4, 3.6.4.5).

APPROXIMATION: resolve inconsistent §3.6.3.3 mass notation as total mass/4 per
post, consistent with §3.6.2.2. Longitudinal wind is included conservatively.
Twistlock shear combines horizontal axes without transverse rod relief.
Hold cell guides use 0.5 m bracket spacing and material k=1. D54 derives each
vessel's section from rated geometry with a 10% margin; area 0.003 m² and
modulus 0.000250 m³ remain lower bounds. Interface loads sum the upper/lower
fittings sharing a bracket.
Guide stress combines horizontal axes conservatively; individual fitting SWLs
are checked against each separate contact, not the shared bracket resultant.

Sources for synthetic gear properties:
https://www.imorules.com/LRSHIP_PT3_CH14_9.html (Table 14.9.4)
https://lashingequipment.com/br-31c/ (SWL 250 kN, breaking load 500 kN)
https://www.atsb.gov.au/investigations/344-mo-2018-008 (rod/turnbuckle ratings)
https://www.silverback.com.au/10973_dash_S/Stackable-Container-Twist-Lock/pd.php
Metrics are worst load/limit ratios; margins are 1 minus those ratios. Condition
GM includes free surface, via the numerical loading-condition helper in visibility.
"""
from __future__ import annotations

from dataclasses import dataclass
import math
from pathlib import PurePosixPath

import numpy as np
from pydantic import Field

from stowage.frozen import frozen
from stowage.model import Instance, PartReport, ShipState, Violation

G = 9.81
WIDTH = 2.438
RULE = "iacs.member_irs.container_securing"
LENGTH = {20: 6.058, 40: 12.192, 45: 13.716}
ROD_SWL = 250000.
LOCK_TENSION_SWL, LOCK_SHEAR_SWL, LOCK_COMPRESSION_SWL = 250000., 210000., 1000000.

# APPROXIMATION: synthetic guide design from experiments/physics-review/DESIGN.md
# §3.3: the earlier section was bending-overloaded up to 2.34 at GM 2–3.6 m.
# Increase structural section rather than changing GM or IRS allowable stresses.
# Frozen Vessel/Stack expose no guide-section parameter. D54 retains these
# historical sections as lower bounds, then derives vessel scantlings below.
# Retaining the shear term needs W >= 2.41311e-4 m³ for the design's old
# worst von Mises ratio 2.34 (contact 81.3594 kN). Round up to 2.50e-4;
# simply scaling bending by 2.35 would leave von Mises at ratio 1.02494.
CELL_GUIDE_SECTION_MODULUS_M3 = 2.50e-4
CELL_GUIDE_BRACKET_SPACING_M = 0.5  # already 0.5 m; no second spacing reduction
CELL_GUIDE_AREA_M2 = 0.003


@dataclass(frozen=True)
class CellGuideDesign:
    """Auditable D54 synthetic scantlings, independent of the actual cargo."""

    section_modulus_m3: float
    area_m2: float
    gm_m: float
    governing_stack_id: str
    rated_ratio: float
    contact_n: float
    maximum_tiers: int
    note: str = ''


class LashingReport(PartReport):
    """PartReport-compatible result carrying the auditable synthetic design."""

    notes: list[str] = Field(default_factory=list)


def _cell_guide_ratio(contact: float, section_modulus: float, area: float) -> float:
    # IRS-G-DES-19 July 2025 §3.6.6.2, PDF p.15 (printed p.14):
    # "200 /k for normal stresses, 100/k for shear stresses and 175/k for
    # von mises stresses". D54 sizes the section; these k=1 limits do not move.
    shear_stress = contact/area
    bend = contact*CELL_GUIDE_BRACKET_SPACING_M/section_modulus
    return max(bend/200e6, shear_stress/100e6,
               math.hypot(bend, math.sqrt(3)*shear_stress)/175e6)


def _rod_stiffness(length: float, tiers: int) -> float:
    """LR Table 14.9.4 effective knob rod + turnbuckle assembly, N/m."""
    effective_e = 175e9 if tiers >= 2 else 140e9
    return effective_e*(math.pi*.028**2/4)/length


@dataclass(frozen=True)
class Motions:
    length: float
    rotation_z: float
    surge: float
    sway: float
    heave: float
    roll: float
    pitch: float
    phi: float
    theta: float
    period_roll: float

    def at(self, x: float, y: float, z: float) -> tuple[float, float, float]:
        """IRS Pt.3 Ch.5 §§2.2.3.1–3; acceleration in m/s²."""
        al = .7 * math.sqrt(self.surge**2 + max(self.length, 110)/325 *
                            (G*math.sin(self.theta) + self.pitch*(z-self.rotation_z))**2)
        at = math.hypot(self.sway, G*math.sin(self.phi) + self.roll*(z-self.rotation_z))
        av = math.sqrt(self.heave**2 + (self.roll*y)**2 + (self.pitch*(x-.45*self.length))**2)
        return al, at, av


def _motions(hull, draught: float, gm: float, cb: float) -> Motions:
    """July 2026 baseline (the January 2027 pitch amendment is not applied)."""
    if gm <= 0 or not 0 < cb <= 1 or draught <= 0:
        raise ValueError("positive loading-condition GM, draught and physical block coefficient required")
    l, b = hull.lpp_m, hull.beam_m
    # Table 2.1.2 permits kr=.35B when a calculated gyradius is unavailable.
    tr = min(30., 2.3*math.pi*(.35*b)/math.sqrt(G*gm))
    phi = 9000*(1.25-.025*tr)*(1 if hull.bilge_keel else 1.2)/((b+75)*math.pi)
    if b < 32.2:
        phi = max(25., phi)  # Guidelines §3.5.2.1
    theta = min(20., 1350*l**(-.94)*(1+(2.57/math.sqrt(G*l))**1.2))
    tp = math.sqrt(2*math.pi*.6*(1+max(.5, draught/hull.scantling_draught_m))*l/G)
    # §2 symbols: unrestricted-service extreme sea coefficient fp=fps=1.
    a0 = min(.52, (1.58-.47*cb)*(2.4/math.sqrt(l)+34/l-600/l**2))
    pr, pt = math.radians(phi), math.radians(theta)
    pitch = pt*(2*math.pi/tp)**2 if l >= 90 else .38*theta/tp**2
    return Motions(l, min(hull.depth_m/4+draught/2, hull.depth_m/2),
                   .2*a0*G, .3*a0*G, .7*a0*G/math.sqrt(cb),
                   pr*(2*math.pi/tr)**2, pitch, pr, pt, tr)


def derive_cell_guide_design(vessel) -> CellGuideDesign:
    """D54 deterministic scantlings from rated geometry, never occupied cargo.

    APPROXIMATION: synthetic design, not class-approved scantlings. There is
    no maximum-GM field in HullModel. Instead bound every permitted waterplane
    geometrically, using the *independent* signed loading-manual trim bounds.
    This conservative upper bound need not be an attainable loading condition.
    No hydrostatic-table maximum or particular background stow defines it.
    """
    from stowage.checker.visibility import _cargo_catalogs, _fixed_loads, _geometry

    cells, stacks = _cargo_catalogs(vessel)
    holds = sorted((s for s in stacks.values() if not s.on_deck), key=lambda s: s.id)
    if not holds:
        return CellGuideDesign(CELL_GUIDE_SECTION_MODULUS_M3, CELL_GUIDE_AREA_M2,
                               0., '', 0., 0., 0, 'D54: no rated hold stacks.')
    hull = vessel.hull
    if hull is None or not hull.hull_offsets_ref:
        raise ValueError('D54 guide design requires referenced hull geometry')
    if hull.min_trim_m is None or hull.max_trim_m is None:
        raise ValueError('D54 guide design requires both signed loading-manual trim bounds')
    if not 0 <= hull.max_list_rad < math.pi/2:
        raise ValueError('D54 guide design requires finite list limit below 90 degrees')
    ref = PurePosixPath(hull.hull_offsets_ref.split('#')[0])
    if str(ref.parent) != 'params/fleet' or ref.suffix != '.toml':
        raise ValueError('D54 unsupported hull reference; expected params/fleet/<name>.toml#hull_form')
    geometry, params = _geometry(ref.stem)
    if not np.allclose([geometry.lpp_m, geometry.beam_m, geometry.depth_m],
                       [hull.lpp_m, hull.beam_m, hull.depth_m], rtol=0., atol=1e-7):
        raise ValueError('D54 hull reference disagrees with principal dimensions')
    points = np.concatenate([np.column_stack((np.full(len(p), x), p))
                             for x, section in zip(geometry.x_m, geometry.sections)
                             for p in section])
    xmin, ymin, zmin = np.min(points, axis=0)
    xmax, ymax, zmax = np.max(points, axis=0)
    phi = hull.max_list_rad
    trim = (hull.min_trim_m, hull.max_trim_m)
    slope = max(abs(t) for t in trim)/hull.lpp_m
    # For z=Tf+(1-x/L)*trim+y*tan(list), this horizontal plane lies
    # below every permitted waterplane. Its volume is therefore a positive,
    # density-independent lower bound, including fresh-water departures.
    hmin = hull.min_draught_fwd_m + min((1-x/hull.lpp_m)*t
                                      for x in (xmin, xmax) for t in trim)
    hmin -= max(abs(ymin), abs(ymax))*math.tan(phi)
    volume_min = geometry.submerged(hmin, hmin).volume_m3
    if not math.isfinite(volume_min) or volume_min <= 0:
        raise ValueError('D54 loading envelope has no positive certified immersed-volume lower bound')
    # Section chords at any allowed heel fit in each polygon's y interval;
    # their physical length is <= interval/cos(phi). The x-to-waterplane
    # Jacobian is <=sqrt(1+slope²). Popoviciu's variance inequality then
    # gives Iwp <= area * transverse_span² / 4 about its own centroid.
    area_max = sum(float(w)*sum(float(np.ptp(p[:, 0])) for p in section)
                   for w, section in zip(geometry.integration_weights_m, geometry.sections))
    area_max *= math.sqrt(1+slope*slope)/math.cos(phi)
    span_max = (xmax-xmin)*slope*math.sin(phi)+(ymax-ymin)+(zmax-zmin)*math.sin(phi)
    from stowage.vessel.tanks import TankGeometry
    raw_tanks = {t['id']: t for t in params.get('tanks', [])}
    tank_floors = []
    for tank in vessel.tanks:
        if tank.id not in raw_tanks:
            raise ValueError(f'D54 guide design lacks physical tank geometry: {tank.id}')
        solid = TankGeometry.from_params(raw_tanks[tank.id], ship_params=params)
        tank_floors.append(solid.z_min_m)
    # A tilted partly-filled tank's CG can be below every nonempty upright
    # capacity-table centroid; only the solid's physical floor is a bound.
    kg_min = min([0., hull.lightship_vcg_m]+[s.base_z_m for s in stacks.values()]+tank_floors)
    # All cargo CGs are inside their declared supported envelopes. Explicit
    # non-cargo constants are part of the vessel, and can be below baseline.
    kg_min = min([kg_min]+[float(q.z_m) for q in _fixed_loads(vessel)
                          if q.kind == 'non_cargo'])
    gm = float(zmax+area_max*span_max**2/(4*volume_min)-kg_min)
    if not math.isfinite(gm) or gm <= 0:
        raise ValueError('D54 guide design has no finite positive GM bound')
    volume_sc = geometry.submerged(hull.scantling_draught_m, hull.scantling_draught_m).volume_m3
    cb = volume_sc/(hull.lpp_m*hull.beam_m*hull.scantling_draught_m)
    # IRS main rules Table 2.1.2/§2.1.3 unchanged: pitch period reaches its
    # minimum for T<=Tsc/2, so Tsc/4 is its exact shallow-draught limit.
    motions = _motions(hull, hull.scantling_draught_m/4, gm, cb)
    maximum_contact, governing, tiers = 0., holds[0].id, 0
    for stack in holds:
        if not stack.cell_ids:
            continue
        support = [cells[cid] for cid in stack.cell_ids]
        # The rated maximum height includes maximum homogeneous tiers, with
        # any declared cargo CG inside the full box height. Using the whole
        # rated column mass at its most adverse height also bounds arbitrary
        # nonuniform masses at a shared two-fitting bracket (not just equal
        # mass tiers). A spanning 40/45 box contributes half its mass to each
        # physical column, but its end guide receives its whole-box force/4.
        ratings = []
        if any(c.accepts_20 for c in support):
            rating = stack.max_mass_20_kg if stack.max_mass_20_kg is not None else stack.max_mass_kg
            ratings.append(max(rating, len(stack.cell_ids)*2500.))
        if any(c.accepts_40 or c.accepts_45 for c in support):
            rating = stack.max_mass_40_kg if stack.max_mass_40_kg is not None else stack.max_mass_kg
            ratings.append(max(2*rating, len(stack.cell_ids)*3500.))
        if not ratings:
            continue
        # _load_graph retains its existing 2.5/3.5 t IRS tare floors; include
        # those unchanged effective masses for exceptionally low-rated stacks.
        mass = max(ratings)
        lever = max(abs(z-r) for z in (stack.base_z_m, stack.base_z_m+stack.max_height_m)
                    for r in (hull.depth_m/4, hull.depth_m/2))
        # Triangle inequalities deliberately remove cancellation; they bound
        # the unchanged IRS acceleration at every rated height, draught and
        # GM up to the certified upper bound. Roll amplitude stays below pi/2
        # under Table 2.1.2, so its gravity component and angular acceleration
        # increase with GM; pitch reaches its maximum at the shallow limit.
        at = math.hypot(motions.sway, G*math.sin(motions.phi)+motions.roll*lever)
        al = .7*math.hypot(motions.surge, math.sqrt(max(hull.lpp_m, 110)/325)*
                            (G*math.sin(motions.theta)+motions.pitch*lever))
        contact = mass*math.hypot(at, al)/4
        if contact > maximum_contact:
            maximum_contact, governing, tiers = contact, stack.id, len(stack.cell_ids)
    scale = max(1., 1.1*_cell_guide_ratio(maximum_contact,
                                        CELL_GUIDE_SECTION_MODULUS_M3, CELL_GUIDE_AREA_M2))
    section = math.ceil(CELL_GUIDE_SECTION_MODULUS_M3*scale/1e-6)*1e-6
    area = math.ceil(CELL_GUIDE_AREA_M2*scale/1e-5)*1e-5
    ratio = _cell_guide_ratio(maximum_contact, section, area)
    note = (f'APPROXIMATION: D54 synthetic rated-vessel guide design; governing stack {governing}, '
            f'GM upper bound {gm:.9g} m, maximum tiers {tiers}, contact bound {maximum_contact:.9g} N, '
            f'W={section:.9g} m3, A={area:.9g} m2, rated IRS ratio {ratio:.9g} '
            '(10% margin, rounded upward; no actual cargo used).')
    return CellGuideDesign(section, area, gm, governing, ratio, maximum_contact, tiers, note)


def _end_response(forces, winds, heights, stiffness, *, cg_heights=None):
    """Guidelines §§3.6.3.1.1–3: equivalent joint forces and elastic rods."""
    n = len(forces)
    fractions = np.full(n, .45) if cg_heights is None else np.array(cg_heights)/np.array(heights)
    equivalent = fractions*np.array(forces) + .5*np.array(winds)
    equivalent[:-1] += (1-fractions[1:])*np.array(forces[1:]) + .5*np.array(winds[1:])
    k = np.full(n, stiffness)
    matrix = np.diag(k.copy())
    for j in range(1, n):
        matrix[j-1, j-1] += k[j]
        matrix[j-1, j] -= k[j]
        matrix[j, j-1] -= k[j]
    anchor = min(2, n)-1
    z = sum(heights[:anchor+1])
    length = math.hypot(WIDTH, z)
    cosine, sine = WIDTH/length, z/length
    # Guidelines §3.6.1.3: EA/l; all internal stiffnesses in N/m.
    rod_k = _rod_stiffness(length, anchor+1)
    matrix[anchor, anchor] += rod_k*cosine**2
    u = np.linalg.solve(matrix, equivalent)
    rod = rod_k*cosine*u[anchor]
    rack = k*(u-np.r_[0., u[:-1]])
    return rack, rod, cosine, sine, anchor


@dataclass
class BoxLoad:
    placement: object
    container: object
    on_deck: bool
    floor: float
    height: float
    cg_height: float  # actual vertical CG above this box's support plane
    mass: float
    length: float  # corner support span, not the expanded envelope
    dx: float
    dy: float
    al: float
    at: float
    av: float
    wind_t: float
    wind_l: float
    parents: tuple[tuple[str, int] | None, tuple[str, int] | None]

    @property
    def slot(self):
        return self.placement.slot


def _load_graph(instance, ship, motions):
    """One combined background/planned graph; two end frames per physical box."""
    from stowage.checker.visibility import _all_cargo_geometry, _cargo_catalogs
    from stowage.conventions import parse_slot

    cells, stacks = _cargo_catalogs(instance.vessel)
    predecessor = {}
    for stack in stacks.values():
        for index, cid in enumerate(stack.cell_ids):
            predecessor[cid] = stack.cell_ids[index-1] if index else None
    # Legacy planned-only callers may retain cell geometry without all Stack
    # objects. Infer only that declared column's order, never background support.
    for cell in cells.values():
        if cell.id not in predecessor:
            column = sorted((q.id for q in cells.values() if q.stack_id == cell.stack_id),
                            key=lambda cid: parse_slot(cid).tier)
            index = column.index(cell.id)
            predecessor[cell.id] = column[index-1] if index else None
    loads, occupied = {}, {}
    for geometry in _all_cargo_geometry(instance, ship):
        p, c, physical = geometry.placement, geometry.container, geometry.physical
        address = parse_slot(p.slot)
        support = [occupied.get(predecessor[cell.id]) for cell in physical]
        below = [loads[q] for q in support if q is not None]
        if c.length_ft == 20 and any(q.container.length_ft != 20 for q in below):
            raise ValueError('unsupported physical order: 20-foot box over a 40/45-foot box')
        if len(physical) == 2 and any(support) and not all(support):
            raise ValueError('long box requires both supporting 20-foot columns')
        if not address.on_deck and below and any((c.length_ft == 20) != (q.container.length_ft == 20) for q in below):
            raise ValueError('mixed 20/long hold stacks require IRS special consideration (§3.6.4.5)')
        for parent, cell in zip(support, physical):
            if parent is None and predecessor[cell.id] is not None:
                raise ValueError('unsupported first occupied tier: lower cell is empty')
        floors = [loads[q].floor+loads[q].height if q else geometry.bottom for q in support]
        if max(floors)-min(floors) > 1e-6:
            raise ValueError('paired long-box supports must have equal actual height')
        parents = ((support[0], 0) if support[0] else None,
                   (support[-1], 1) if support[-1] else None)
        if below and any(parent is None for parent in parents):
            raise ValueError('box corner lacks a matching support frame')
        oog = c.oog
        height = geometry.top-geometry.bottom
        length = LENGTH[c.length_ft]
        expanded_l = length+(oog.over_length_fore_m+oog.over_length_aft_m if oog else 0.)
        expanded_w = WIDTH+(oog.over_width_port_m+oog.over_width_starboard_m if oog else 0.)
        centre_x = sum(q.lcg_m for q in physical)/len(physical)
        centre_y = sum(q.tcg_m for q in physical)/len(physical)
        if geometry.cg_m is not None:
            x, y, z = geometry.cg_m
            dx, dy = x-centre_x, y-centre_y
        else:
            dx = (oog.over_length_fore_m-oog.over_length_aft_m)/2 if oog else 0.
            dy = (oog.over_width_port_m-oog.over_width_starboard_m)/2 if oog else 0.
            x, y = centre_x+dx, centre_y+dy
            # IRS-G-DES-19 §3.6.2.4, PDF p.12 (printed p.11): loads act
            # "at the centre of gravity"; only "In absence of data for centre
            # of gravity" use the tabulated "Vertically: 0.45 Hc".
            # Background D42 records declare CG, so use that actual lever arm
            # throughout moments and equivalent-joint-force fractions instead.
            z = geometry.bottom+.45*height
        al, at, av = motions.at(x, y, z)
        # IRS-G-DES-19 July 2025 §3.6.1.1, PDF p.10: corner-casting loads
        # "should be checked in order to keep them below permissible limits";
        # §3.6.4.1, PDF p.14: hold forces "should be computed as indicated in
        # Section 3.5". The aggregate FixedLoad is equilibrium mass only: its
        # individual boxes use this identical securing path at the current GM.
        load = BoxLoad(p, c, address.on_deck, geometry.bottom, height, z-geometry.bottom,
                       max(c.gross_mass_kg, 2500 if c.length_ft == 20 else 3500),
                       12.192 if c.length_ft == 45 else length, dx, dy, al, at, av,
                       max(15700 if c.length_ft == 20 else 31600, expanded_l*height*1000),
                       max(6400, expanded_w*height*1000), parents)
        if p.slot in loads or any(q.id in occupied for q in physical):
            raise ValueError('background/planned boxes overlap physical securing cells')
        loads[p.slot] = load
        for q in physical:
            occupied[q.id] = p.slot
    return loads


def _chains(loads):
    children = {}
    roots = []
    for q in loads.values():
        for end, parent in enumerate(q.parents):
            node = (q.slot, end)
            if parent is None:
                roots.append(node)
            else:
                if parent in children:
                    raise ValueError('multiple boxes use one supporting end frame')
                children[parent] = node
    result = []
    for root in sorted(roots):
        chain, node = [], root
        while node is not None:
            chain.append((loads[node[0]], node[1]))
            node = children.get(node)
        result.append(chain)
    return result


def _longitudinal_reactions(loads, direction: float, *, dynamic_vertical: bool):
    """Four bottom-post reactions per box, with exact recursive force/moment balance.

    Upper end reactions land on the same ISO corner of the lower box; each
    lower box also reacts the upper horizontal force at its roof. Consequently
    a 40-foot box on paired 20s loads only their two outer end frames directly.
    Returned shear is per end (two corners), vertical is per corner (2×2).
    """
    incoming = {slot: np.zeros((2, 2)) for slot in loads}
    incoming_shear = {slot: np.zeros(2) for slot in loads}
    reactions, shear = {}, {}
    for q in reversed(list(loads.values())):
        weight = q.mass*(G+q.av if dynamic_vertical else G)
        vertical = incoming[q.slot].copy()+weight/4
        force = direction*(q.mass*q.al+q.wind_l) if q.on_deck else 0.
        moment = direction*(q.mass*q.al*q.cg_height+(q.wind_l*.5*q.height if q.on_deck else 0.))
        moment += float(sum(incoming_shear[q.slot]))*q.height
        if not q.on_deck:
            moment = 0.  # §3.6.4: horizontal load goes directly into cell guides.
        # Eccentric gravity and the longitudinal force moment preserve pitch moment.
        for end, sign in enumerate((-1., 1.)):
            vertical[end] += sign*(weight*q.dx+moment)/(2*q.length)
        vertical[:, 0] -= weight*q.dy/(2*WIDTH)
        vertical[:, 1] += weight*q.dy/(2*WIDTH)
        end_shear = (force+sum(incoming_shear[q.slot]))/2
        reactions[q.slot] = vertical
        shear[q.slot] = np.full(2, end_shear)
        for end, parent in enumerate(q.parents):
            if parent is not None:
                incoming[parent[0]][parent[1]] += vertical[end]
                incoming_shear[parent[0]][parent[1]] += end_shear
    return reactions, shear


def _check_lashing(instance: Instance, ship: ShipState, *, call_index: int) -> PartReport:
    from stowage.checker.visibility import _loading_condition

    def problem(message, rule="checker.missing_vessel_data", **kwargs):
        return Violation(rule_id=rule, severity="hard", call_index=call_index, message=message, **kwargs)

    # Missing D42 evidence is missing geometry, distinct from a valid graph that
    # exceeds securing limits. Revalidate nested mutable records at this entry.
    from stowage.checker.visibility import _fixed_loads, _cargo_catalogs
    for index, load in enumerate(_fixed_loads(instance.vessel)):
        if load.mass_kg > 0 and load.kind in ('unknown', 'other'):
            raise ValueError(f'background fixed_loads[{index}] has unknown cargo kind')
        if load.kind == 'cargo' and load.cargo_boxes is None:
            raise ValueError(f'background cargo fixed_loads[{index}] lacks per-box securing records')
    _cargo_catalogs(instance.vessel)

    hull = instance.vessel.hull
    if hull is None:
        raise ValueError('hull data absent')
    condition = _loading_condition(instance, ship)
    draught = (condition.t_aft_m+condition.t_fwd_m)/2
    # IRS Pt.3 Ch.1 §2.1.6 defines CB at scantling, not at loading draught.
    volume_sc = condition.geometry.submerged(hull.scantling_draught_m, hull.scantling_draught_m).volume_m3
    cb = volume_sc/(hull.lpp_m*hull.beam_m*hull.scantling_draught_m)
    motions = _motions(hull, draught, condition.gm_m, cb)
    try:
        loads = _load_graph(instance, ship, motions)
    except ValueError as exc:
        # An unsupported arrangement is a hard securing failure, not a missing-hull error.
        return PartReport(name='lashing', violations=[problem(str(exc), RULE)])
    guide_design = derive_cell_guide_design(instance.vessel)
    if guide_design.gm_m > 0 and condition.gm_m > guide_design.gm_m+1e-8:
        raise ValueError('loading-condition GM exceeds certified D54 design envelope bound')
    ratios = dict(max_racking_ratio=0., max_longitudinal_racking_ratio=0.,
                  max_corner_post_ratio=0., max_corner_tension_ratio=0.,
                  max_lashing_ratio=0., max_fitting_ratio=0., max_cell_guide_ratio=0.)
    violations = []

    # D54's structural margin does not authorize carrying more than the ship's
    # declared rating. Repeat the existing physical capacity rule on the same
    # combined support catalog, including the half-mass long-box shares.
    from stowage.conventions import cells_of_slot
    cells, stacks = _cargo_catalogs(instance.vessel)
    columns = {}
    for q in loads.values():
        footprint = cells_of_slot(q.slot)
        for cid in footprint:
            if cid in cells and cells[cid].stack_id in stacks:
                columns.setdefault(cells[cid].stack_id, []).append((q, len(footprint)))
    for sid, entries in sorted(columns.items()):
        stack = stacks[sid]
        lengths = {q.container.length_ft for q, _ in entries}
        limit = stack.max_mass_kg
        if 20 in lengths and stack.max_mass_20_kg is not None:
            limit = stack.max_mass_20_kg
        elif lengths <= {40, 45} and stack.max_mass_40_kg is not None:
            limit = stack.max_mass_40_kg
        mass = math.fsum(q.container.gross_mass_kg/parts for q, parts in entries)
        # Match the existing D42 background-capacity evidence boundary
        # (rules_evidence.py): equivalent-mass aggregation can exceed the
        # declared rating by floating-point roundoff. This 1e-5 kg allowance
        # applies only to entirely fixed background columns, never planned
        # cargo, and does not alter any IRS stress/force limit.
        roundoff_kg = (1e-5 if all(getattr(q.container, 'background', False)
                                  for q, _ in entries) else 0.)
        if mass > limit+roundoff_kg:
            violations.append(problem(f'Physical stack {sid} exceeds its mass rating.',
                                      'expert.physical.stack_weight',
                                      slots=[q.slot for q, _ in entries],
                                      containers=[q.container.id for q, _ in entries
                                                  if not getattr(q.container, 'background', False)],
                                      measured=mass, limit=limit, unit='kg'))

    def record(key, value, slots):
        value = float(value)
        ratios[key] = max(ratios[key], value)
        if value > 1+1e-10:
            violations.append(problem(f"{key} exceeds the declared synthetic securing limit.", RULE,
                                      slots=slots, containers=[loads[s].container.id for s in slots if not getattr(loads[s].container, "background", False)],
                                      measured=value, limit=1., unit='ratio'))

    for q in loads.values():
        if q.container.gross_mass_kg > 30500:
            violations.append(problem('Container exceeds synthetic IRS Table 3.3.1 mass rating.', RULE,
                                      slots=[q.slot], containers=[] if getattr(q.container, "background", False) else [q.container.id],
                                      measured=q.container.gross_mass_kg,
                                      limit=30500., unit='kg'))

    # Longitudinal combinations have no transverse rod force. Both directions
    # and separate g+av compression / g tension cases are propagated physically.
    longitudinal_shear = {}
    for direction in (-1., 1.):
        compression, shear = _longitudinal_reactions(loads, direction, dynamic_vertical=True)
        tension, _ = _longitudinal_reactions(loads, direction, dynamic_vertical=False)
        for slot, q in loads.items():
            longitudinal_shear[slot] = np.abs(shear[slot])
            record('max_corner_post_ratio', max(0., float(np.max(compression[slot])))/848000, [slot])
            record('max_corner_tension_ratio', max(0., -float(np.min(tension[slot])))/250000, [slot])
            record('max_fitting_ratio', max(0., float(np.max(compression[slot])))/(.85*LOCK_COMPRESSION_SWL), [slot])
            record('max_fitting_ratio', max(0., -float(np.min(tension[slot])))/(.85*LOCK_TENSION_SWL), [slot])

    for chain in _chains(loads):
        qs = [q for q, _ in chain]
        slots = list(dict.fromkeys(q.slot for q in qs))
        heights = [q.height for q in qs]
        cg_heights = [q.cg_height for q in qs]
        # Eccentric CG changes force share at the two physical ends. Negative
        # end forces beyond the support polygon use absolute worst-direction loads.
        shares = [.5+(-1 if end == 0 else 1)*q.dx/q.length for q, end in chain]
        trans = [abs(q.mass*q.at*f) for q, f in zip(qs, shares)]
        longs = [abs(q.mass*q.al*f) for q, f in zip(qs, shares)]
        # IRS-G-DES-19 §3.6.2.3, PDF p.12 (printed p.11): wind "should be
        # divided equally amongst both ends (fore and aft)". D42 background
        # envelopes are symmetric: their eccentric cargo CG shifts inertial
        # force shares, never the geometric wind-pressure centre. Retain the
        # existing synthetic expanded-envelope treatment for planned OOG.
        wind_shares = [.5 if getattr(q.container, 'background', False) else f
                       for q, f in zip(qs, shares)]
        wt = [abs(q.wind_t*f) if q.on_deck else 0. for q, f in zip(qs, wind_shares)]
        wl = [abs(q.wind_l*f) if q.on_deck else 0. for q, f in zip(qs, wind_shares)]
        # Each frame has two posts; per-end vertical weight is mass*g*share.
        vertical = [q.mass*(G+q.av)*f/2 for q, f in zip(qs, shares)]
        gravity = [q.mass*G*f/2 for q, f in zip(qs, shares)]
        if qs[0].on_deck:
            for stiffness in (15.7e6, 3.7e6):
                rack, rod, co, si, anchor = _end_response(trans, wt, heights, stiffness, cg_heights=cg_heights)
                record('max_racking_ratio', max(abs(rack))/150000, slots)
                fractions = np.array(cg_heights)/np.array(heights)
                equivalent_l = fractions*np.array(longs)+.5*np.array(wl)
                equivalent_l[:-1] += (1-fractions[1:])*np.array(longs[1:])+.5*np.array(wl[1:])
                record('max_longitudinal_racking_ratio', max(np.cumsum(equivalent_l[::-1]))/75000, slots)
                record('max_lashing_ratio', max(rod*co/150000, rod*si/225000, rod/(.85*ROD_SWL)), slots)
                for i in range(len(qs)):
                    hw = np.cumsum([0.]+heights[i:-1])+np.array(cg_heights[i:])
                    hc = np.cumsum([0.]+heights[i:-1])+.5*np.array(heights[i:])
                    dynamic_moment = float(np.dot(trans[i:], hw)+np.dot(wt[i:], hc))
                    rod_down = rod*si if i <= anchor else 0.
                    if i <= anchor:
                        dynamic_moment -= rod*co*sum(heights[i:anchor+1])
                    static_moment = sum(2*v*q.dy for v, q in zip(vertical[i:], qs[i:]))
                    gravity_moment = sum(2*v*q.dy for v, q in zip(gravity[i:], qs[i:]))
                    comp = sum(vertical[i:])+rod_down+(abs(dynamic_moment)+abs(static_moment))/WIDTH
                    tens = max(0., (abs(dynamic_moment)+abs(gravity_moment))/WIDTH-sum(gravity[i:]))
                    record('max_corner_post_ratio', comp/848000, slots)
                    record('max_corner_tension_ratio', tens/250000, slots)
                    # Both axes on a corner; use longitudinal DAG reaction shear
                    # rather than summing whole boxes across a mixed support twice.
                    long_shear = longitudinal_shear[qs[i].slot][chain[i][1]]/2
                    shear_corner = math.hypot((sum(trans[i:])+sum(wt[i:]))/2, long_shear)
                    record('max_fitting_ratio', max(tens/(.85*LOCK_TENSION_SWL),
                           comp/(.85*LOCK_COMPRESSION_SWL), shear_corner/(.85*LOCK_SHEAR_SWL)), slots)
        else:
            # Cell guides take each box's horizontal forces directly; vertical
            # loads alone accumulate down posts (IRS §3.6.4).
            for i, q in enumerate(qs):
                comp = sum(vertical[i:]) + abs(sum(2*v*r.dy for v, r in zip(vertical[i:], qs[i:])))/WIDTH
                record('max_corner_post_ratio', comp/848000, slots)
                record('max_fitting_ratio', comp/(.85*LOCK_COMPRESSION_SWL), slots)
                # Top fitting of lower box + bottom fitting of upper box act on
                # one bracket. Each force is whole-box acceleration force /4.
                lower = np.array([q.mass*q.at, q.mass*q.al])/4
                upper = np.array([qs[i+1].mass*qs[i+1].at, qs[i+1].mass*qs[i+1].al])/4 if i+1 < len(qs) else np.zeros(2)
                contact = float(np.linalg.norm(lower+upper))
                record('max_fitting_ratio', max(float(np.linalg.norm(lower)), float(np.linalg.norm(upper)))/(.85*LOCK_SHEAR_SWL), slots)
                record('max_cell_guide_ratio', _cell_guide_ratio(contact,
                       guide_design.section_modulus_m3, guide_design.area_m2), slots)
    from stowage.conventions import parse_slot
    governing = parse_slot(guide_design.governing_stack_id[:5]+'02') if guide_design.governing_stack_id else None
    return LashingReport(name='lashing', violations=violations,
                      metrics={**ratios, 'gm_m': condition.gm_m, 'roll_period_s': motions.period_roll,
                               'roll_angle_rad': motions.phi, 'block_coefficient_scantling': cb,
                               'synthetic_arrangement': 1., 'physical_box_count': float(len(loads)),
                               'guide_design_gm_upper_bound_m': guide_design.gm_m,
                               'guide_design_section_modulus_m3': guide_design.section_modulus_m3,
                               'guide_design_area_m2': guide_design.area_m2,
                               'guide_design_rated_ratio': guide_design.rated_ratio,
                               'guide_design_contact_n': guide_design.contact_n,
                               'guide_design_maximum_tiers': float(guide_design.maximum_tiers),
                               'guide_design_governing_bay': float(governing.bay) if governing else 0.,
                               'guide_design_governing_row': float(governing.row) if governing else 0.},
                      margins={key: 1-value for key, value in ratios.items()},
                      notes=[guide_design.note])


@frozen("checker.check_lashing", owner="3.3", implemented=True)
def check_lashing(instance: Instance, ship: ShipState, *, call_index: int) -> PartReport:
    """Check securing at actual loading GM; missing geometry fails closed."""
    try:
        report = _check_lashing(instance, ship, call_index=call_index)
        if not all(math.isfinite(v) for v in report.metrics.values()):
            raise ValueError("non-finite lashing loads or motions")
        return report
    except (ValueError, KeyError, IndexError, ArithmeticError, FileNotFoundError, TypeError, AttributeError, NotImplementedError,
            np.linalg.LinAlgError) as exc:
        message = f"Lashing cannot evaluate missing/invalid securing geometry: {exc}"
        return PartReport(name="lashing", ran=False, error=message, violations=[Violation(
            rule_id="checker.missing_vessel_data", severity="hard", call_index=call_index, message=message)])
