"""C4 three-phase whole-map outward model (never a legal-stow claim).

The real physical cells remain in the capacity accounting, but rows and stack
identities are forgotten after each real slot is mapped to a bay/tier footprint.
All coefficients use exact class masses/heights; only the support-plane height
is lowered. See packing_notes.md for the map from a legal stow to this LP.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass, field
import math
from fractions import Fraction
from pathlib import PurePosixPath
from time import monotonic
import warnings

import numpy as np
from scipy.optimize import Bounds, LinearConstraint, milp, linprog
from scipy.sparse import coo_matrix

from stowage.conventions import cells_of_slot, parse_slot
from stowage.io import load_instance


@dataclass
class PackingModel:
    instance_id: str
    metadata: dict
    allocations: list[dict]
    rows: list[dict[int, float]] = field(default_factory=list)
    lower: list[float] = field(default_factory=list)
    upper: list[float] = field(default_factory=list)
    row_names: list[str] = field(default_factory=list)
    variable_lower: list[float] = field(default_factory=list)
    variable_upper: list[float] = field(default_factory=list)
    cargo_moment_coeff: dict[int, float] = field(default_factory=dict)
    count_by_region_coeff: dict = field(default_factory=lambda: defaultdict(dict))
    mass_by_region_coeff: dict = field(default_factory=lambda: defaultdict(dict))
    deck_count_by_bay_coeff: dict = field(default_factory=lambda: defaultdict(dict))
    deck_mass_by_bay_coeff: dict = field(default_factory=lambda: defaultdict(dict))

    @property
    def n_variables(self):
        return len(self.variable_lower)

    @property
    def bounds(self):
        return Bounds(self.variable_lower, self.variable_upper)

    @property
    def A(self):
        rr, cc, vv = [], [], []
        for r, row in enumerate(self.rows):
            for c, v in row.items():
                if v:
                    rr.append(r); cc.append(c); vv.append(v)
        return coo_matrix((vv, (rr, cc)), shape=(len(self.rows), self.n_variables)).tocsc()

    def add_variable(self, lower=0., upper=math.inf):
        index = self.n_variables
        self.variable_lower.append(float(lower))
        self.variable_upper.append(float(upper))
        return index

    def add_row(self, coefficients, lower=-math.inf, upper=math.inf, name='extra'):
        self.rows.append(dict(coefficients))
        self.lower.append(float(lower)); self.upper.append(float(upper))
        self.row_names.append(name)

    def solve(self, *, extra_rows=(), objective=None, time_limit=30.):
        """HiGHS LP, via scipy.milp, with no integer restrictions.

        Solver infeasibility alone is a numerical LP verdict; a physical proof
        additionally needs an independently checked, outward-safe certificate.
        """
        started = monotonic()
        count = len(self.rows)
        for item in extra_rows:
            self.add_row(*item)
        try:
            c = np.zeros(self.n_variables)
            for j, v in (self.cargo_moment_coeff if objective is None else objective).items():
                c[j] = v
            # HiGHS exposes threads as an unrecognised scipy option, forwarded
            # verbatim. It is required by the shared-machine process contract.
            with warnings.catch_warnings():
                warnings.filterwarnings('ignore', message='Unrecognized options detected')
                result = milp(c, integrality=np.zeros(self.n_variables), bounds=self.bounds,
                              constraints=LinearConstraint(self.A, self.lower, self.upper),
                              options={'time_limit': float(time_limit), 'threads': 1})
            result['wall_s'] = monotonic()-started
            if result.x is not None:
                result['witness'] = self.witness(result.x)
            return result
        finally:
            del self.rows[count:]; del self.lower[count:]; del self.upper[count:]
            del self.row_names[count:]

    def witness(self, x):
        def evaluate(row):
            return math.fsum(float(x[j])*v for j, v in row.items())
        allocated = [dict(a, variable=j, count=float(x[j]))
                     for j, a in enumerate(self.allocations) if x[j] > 1e-9]
        result = dict(instance_id=self.instance_id, allocations=allocated,
                    classes=self.metadata['classes'], capacities=self.metadata['capacities'],
                    min_ordinary_height_m=self.metadata['min_ordinary_height_m'],
                    cargo_mass_kg=self.metadata['cargo_mass_kg'],
                    cargo_vertical_moment_lower_kg_m=evaluate(self.cargo_moment_coeff),
                    deck_boxes_by_odd_bay={str(b): evaluate(row) for b, row in self.deck_count_by_bay_coeff.items()},
                    deck_mass_by_odd_bay_kg={str(b): evaluate(row) for b, row in self.deck_mass_by_bay_coeff.items()},
                    deck_count_by_bay={str(b): evaluate(row) for b, row in self.deck_count_by_bay_coeff.items()},
                    deck_mass_by_bay={str(b): evaluate(row) for b, row in self.deck_mass_by_bay_coeff.items()},
                    variable_values=list(map(float, x)),
                    max_row_violation=max((max(lo-evaluate(row), evaluate(row)-hi, 0.)
                                           for row, lo, hi in zip(self.rows, self.lower, self.upper)), default=0.))
        if 'physics' in self.metadata:
            p = self.metadata['physics']
            tank_mass = {t['id']:float(x[t['variable']]) for t in p['tanks']}
            total_mass = p['fixed_mass_kg']+p['cargo_mass_kg']+math.fsum(tank_mass.values())
            lower_moment = (p['fixed_zmoment_kg_m']+evaluate(self.cargo_moment_coeff)
                            +math.fsum(tank_mass[t['id']]*t['z_lower_m'] for t in p['tanks']))
            result.update(tank_masses_kg=tank_mass,total_mass_kg=total_mass,
                physical_kg_lower_m=lower_moment/total_mass,
                gm_lashing_interval_m=p['gm_lashing_interval_m'],
                physics_outer_bounds=p)
        return result


def build_model(instance, *, visibility_top_by_stack=None):
    """Build a whole-map superset without building the search engine's mask.

    Visibility caps are optional and must themselves be outward-safe. Omitting
    them is safe. Only empty arrival/current load-list scope is supported.
    """
    if isinstance(instance, (str, bytes)):
        instance = load_instance(instance)
    if (instance.onboard.placements or instance.discharge_list or instance.onboard.covers_off
            or instance.plan_calls or instance.forecasts):
        raise ValueError('Only empty arrival with no future loads or removed covers is supported')
    cells = {c.id: c for c in instance.vessel.cells}
    stacks = {s.id: s for s in instance.vessel.stacks}
    boxes = {b.id: b for b in instance.containers}
    load = [boxes[bid] for bid in instance.load_list]
    if not load or any(not math.isfinite(b.gross_mass_kg) or b.gross_mass_kg <= 0
                       or b.height_m <= 0 for b in load):
        raise ValueError('Positive finite box masses and heights required')
    minimum_height = min(b.height_m for b in load)
    index = {cid: (sid, k) for sid, s in stacks.items() for k, cid in enumerate(s.cell_ids)}
    if set(index) != set(cells):
        raise ValueError('Physical cell/stack index mismatch')
    region_of = {cid: (parse_slot(cid).bay, bool(stacks[sid].on_deck), k)
                 for cid, (sid, k) in index.items()}
    capacity = Counter(region_of.values())
    region_stacks = defaultdict(list)
    for sid, s in stacks.items():
        if s.cell_ids:
            region_stacks[parse_slot(s.cell_ids[0]).bay, bool(s.on_deck)].append(s)
        if any(parse_slot(cid).bay != parse_slot(s.cell_ids[0]).bay for cid in s.cell_ids):
            raise ValueError('A physical stack crosses bays')
    slots = set(cells)
    for cid in cells:
        addr = parse_slot(cid)
        for bay in (addr.bay-1, addr.bay+1):
            if bay > 0 and bay % 2 == 0:
                slot = f'{bay:03d}{addr.row:02d}{addr.tier:02d}'
                if all(c in cells for c in cells_of_slot(slot)):
                    slots.add(slot)
    slot_data = []
    for slot in sorted(slots):
        footprint = cells_of_slot(slot)
        signature = tuple(sorted((*reg, n) for reg, n in Counter(region_of[c] for c in footprint).items()))
        support_lower = math.fsum(stacks[index[c][0]].base_z_m+index[c][1]*minimum_height
                                  for c in footprint)/len(footprint)
        slot_data.append((slot, footprint, signature, support_lower))
    classes = defaultdict(list)
    for b in load:
        physical_height = b.height_m+(b.oog.over_height_m if b.oog else 0.)
        key = (b.length_ft, bool(b.reefer), b.gross_mass_kg, b.height_m, physical_height,
               'under_deck' in b.special_stowage, 'on_deck' in b.special_stowage)
        classes[key].append(b.id)
    metadata = dict(classes=[], capacities=[], min_ordinary_height_m=minimum_height,
                    cargo_mass_kg=math.fsum(b.gross_mass_kg for b in load),
                    relaxation='fractional exact-mass classes; bay/tier footprints; no row, stack support coupling, DG, E6, OOG clearance, securing or ship physics')
    model = PackingModel(instance.id, metadata, [])
    cells_terms, mass_terms, height_terms = defaultdict(dict), defaultdict(dict), defaultdict(dict)
    # Either legacy or frozen replay may apply. The larger limit safely covers
    # both, without making this model depend on a global activation switch.
    crane_limit = max(instance.port.quay_crane.max_on_deck_tiers,
                      instance.port.max_crane_height_tiers or 0)
    for ci, (key, members) in enumerate(classes.items()):
        length, reefer, mass, height, physical_height, under, on = key
        units = 1 if length == 20 else 2
        metadata['classes'].append(dict(members=members, length_ft=length, reefer=reefer,
            mass_kg=mass, height_m=height, physical_height_m=physical_height,
            units=units, under_deck=under, on_deck=on))
        choices = {}
        for slot, footprint, sig, z in slot_data:
            if len(footprint) != units or any(not getattr(cells[c], f'accepts_{length}') for c in footprint):
                continue
            if reefer and not any(cells[c].reefer_plug for c in footprint):
                continue
            deck = parse_slot(slot).on_deck
            if (under and deck) or (on and not deck):
                continue
            if deck and crane_limit is not None and max(index[c][1]+1 for c in footprint) > crane_limit:
                continue
            if physical_height > min(stacks[index[c][0]].max_height_m for c in footprint):
                continue
            if visibility_top_by_stack is not None and any(
                    stacks[index[c][0]].base_z_m+index[c][1]*minimum_height+physical_height
                    > visibility_top_by_stack.get(index[c][0], math.inf) for c in footprint):
                continue
            if sig not in choices or z < choices[sig][0]:
                choices[sig] = (z, slot)
        row = {}
        for sig, (support_z, slot) in choices.items():
            j = model.add_variable(0., len(members))
            row[j] = 1.
            z = support_z+height/2
            model.allocations.append(dict(class_index=ci, signature=[list(v) for v in sig],
                z_lower_m=z, support_z_lower_m=support_z, representative_slot=slot))
            model.cargo_moment_coeff[j] = mass*z
            for bay, deck, k, count in sig:
                region = (bay, deck, k)
                cells_terms[region][j] = count
                mass_terms[bay, deck][j] = mass*count/units
                height_terms[bay, deck][j] = physical_height*count
                model.count_by_region_coeff[region][j] = count/units
                model.mass_by_region_coeff[region][j] = mass*count/units
                if deck:
                    model.deck_count_by_bay_coeff[bay][j] = model.deck_count_by_bay_coeff[bay].get(j, 0.)+count/units
                    model.deck_mass_by_bay_coeff[bay][j] = model.deck_mass_by_bay_coeff[bay].get(j, 0.)+mass*count/units
        model.add_row(row, len(members), len(members), f'class_{ci}')
    for reg, cap in sorted(capacity.items()):
        model.add_row(cells_terms[reg], upper=cap, name=f'cells_{reg}')
        bay, deck, k = reg
        if k:
            row = dict(cells_terms[reg])
            for j, coefficient in cells_terms[bay, deck, k-1].items():
                row[j] = row.get(j, 0.)-coefficient
            model.add_row(row, upper=0., name=f'prefix_{reg}')
        metadata['capacities'].append(dict(bay=bay, deck=deck, k=k, cells=cap))
    for region, sts in sorted(region_stacks.items()):
        mass_capacity = math.fsum(max(s.max_mass_kg,
                            s.max_mass_20_kg if s.max_mass_20_kg is not None else s.max_mass_kg,
                            s.max_mass_40_kg if s.max_mass_40_kg is not None else s.max_mass_kg) for s in sts)
        height_capacity = math.fsum(s.max_height_m for s in sts)
        model.add_row(mass_terms[region], upper=mass_capacity, name=f'mass_{region}')
        model.add_row(height_terms[region], upper=height_capacity, name=f'height_{region}')
    return model


def legacy_attach_departure_rows(model, instance, *, gm_lo=0., gm_hi=None, physics_module=None):
    """Attach outward tank, mass, weather-KG and necessary securing rows.

    GM here is the lashing provider's GM. It is intentionally not identified
    with the stability provider's GM. Branch upper endpoints are metadata only:
    a floor strengthens securing while the union of branches covers its range.
    """
    if physics_module is None:
        import physics as physics_module
    p = dict(physics_module.physical_bounds(instance))
    if gm_hi is None:
        gm_hi = p['gm_lashing_upper_m']
    if not 0 <= gm_lo <= gm_hi:
        raise ValueError('Invalid lashing GM interval')
    tank_variables = []
    for raw_tank in p['tanks']:
        t = dict(raw_tank)
        t['variable'] = model.add_variable(0.,t['capacity_kg'])
        tank_variables.append(t)
    p['tanks'] = tank_variables
    p['gm_lashing_interval_m'] = [gm_lo,gm_hi]
    p['scope'] = 'departure outward necessary conditions; finite-angle and second-check curve relaxed'
    model.metadata['physics'] = p
    fixed_mass = p['fixed_mass_kg']+p['cargo_mass_kg']
    # The stability loading margin accepts a 1e-10 normalized residual.
    # Widen the source endpoints before treating the row as necessary.
    guard = max(1.e-5,2.e-10*p['mass_upper_kg'])
    p['mass_comparison_guard_kg'] = guard
    model.add_row({t['variable']:1. for t in tank_variables},
                  p['mass_lower_kg']-fixed_mass-guard,p['mass_upper_kg']-fixed_mass+guard,'displacement_mass')
    # Every actual tank centroid is above z_lower; FSC >=0 means upright KG
    # <= virtual KG. Weather applicability requires virtual KG<=1.5*draft.
    # Thus this minimized physical moment must fit under the same upper KG.
    kg_upper = p['physical_kg_upper_m']
    row = {j:v/1.e6 for j,v in model.cargo_moment_coeff.items()}
    row.update({t['variable']:(t['z_lower_m']-kg_upper)/1.e6 for t in tank_variables})
    model.add_row(row,upper=(kg_upper*fixed_mass-p['fixed_zmoment_kg_m'])/1.e6,
                  name='weather_physical_kg_upper')
    boxes = {b.id:b for b in instance.containers}
    capacities = Counter()
    # Shifted long-over-long placements can move a logical end chain across
    # odd bays. Per-bay budgets need isolated long pairs; otherwise sum globally.
    cells = {c.id:c for c in instance.vessel.cells}
    partners = defaultdict(set)
    for cid,cell in cells.items():
        addr = parse_slot(cid)
        if not addr.on_deck:
            continue
        other = f'{addr.bay+2:03d}{addr.row:02d}{addr.tier:02d}'
        if other in cells and any(getattr(cell,f'accepts_{length}') and
                getattr(cells[other],f'accepts_{length}') for length in (40,45)):
            partners[addr.bay].add(addr.bay+2)
            partners[addr.bay+2].add(addr.bay)
    isolated_pairs = all(len(ps)<=1 for ps in partners.values())
    p['isolated_long_bay_pairs'] = isolated_pairs
    for s in instance.vessel.stacks:
        if s.on_deck and s.cell_ids:
            capacities[parse_slot(s.cell_ids[0]).bay] += 1
    force_rows = defaultdict(dict)
    force_capacity = None
    excluded = 0
    # Classes may differ in OOG horizontal asymmetry. Coefficient minima over
    # all members preserve the legal allocation of every original member.
    cache = {}
    for j,a in enumerate(model.allocations):
        cls = model.metadata['classes'][a['class_index']]
        deck = any(reg[1] for reg in a['signature'])
        coefficient_records = []
        for member in cls['members']:
            box = boxes[member]
            physical_height = box.height_m+(box.oog.over_height_m if box.oog else 0.)
            z = a['support_z_lower_m']+.45*physical_height
            key = (member,deck,z,gm_lo)
            if key not in cache:
                cache[key] = physics_module.securing_coefficients(instance,box,
                                        on_deck=deck,z_lower=z,gm_lower=gm_lo)
            coefficient_records.append(cache[key])
        ratios = ('own_fitting_ratio','own_corner_ratio','own_mass_ratio')
        if any(min(record[key] for record in coefficient_records)>1.+1.e-8 for key in ratios):
            model.variable_upper[j] = 0.
            excluded += 1
        if not deck:
            continue
        force_capacity = min(record['deck_force_capacity_per_physical_column_n'] for record in coefficient_records)
        coefficient = min(record['transverse_force_n']*record['per_column_share_factor']
                          for record in coefficient_records)
        for bay,deck,k,count in a['signature']:
            if deck:
                force_rows[bay][j] = force_rows[bay].get(j,0.)+coefficient*count/cls['units']
    if force_capacity is not None:
        if isolated_pairs:
            for bay,row in force_rows.items():
                model.add_row({j:v/1.e6 for j,v in row.items()},
                              upper=capacities[bay]*force_capacity/1.e6,name=f'securing_force_bay_{bay}')
        else:
            total = defaultdict(float)
            for row in force_rows.values():
                for j,value in row.items():
                    total[j] += value/1.e6
            model.add_row(total,upper=sum(capacities.values())*force_capacity/1.e6,
                          name='securing_force_global')
    p['securing_excluded_allocations'] = excluded
    p['securing_bay_physical_columns'] = dict(capacities)
    p['securing_force_capacity_per_column_n'] = force_capacity
    return p


MOMENT_SCALE = 1.e6
CONSUMABLE_KINDS = frozenset(('fuel_oil','diesel_oil','lube_oil','fresh_water','stores'))


def _directed(value, upwards=False):
    """One directed conversion from exact binary-input rational arithmetic."""
    value = Fraction(value)
    result = float(value)
    wrong_side = Fraction(result)<value if upwards else Fraction(result)>value
    if wrong_side:
        result = math.nextafter(result, math.inf if upwards else -math.inf)
    return result


def voyage_contexts(instance):
    """Exact supported D57 default proportional phases; never guess a fraction.

    Explicit kg consumption gives a mass-dependent common fraction. It needs a
    bilinear outer envelope and is deliberately unsupported by this entrypoint.
    """
    from stowage.model.voyage import VoyageSpec, validate_voyage_spec
    if instance.voyage is None or instance.vessel.voyage_policy is None:
        raise ValueError('Explicit D57 voyage and vessel voyage policy required')
    raw = instance.voyage
    leg = VoyageSpec.model_validate(raw.model_dump() if hasattr(raw,'model_dump') else raw)
    validate_voyage_spec(instance.vessel,leg)
    if leg.consumption is not None or leg.ballast_operations:
        raise ValueError('Mass-dependent explicit consumption / ballast-at-sea is unsupported: report unknown')
    policy = instance.vessel.voyage_policy
    policy = policy.model_dump() if hasattr(policy,'model_dump') else policy
    remaining = float(policy['default_arrival_remaining_fraction'])
    phases = []
    for phase,r,identity in (('departure',1.,leg.departure),
                              ('mid_voyage',(1.+remaining)/2,leg.departure),
                              ('arrival',remaining,leg.arrival)):
        if phase == 'departure':
            context = instance
        else:
            port = instance.port.model_copy(update={'locode':identity.locode,
                'water_density_kg_m3':identity.water_density_kg_m3,
                'max_draught_m':None,'port_rules':[]})
            context = instance.model_copy(update={'call_index':identity.call_index,'port':port})
        phases.append(dict(name=phase,remaining_fraction=r,context=context,
            tank_factors={t.id:(r if t.kind in CONSUMABLE_KINDS else 1.) for t in instance.vessel.tanks}))
    return phases


def static_bounds(instance):
    """Source-derived bounds with fixed tanks fixed, adjustable tanks free.

    Entire solid tank bounding boxes enclose all upright/tilted liquid arms.
    No half-fill sounding or sampled hydrostatic tangent is a proof row.
    """
    from stowage.checker import stability,visibility
    h = instance.vessel.hull
    if h is None:
        raise ValueError('Hull data required')
    ref = PurePosixPath(h.hull_offsets_ref.split('#')[0])
    if str(ref.parent) != 'params/fleet' or ref.suffix != '.toml':
        raise ValueError('Unsupported hull reference')
    _,params = visibility._geometry(ref.stem)
    raw = {q['id']:q for q in params.get('tanks',[])}
    tanks = []
    for t in instance.vessel.tanks:
        if t.id not in raw:
            raise ValueError(f'Tank {t.id} lacks a solid geometry enclosure')
        g = raw[t.id].get('geometry',raw[t.id])
        needed = ('x_min_m','x_max_m','z_min_m','z_max_m')
        if any(k not in g for k in needed):
            raise ValueError(f'Tank {t.id} needs explicit bounding box geometry')
        cap = t.capacity_m3*t.content_density_kg_m3
        initial = instance.onboard.tank_masses_kg.get(t.id,0.)
        lo,hi = (0.,cap) if t.adjustable else (initial,initial)
        if not 0 <= lo <= hi <= cap*(1.+1e-12):
            raise ValueError(f'Tank {t.id} initial mass invalid')
        tanks.append(dict(id=t.id,kind=t.kind,adjustable=t.adjustable,capacity_kg=cap,
            mass_lower_kg=lo,mass_upper_kg=hi,lcg_lower_m=float(g['x_min_m']),
            lcg_upper_m=float(g['x_max_m']),vcg_lower_m=float(g['z_min_m']),
            vcg_upper_m=float(g['z_max_m'])))
    fixed = list(visibility._fixed_loads(instance.vessel))
    fixed_mass = h.lightship_mass_kg+math.fsum(q.mass_kg for q in fixed)
    fixed_z = h.lightship_mass_kg*h.lightship_vcg_m+math.fsum(q.mass_kg*q.z_m for q in fixed)
    fixed_x = h.lightship_mass_kg*h.lightship_lcg_m+math.fsum(q.mass_kg*q.x_m for q in fixed)
    trimlo,trimhi,masslo,masshi = stability._loading_policy(instance)
    draft_hi = min(h.max_draught_m,instance.port.max_draught_m or h.max_draught_m)
    guard = max(1.e-5,2.e-10*masshi)
    return dict(tanks=tanks,fixed_mass_kg=fixed_mass,fixed_zmoment_kg_m=fixed_z,
        fixed_xmoment_kg_m=fixed_x,mass_lower_kg=masslo-guard,mass_upper_kg=masshi+guard,
        trim_lower_m=trimlo-1.e-8,trim_upper_m=trimhi+1.e-8,
        draft_lower_m=max(0.,h.beam_m/3.5-1.e-8),draft_upper_m=draft_hi+1.e-8,
        gm_lashing_lower_m=0.,gm_lashing_upper_m=h.max_gm_lashing_m,
        numerical_mass_guard_kg=guard)


def augment_geometry(model,instance):
    """Enclose cargo X/Z moments above and below for each allocation.

    The upper geometry union deliberately ignores unary filters: it is larger
    than the actual class domain, so its extrema remain outward-safe.
    """
    cells = {c.id:c for c in instance.vessel.cells}
    stacks = {s.id:s for s in instance.vessel.stacks}
    index = {cid:(sid,k) for sid,s in stacks.items() for k,cid in enumerate(s.cell_ids)}
    slots = set(cells)
    for cid in cells:
        a = parse_slot(cid)
        for bay in (a.bay-1,a.bay+1):
            if bay > 0 and bay%2==0:
                slot = f'{bay:03d}{a.row:02d}{a.tier:02d}'
                if all(c in cells for c in cells_of_slot(slot)):
                    slots.add(slot)
    by_signature = defaultdict(list)
    for slot in slots:
        foot = cells_of_slot(slot)
        sig = tuple(sorted((*r,n) for r,n in Counter(
            (parse_slot(c).bay,bool(stacks[index[c][0]].on_deck),index[c][1]) for c in foot).items()))
        top = sum((Fraction(stacks[index[c][0]].base_z_m)+Fraction(stacks[index[c][0]].max_height_m)
                   for c in foot),Fraction(0))/len(foot)
        x = sum((Fraction(cells[c].lcg_m) for c in foot),Fraction(0))/len(foot)
        by_signature[sig].append((top,x))
    zlo,zhi,xlo,xhi = {},{},{},{}
    class_extrema = defaultdict(lambda:dict(zlo=math.inf,zhi=-math.inf,xlo=math.inf,xhi=-math.inf))
    for j,a in enumerate(model.allocations):
        cls = model.metadata['classes'][a['class_index']]
        rows = by_signature[tuple(tuple(v) for v in a['signature'])]
        # A legal stack's physical height includes OOG; its ordinary loading
        # support cannot exceed physical stack top minus this physical height.
        upper = max(top-Fraction(cls['physical_height_m'])+Fraction(cls['height_m'])/2 for top,x in rows)+Fraction(1.e-8)
        lower = Fraction(a['z_lower_m'])-Fraction(1.e-8)
        lowx,highx = min(x for top,x in rows)-Fraction(1.e-8),max(x for top,x in rows)+Fraction(1.e-8)
        mass = Fraction(cls['mass_kg'])
        zlo[j],zhi[j] = _directed(mass*lower),_directed(mass*upper,True)
        xlo[j],xhi[j] = _directed(mass*lowx),_directed(mass*highx,True)
        a.update(z_upper_m=_directed(upper,True),lcg_lower_m=_directed(lowx),lcg_upper_m=_directed(highx,True))
        ex = class_extrema[a['class_index']]
        ex['zlo']=min(ex['zlo'],_directed(lower)); ex['zhi']=max(ex['zhi'],_directed(upper,True))
        ex['xlo']=min(ex['xlo'],_directed(lowx)); ex['xhi']=max(ex['xhi'],_directed(highx,True))
    model.metadata['cargo_class_extrema'] = dict(class_extrema)
    return dict(z_lower=zlo,z_upper=zhi,x_lower=xlo,x_upper=xhi)


def _ratio_enclosure(fixed_mass,fixed_moment,tank_terms,*,maximize=False):
    """Exact fractional-linear extreme over independent mass intervals.

    Starting at all lower masses, add optional liquid in increasing centroid
    order for a minimum (decreasing for maximum). Every extreme occurs at one
    of these prefixes: an optimal derivative has sign (z-current ratio).
    """
    m,z = Fraction(fixed_mass),Fraction(fixed_moment)
    optional = []
    for lower,upper,centroid in tank_terms:
        lo,hi,c = Fraction(lower),Fraction(upper),Fraction(centroid)
        m += lo; z += lo*c
        optional.append((c,hi-lo))
    if m <= 0:
        raise ValueError('Positive displacement base required')
    best = z/m
    for c,delta in sorted(optional,reverse=maximize):
        m += delta; z += delta*c
        candidate=z/m
        best=max(best,candidate) if maximize else min(best,candidate)
    return _directed(best,upwards=maximize)


def analytic_windows(model,instance,phases):
    """Strict algebraic scalar enclosures, independent of an LP optimum.

    These can be checked by exact rational arithmetic from raw moments and
    classwise geometry minima/maxima. They intentionally ignore correlations.
    """
    classes = model.metadata['classes']
    extrema = model.metadata['cargo_class_extrema']
    cargo_mass = sum(Fraction(c['mass_kg'])*len(c['members']) for c in classes)
    cargo_moments = {}
    for axis in ('zlo','zhi','xlo','xhi'):
        if any(i not in extrema for i in range(len(classes))):
            cargo_moments[axis] = None
        else:
            cargo_moments[axis] = sum(Fraction(c['mass_kg'])*len(c['members'])*Fraction(extrema[i][axis])
                                       for i,c in enumerate(classes))
    for axis,value in model.metadata.get('cargo_global_bounds',{}).items():
        if axis in cargo_moments and value is not None:
            if axis.endswith('lo'):
                cargo_moments[axis]=max(cargo_moments[axis],Fraction(value))
            else:
                cargo_moments[axis]=min(cargo_moments[axis],Fraction(value))
    out = {}
    for phase in phases:
        p=phase['bounds']; factors=phase['tank_factors']
        base_mass=Fraction(p['fixed_mass_kg'])+cargo_mass
        masslo=base_mass+sum(Fraction(t['mass_lower_kg'])*Fraction(factors[t['id']]) for t in p['tanks'])
        masshi=base_mass+sum(Fraction(t['mass_upper_kg'])*Fraction(factors[t['id']]) for t in p['tanks'])
        scalar=dict(mass_kg=[max(p['mass_lower_kg'],_directed(masslo)-1.e-5),min(p['mass_upper_kg'],_directed(masshi,True)+1.e-5)],
                    draft_m=[p['draft_lower_m'],p['draft_upper_m']],trim_m=[p['trim_lower_m'],p['trim_upper_m']],
                    gm_lashing_m=[0.,p['gm_lashing_upper_m']],remaining_fraction=phase['remaining_fraction'])
        for label,loaxis,hiaxis,armlo,armhi,fixedkey in (
                ('kg_m','zlo','zhi','vcg_lower_m','vcg_upper_m','fixed_zmoment_kg_m'),
                ('lcg_m','xlo','xhi','lcg_lower_m','lcg_upper_m','fixed_xmoment_kg_m')):
            if cargo_moments[loaxis] is None:
                scalar[label]=[-math.inf,math.inf]
                continue
            termslo=[(Fraction(t['mass_lower_kg'])*Fraction(factors[t['id']]),
                      Fraction(t['mass_upper_kg'])*Fraction(factors[t['id']]),t[armlo]) for t in p['tanks']]
            termshi=[(Fraction(t['mass_lower_kg'])*Fraction(factors[t['id']]),
                      Fraction(t['mass_upper_kg'])*Fraction(factors[t['id']]),t[armhi]) for t in p['tanks']]
            lower=_ratio_enclosure(base_mass,Fraction(p[fixedkey])+cargo_moments[loaxis],termslo)
            upper=_ratio_enclosure(base_mass,Fraction(p[fixedkey])+cargo_moments[hiaxis],termshi,maximize=True)
            if label=='kg_m':
                upper=min(upper,1.5*p['draft_upper_m']+1.e-8)
            scalar[label]=[lower-1.e-8,upper+1.e-8]
        scalar['certification']='algebraic outer enclosure; rational endpoint evaluation, no LP dual claim'
        out[phase['name']]=scalar
    return out


class JointModel(PackingModel):
    def witness(self,x):
        result=super().witness(x)
        joint=self.metadata['joint']
        result['tank_masses_kg']={t['id']:float(x[t['variable']]) for t in joint['tanks']}
        result['phases']={}
        for phase,indices in joint['phase_variables'].items():
            mass=float(x[indices['mass']])
            result['phases'][phase]=dict(mass_kg=mass*MOMENT_SCALE,
                kg_m=float(x[indices['z_moment']])/mass,
                lcg_m=float(x[indices['x_moment']])/mass,
                remaining_fraction=indices['remaining_fraction'],
                kg_interpretation='outer moment witness; not achieved checker KG')
        result['strict_windows']=joint['strict_windows']
        result['scope']='fractional three-phase outward witness; physical feasibility unknown'
        return result


def build_joint_model(instance,*,phase_windows=None,physics_module=None,gm_lo=0.,area_caps=None):
    """Build joint packing + shared tanks + all three D57 moment conditions.

    phase_windows maps phase names to certified kg_m/lcg_m/draft_m/trim_m
    intervals. area_caps may tighten kg_m upper endpoints only when supplied
    with certified=True and physical_kg_upper_m. Heuristic rows belong outside
    this model; they must never be passed here as strict windows.
    """
    base=build_model(instance)
    model=JointModel(**base.__dict__)
    geometry=augment_geometry(model,instance)
    model.metadata['cargo_geometry_coefficients']=geometry
    phases=voyage_contexts(instance)
    for phase in phases:
        phase['bounds']=static_bounds(phase['context'])
    strict=analytic_windows(model,instance,phases)
    phase_windows=phase_windows or {}
    area_caps=area_caps or {}
    # Shared departure masses retain every fixed tank exactly, unlike C3's
    # independent 0..capacity consumables. D57 supplies phase factors only.
    tanks=[]
    for raw in phases[0]['bounds']['tanks']:
        t=dict(raw); t['variable']=model.add_variable(t['mass_lower_kg'],t['mass_upper_kg']); tanks.append(t)
    cvars={axis:model.add_variable(-math.inf,math.inf) for axis in ('z','x')}
    for axis in ('z','x'):
        lo={j:v/MOMENT_SCALE for j,v in geometry[axis+'_lower'].items()}; lo[cvars[axis]]=-1.
        hi={j:-v/MOMENT_SCALE for j,v in geometry[axis+'_upper'].items()}; hi[cvars[axis]]=1.
        model.add_row(lo,upper=0.,name='cargo_'+axis+'_moment_lower')
        model.add_row(hi,upper=0.,name='cargo_'+axis+'_moment_upper')
    phase_variables={}
    for phase in phases:
        name=phase['name']; p=phase['bounds']; factors=phase['tank_factors']
        win={k:list(v) if isinstance(v,list) else v for k,v in strict[name].items()}
        for key,interval in phase_windows.get(name,{}).items():
            key={'physical_kg_m':'kg_m','mean_draught_m':'draft_m','upright_trim_m':'trim_m'}.get(key,key)
            if key in ('kg_m','lcg_m','mass_kg','draft_m','trim_m'):
                win[key]=[max(win[key][0],interval[0]),min(win[key][1],interval[1])]
        win['kg_m'][1]=min(win['kg_m'][1],1.5*win['draft_m'][1]+1.e-8)
        cap=area_caps.get(name,{})
        if 'physical_kg_upper_m' in cap:
            if not cap.get('certified',False):
                raise ValueError('Area cap supplied without outward certification')
            win['kg_m'][1]=min(win['kg_m'][1],cap['physical_kg_upper_m'])
        m=model.add_variable(win['mass_kg'][0]/MOMENT_SCALE,win['mass_kg'][1]/MOMENT_SCALE)
        z=model.add_variable(-math.inf,math.inf); x=model.add_variable(-math.inf,math.inf)
        massrow={m:1.}
        for t in tanks:
            massrow[t['variable']]=-factors[t['id']]/MOMENT_SCALE
        fixedmass=p['fixed_mass_kg']+model.metadata['cargo_mass_kg']
        model.add_row(massrow,fixedmass/MOMENT_SCALE,fixedmass/MOMENT_SCALE,name+'_mass')
        for axis,var,armlo,armhi,fixedkey in (
                ('z',z,'vcg_lower_m','vcg_upper_m','fixed_zmoment_kg_m'),
                ('x',x,'lcg_lower_m','lcg_upper_m','fixed_xmoment_kg_m')):
            low={var:1.,cvars[axis]:-1.}; high=dict(low)
            for t in tanks:
                factor=factors[t['id']]/MOMENT_SCALE
                low[t['variable']]=-factor*t[armlo]
                high[t['variable']]=-factor*t[armhi]
            model.add_row(low,lower=p[fixedkey]/MOMENT_SCALE,name=name+'_'+axis+'_moment_lower')
            model.add_row(high,upper=p[fixedkey]/MOMENT_SCALE,name=name+'_'+axis+'_moment_upper')
        for label,var in (('kg_m',z),('lcg_m',x)):
            lower,upper=win[label]
            if math.isfinite(lower):model.add_row({var:1.,m:-lower},lower=0.,name=name+'_'+label+'_lower')
            if math.isfinite(upper):model.add_row({var:1.,m:-upper},upper=0.,name=name+'_'+label+'_upper')
        phase_variables[name]=dict(mass=m,z_moment=z,x_moment=x,remaining_fraction=phase['remaining_fraction'],
                                   windows=win,tank_factors=factors)
    model.metadata['joint']=dict(tanks=tanks,phase_variables=phase_variables,
        cargo_moment_variables=cvars,strict_windows=strict,
        phase_windows_source='static source rules + exact rational classwise moment enclosures',
        area_caps=area_caps,native_gm_providers='distinct; no equality or guessed transfer bound')
    if physics_module is not None:
        # Reuse only the necessary securing floors, not C3's free consumable
        # variables or departure-only physical-moment surrogate.
        add_securing_rows(model,instance,physics_module,gm_lo=gm_lo)
    return model


def export_windows(model):
    """Stable external schema: strict scalar bounds and heuristic targets apart."""
    joint=model.metadata['joint']
    phases={}
    for phase,win in joint['strict_windows'].items():
        phases[phase]=dict(physical_kg_m=win['kg_m'],mass_kg=win['mass_kg'],
            lcg_m=win['lcg_m'],mean_draught_m=win['draft_m'],upright_trim_m=win['trim_m'],
            gm_lashing_m=win['gm_lashing_m'],remaining_fraction=win['remaining_fraction'],
            area_upper_mrad=None,required_area_lower_mrad=None,
            proof_eligible=dict(scalar_enclosures=True,numerical_lp_infeasibility=False,
                finite_angle_area=False,shared_stability_lashing_gm=False))
    classes=model.metadata['classes']
    class_allocations=defaultdict(list)
    for a in model.allocations:
        class_allocations[a['class_index']].append(a)
    bays=sorted(model.deck_count_by_bay_coeff)
    by_bay={str(b):dict(count=[Fraction(0),Fraction(0)],mass_kg=[Fraction(0),Fraction(0)]) for b in bays}
    countlo,counthi,masslo,masshi=Fraction(0),Fraction(0),Fraction(0),Fraction(0)
    total_cells=Fraction(0); forced_cells=Fraction(0); optional=[]
    for i,cls in enumerate(classes):
        n=len(cls['members']); mass=Fraction(cls['mass_kg']); units=cls['units']
        options=class_allocations[i]
        if not options:
            continue
        shares=[sum(Fraction(v[3],units) for v in a['signature'] if v[1]) for a in options]
        low,high=min(shares),max(shares)
        countlo+=n*low; counthi+=n*high
        masslo+=n*mass*low; masshi+=n*mass*high
        total_cells+=n*units
        if low==1:
            forced_cells+=n*units
        elif high:
            optional.append((mass/units,Fraction(n*units)))
        for bay in bays:
            bs=[sum(Fraction(v[3],units) for v in a['signature'] if v[1] and v[0]==bay) for a in options]
            lo,hi=n*min(bs),n*max(bs)
            by_bay[str(bay)]['count'][0]+=lo; by_bay[str(bay)]['count'][1]+=hi
            by_bay[str(bay)]['mass_kg'][0]+=lo*mass; by_bay[str(bay)]['mass_kg'][1]+=hi*mass
    hold_cells=sum(c['cells'] for c in model.metadata['capacities'] if not c['deck'])
    deck_cells=sum(c['cells'] for c in model.metadata['capacities'] if c['deck'])
    min_units=min(c['units'] for c in classes)
    max_units=max(c['units'] for c in classes)
    forced_deck_cells=max(Fraction(0),total_cells-hold_cells)
    # Every deck box occupies at most max_units cells and at least min_units.
    # Keep this continuous: rounding upward would exclude fractional LP images.
    countlo=max(countlo,forced_deck_cells/max_units)
    counthi=min(counthi,Fraction(deck_cells,min_units))
    bay_deck_cells=Counter()
    for capacity in model.metadata['capacities']:
        if capacity['deck']:bay_deck_cells[str(capacity['bay'])]+=capacity['cells']
    for bay,limits in by_bay.items():
        limits['count'][1]=min(limits['count'][1],Fraction(bay_deck_cells[bay],min_units))
    needed=max(Fraction(0),total_cells-hold_cells-forced_cells)
    capacity_masslo=masslo
    for cost,available in sorted(optional):
        use=min(needed,available)
        capacity_masslo+=use*cost; needed-=use
    for bay in by_bay.values():
        for key,interval in bay.items():
            bay[key]=[_directed(interval[0]),_directed(interval[1],True)]
    return dict(instance_id=model.instance_id,phase_windows=phases,
        cargo=dict(deck_count=[_directed(countlo),_directed(counthi,True)],
            deck_mass_kg=[_directed(capacity_masslo),_directed(masshi,True)],
            minimum_deck_physical_cells=max(0.,float(total_cells)-hold_cells),by_bay=by_bay),
        tanks=dict(shared_departure_mass_kg={t['id']:[t['mass_lower_kg'],t['mass_upper_kg']] for t in joint['tanks']}),
        guidance_targets={},physical_verdict='unknown',
        certification='source-derived algebraic outer windows; no achieved KG or numerical infeasibility proof')


def project_plan(model,instance,plan,*,phase_metrics=None):
    """Project one replay-legal exact stow into every joint LP row.

    This is a validity check, not the full safety checker. Optional native
    phase_metrics supplies mass_kg/kg_m/lcg_m from freshly checked phases.
    Otherwise raw geometry plus native upright tank sounding supplies moments.
    """
    from stowage.checker import departure_condition,visibility
    from stowage.vessel.tanks import TankGeometry
    ship=departure_condition(instance,plan)
    x=np.zeros(model.n_variables)
    cells={c.id:c for c in instance.vessel.cells}
    stacks={s.id:s for s in instance.vessel.stacks}
    index={cid:(sid,k) for sid,s in stacks.items() for k,cid in enumerate(s.cell_ids)}
    boxes={b.id:b for b in instance.containers}
    member_class={bid:i for i,c in enumerate(model.metadata['classes']) for bid in c['members']}
    lookup={(a['class_index'],tuple(tuple(v) for v in a['signature'])):j for j,a in enumerate(model.allocations)}
    occupied={cid:boxes[p.container_id] for p in ship.placements for cid in cells_of_slot(p.slot)}
    support={}
    for s in stacks.values():
        z=s.base_z_m
        for cid in s.cell_ids:
            support[cid]=z
            if cid in occupied:z+=occupied[cid].height_m
    cargo_x,cargo_z=0.,0.
    for placement in ship.placements:
        b=boxes[placement.container_id]; foot=cells_of_slot(placement.slot)
        sig=tuple(sorted((*reg,n) for reg,n in Counter(
            (parse_slot(cid).bay,bool(stacks[index[cid][0]].on_deck),index[cid][1]) for cid in foot).items()))
        x[lookup[member_class[b.id],sig]]+=1.
        cargo_x+=b.gross_mass_kg*math.fsum(cells[cid].lcg_m for cid in foot)/len(foot)
        cargo_z+=b.gross_mass_kg*(math.fsum(support[cid] for cid in foot)/len(foot)+b.height_m/2)
    joint=model.metadata['joint']
    x[joint['cargo_moment_variables']['x']]=cargo_x/MOMENT_SCALE
    x[joint['cargo_moment_variables']['z']]=cargo_z/MOMENT_SCALE
    for t in joint['tanks']:x[t['variable']]=ship.tank_masses_kg.get(t['id'],0.)
    rawref=PurePosixPath(instance.vessel.hull.hull_offsets_ref.split('#')[0])
    _,params=visibility._geometry(rawref.stem)
    rawtanks={t['id']:t for t in params.get('tanks',[])}
    phase_values={}
    for phase in voyage_contexts(instance):
        name=phase['name']; indices=joint['phase_variables'][name]
        if phase_metrics is not None and 'lcg_m' in phase_metrics[name]:
            metrics=phase_metrics[name]
            mass=metrics['mass_kg']; z=mass*metrics['kg_m']; cx=mass*metrics['lcg_m']
        else:
            p=static_bounds(phase['context'])
            mass=p['fixed_mass_kg']+model.metadata['cargo_mass_kg']
            z=p['fixed_zmoment_kg_m']+cargo_z; cx=p['fixed_xmoment_kg_m']+cargo_x
            for t in instance.vessel.tanks:
                m=ship.tank_masses_kg.get(t.id,0.)*phase['tank_factors'][t.id]
                if not m:continue
                solid=TankGeometry.from_params(rawtanks[t.id],ship_params=params)
                sounding=solid.sounding(min(m/t.content_density_kg_m3,solid.capacity_m3))
                mass+=m; z+=m*sounding.vcg_m; cx+=m*sounding.lcg_m
            if phase_metrics is not None:
                metrics=phase_metrics[name]
                # Native reports expose KG/mass but no LCG. Reuse their exact
                # values for those two coordinates; raw sounding supplies X.
                mass=metrics['mass_kg']; z=mass*metrics.get('kg_m',metrics.get('kg_physical_m'))
        x[indices['mass']]=mass/MOMENT_SCALE
        x[indices['z_moment']]=z/MOMENT_SCALE
        x[indices['x_moment']]=cx/MOMENT_SCALE
        phase_values[name]=dict(mass_kg=mass,kg_m=z/mass,lcg_m=cx/mass)
    activity=np.asarray(model.A@x).ravel()
    violations=[]
    for name,value,lo,hi in zip(model.row_names,activity,model.lower,model.upper):
        error=max(lo-value,value-hi,0.)
        if error>1.e-6:violations.append(dict(row=name,value=float(value),lower=lo,upper=hi,violation=float(error)))
    for j,(value,lo,hi) in enumerate(zip(x,model.variable_lower,model.variable_upper)):
        if max(lo-value,value-hi,0.)>1.e-6:
            violations.append(dict(variable=j,value=float(value),lower=lo,upper=hi))
    return dict(projected=not violations,max_row_violation=float(max(
        np.max(np.maximum(np.asarray(model.lower)-activity,0.)),
        np.max(np.maximum(activity-np.asarray(model.upper),0.)))),
        violations=violations,phases=phase_values,variable_values=x.tolist(),
        scope='raw exact-stow projection only; full checker remains mandatory')


def verify_support_certificate(matrix,certificate):
    """Exact bounded-variable Lagrangian bound; no zero-residual assumption.

    Every <= row has multiplier y<=0; equality multipliers are unrestricted.
    For any box-bounded feasible x, c*x >= y*b + min_box((c-y*A)*x).
    The residual term is evaluated exactly over decoded binary floats.
    """
    n=len(matrix['lower'])
    if len(matrix['upper'])!=n:
        raise ValueError('Variable bound dimension mismatch')
    def number(value):
        if isinstance(value,bool) or not isinstance(value,(int,float)) or not math.isfinite(value):
            raise ValueError('Finite numerical coefficient required')
        return Fraction(value)
    def entries(values):
        seen=set()
        for j,value in values:
            if isinstance(j,bool) or not isinstance(j,int) or not 0<=j<n or j in seen:
                raise ValueError('Sparse indices must be unique integers in range')
            seen.add(j)
            yield j,number(value)
    residual=[Fraction(0) for _ in range(n)]
    for j,value in entries(certificate['objective']):
        residual[j]=value
    constant=Fraction(0)
    for rows,multipliers,inequality in ((matrix['ub_rows'],certificate['ub_multipliers'],True),
                                         (matrix['eq_rows'],certificate['eq_multipliers'],False)):
        if len(rows)!=len(multipliers):
            raise ValueError('Certificate multiplier dimension mismatch')
        for row,multiplier in zip(rows,multipliers):
            y=number(multiplier)
            if inequality and y>0:raise ValueError('Positive <= multiplier is invalid')
            constant+=y*number(row['rhs'])
            for j,a in entries(row['coefficients']):
                residual[j]-=y*a
    support=Fraction(0)
    for value,lo,hi in zip(residual,matrix['lower'],matrix['upper']):
        low,high=number(lo),number(hi)
        if lo>hi:
            raise ValueError('Finite valid bounds required for residual support')
        support+=min(value*low,value*high)
    bound=constant+support
    return dict(valid=True,lower_bound=_directed(bound),
                exact_numerator=str(bound.numerator),exact_denominator=str(bound.denominator),
                lagrangian_constant=_directed(constant),residual_box_minimum=_directed(support),
                max_abs_coefficient_residual=max((float(abs(r)) for r in residual),default=0.))


def certified_cargo_bounds(model,*,seconds_per_lp=3.,include_certificate=True):
    """Four short LPs plus exact rational support certificates for cargo X/Z.

    Only necessary packing rows participate: class counts, physical cells,
    aggregate prefixes and optimistic mass/height capacities. The latter
    capacities are widened for native summation roundoff. No tank, GM, area,
    local tangent, integer restriction or restricted page pool is used.
    """
    started=monotonic(); n=len(model.allocations)
    matrix=dict(lower=[0.]*n,upper=list(model.variable_upper[:n]),ub_rows=[],eq_rows=[])
    # Own securing exclusions may already be installed, but the cargo bound
    # is deliberately independent of any GM branch. Restore class-count UBs.
    matrix['upper']=[float(len(model.metadata['classes'][a['class_index']]['members'])) for a in model.allocations]
    for row,lo,hi,name in zip(model.rows,model.lower,model.upper,model.row_names):
        if not name.startswith(('class_','cells_','prefix_','mass_','height_')):
            continue
        if any(j>=n for j in row):
            raise ValueError('Unexpected nonallocation variable in packing row')
        coefficients=sorted(row.items())
        if lo==hi:
            matrix['eq_rows'].append(dict(name=name,coefficients=coefficients,rhs=lo))
            continue
        # A native stack sum can accept an exact sum infinitesimally beyond
        # its capacity. These guards exceed operation-count error on supported
        # finite maps; all coefficient/source assumptions remain recorded.
        guard=1.e-5 if name.startswith('mass_') else (1.e-8 if name.startswith('height_') else 0.)
        if math.isfinite(hi):matrix['ub_rows'].append(dict(name=name,coefficients=coefficients,rhs=hi+guard))
        if math.isfinite(lo):matrix['ub_rows'].append(dict(name=name+'_lower',coefficients=[(j,-v) for j,v in coefficients],rhs=-lo+guard))
    def sparse(rows):
        rr,cc,vv=[],[],[]
        for r,row in enumerate(rows):
            for j,value in row['coefficients']:
                rr.append(r);cc.append(j);vv.append(value)
        return coo_matrix((vv,(rr,cc)),shape=(len(rows),n)).tocsc()
    Aub,Aeq=sparse(matrix['ub_rows']),sparse(matrix['eq_rows'])
    bub=[r['rhs'] for r in matrix['ub_rows']];beq=[r['rhs'] for r in matrix['eq_rows']]
    out=dict(cargo_bounds={},directions={},wall_s=None,
             certification='exact rational Lagrangian support over serialized necessary packing relaxation',
             physical_proof=False,physical_note='Coefficient/source outwardness separate from exact algebraic bound verification')
    geometry=model.metadata['cargo_geometry_coefficients']
    for axis,direction in (('z','lower'),('z','upper'),('x','lower'),('x','upper')):
        objective=geometry[axis+'_'+direction]
        sign=1. if direction=='lower' else -1.
        c=np.zeros(n)
        for j,value in objective.items():c[j]=sign*value
        with warnings.catch_warnings():
            warnings.filterwarnings('ignore',message='Unrecognized options detected')
            result=linprog(c,A_ub=Aub,b_ub=bub,A_eq=Aeq,b_eq=beq,
                bounds=list(zip(matrix['lower'],matrix['upper'])),method='highs',
                options={'threads':1,'time_limit':float(seconds_per_lp)})
        name=axis+'_'+direction
        record=dict(solver_status=int(result.status),solver_message=result.message,
                    numerical_objective=None if result.fun is None else float(result.fun),
                    certificate_verified=False)
        if result.status==0:
            cert=dict(objective=[(int(j),float(value)) for j,value in enumerate(c) if value],
                ub_multipliers=[min(0.,float(v)) for v in result.ineqlin.marginals],
                eq_multipliers=list(map(float,result.eqlin.marginals)))
            checked=verify_support_certificate(matrix,cert)
            bound=checked['lower_bound'] if direction=='lower' else -checked['lower_bound']
            if direction=='upper':bound=math.nextafter(bound,math.inf)
            out['cargo_bounds'][axis+('lo' if direction=='lower' else 'hi')]=bound
            record.update(certificate_verified=True,certified_support_bound=bound,verification=checked)
            if include_certificate:record['certificate']=cert
        out['directions'][name]=record
    if include_certificate:out['matrix']=matrix
    out['wall_s']=monotonic()-started
    return out


def apply_cargo_bound_windows(model,instance,bounds):
    """Attach verified algebraic cargo bounds and recompute phase KG windows.

    The root audit still decides whether the recorded coefficient enclosures
    are eligible for a physical proof; no infeasibility claim occurs here.
    """
    for name,record in bounds['directions'].items():
        if name[0]+('lo' if name.endswith('lower') else 'hi') in bounds['cargo_bounds'] and not record['certificate_verified']:
            raise ValueError('Unverified support bound')
    model.metadata['cargo_global_bounds']=dict(bounds['cargo_bounds'])
    phases=voyage_contexts(instance)
    for phase in phases:phase['bounds']=static_bounds(phase['context'])
    strict=analytic_windows(model,instance,phases)
    model.metadata['joint']['strict_windows']=strict
    model.metadata['joint']['cargo_support_bounds']={k:v for k,v in bounds.items() if k!='matrix'}
    for phase,win in strict.items():
        indices=model.metadata['joint']['phase_variables'][phase]
        for key,var in (('kg_m',indices['z_moment']),('lcg_m',indices['x_moment'])):
            lo,hi=win[key]
            model.add_row({var:1.,indices['mass']:-lo},lower=0.,name=phase+'_certified_'+key+'_lower')
            model.add_row({var:1.,indices['mass']:-hi},upper=0.,name=phase+'_certified_'+key+'_upper')
    return strict


def add_securing_rows(model,instance,provider,*,gm_lo=0.):
    """Necessary all-phase securing floor, evaluated independently of tanks."""
    boxes={b.id:b for b in instance.containers}
    # A global sum is safe even when shifted long boxes change bay end chains.
    columns=sum(bool(s.on_deck and s.cell_ids) for s in instance.vessel.stacks)
    row={}; capacity=None; cache={}; excluded=0
    for j,a in enumerate(model.allocations):
        cls=model.metadata['classes'][a['class_index']]
        deck=any(reg[1] for reg in a['signature'])
        records=[]
        for member in cls['members']:
            box=boxes[member]
            height=box.height_m+(box.oog.over_height_m if box.oog else 0.)
            z=a['support_z_lower_m']+.45*height
            key=(member,deck,z,gm_lo)
            if key not in cache:
                cache[key]=provider.securing_coefficients(instance,box,on_deck=deck,z_lower=z,gm_lower=gm_lo)
            records.append(cache[key])
        if any(min(r[key] for r in records)>1.+1.e-8 for key in ('own_fitting_ratio','own_mass_ratio')):
            model.variable_upper[j]=0.; excluded+=1
        if deck:
            row[j]=min(r['transverse_force_n']*r['per_column_share_factor'] for r in records)/MOMENT_SCALE
            capacity=min(r['deck_force_capacity_per_physical_column_n'] for r in records)
    if capacity is not None:
        model.add_row(row,upper=columns*capacity/MOMENT_SCALE,name='global_necessary_securing_force')
    model.metadata['joint']['securing']=dict(gm_lashing_floor_m=gm_lo,excluded_allocations=excluded,
                                           physical_columns=columns,capacity_per_column_n=capacity,
                                           strict_vs_guidance='necessary only; no stability GM substitution')
    return model
