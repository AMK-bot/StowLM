"""Continuous whole-map outward packing relaxation (never a legal-stow claim).

The real physical cells remain in the capacity accounting, but rows and stack
identities are forgotten after each real slot is mapped to a bay/tier footprint.
All coefficients use exact class masses/heights; only the support-plane height
is lowered. See packing_notes.md for the map from a legal stow to this LP.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass, field
import math
from time import monotonic
import warnings

import numpy as np
from scipy.optimize import Bounds, LinearConstraint, milp
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


def attach_rows(model, instance, *, gm_lo=0., gm_hi=None, physics_module=None):
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
