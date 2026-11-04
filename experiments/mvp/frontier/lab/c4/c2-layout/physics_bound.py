"""Outward necessary physics rows for the full supporting-stack relaxation.

No local GM fit or finite-angle curve sample is a global bound.  The copied
Cycle 3 envelopes keep stability GM distinct from lashing GM.
"""
from __future__ import annotations

from fractions import Fraction
import importlib.util
import math
from pathlib import Path

HERE = Path(__file__).resolve().parent
_spec = importlib.util.spec_from_file_location('c4_layout_physics_envelopes', HERE/'physics_envelopes.py')
envelopes = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(envelopes)


def floor(value):
    return math.floor(Fraction(value))


def ceil(value):
    return math.ceil(Fraction(value))


def rounded_tank_row(coefficients, capacities):
    """u=ceil(actual kg): floor(c)*u <= c*m+max(0,floor(c))."""
    cs = [floor(Fraction(c)) for c in coefficients]
    return cs, [ceil(Fraction(v)) for v in capacities], sum(max(0, c) for c in cs)


def tier_support_floor(T, cells, minimum_height):
    """No-gap support: kth physical tier has k ordinary-height predecessors."""
    exact = min(Fraction(float(T.stack_base_z[T.cell_stack[c]]))
                + int(T.cell_k[c])*Fraction(float(minimum_height)) for c in cells)
    return envelopes._down(exact)


def attach(model, instance, T, allocations):
    """Each allocation is (CP count, member IDs, physical footprint signature).

    Tier positions are relaxed.  Base-height CG floors remain valid for all
    support heights. Securing uses the limit GM_lashing -> 0+, never a fitted
    or stability GM floor. Returns JSON metadata, never a legal-plan claim.
    """
    from stowage.checker.visibility import _fixed_loads
    p = envelopes.physical_bounds(instance)
    F = Fraction
    h = instance.vessel.hull
    boxes = {b.id: b for b in instance.containers}
    fixed = _fixed_loads(instance.vessel)
    fixed_mass = F(h.lightship_mass_kg)+sum((F(q.mass_kg) for q in fixed), F())
    fixed_z = F(h.lightship_mass_kg)*F(h.lightship_vcg_m)+sum(
        (F(q.mass_kg)*F(q.z_m) for q in fixed), F())
    cargo_mass = sum((F(boxes[bid].gross_mass_kg) for bid in instance.load_list), F())
    constant_mass = fixed_mass+cargo_mass
    # Ordinary hard weather applicability is permitted a 1e-10 comparison
    # residual. Give both draught and KG ratios an additional 1e-7 m guard.
    kg_upper = F(1.5)*F(p['draft_stability_upper_m']+1.e-7)+F(1.e-7)
    tank_coeff, tank_ub, rounding_guard = rounded_tank_row(
        [F(t['z_lower_m'])-kg_upper for t in p['tanks']],
        [t['capacity_kg'] for t in p['tanks']])
    tank_vars = [model.new_int_var(0, ub, f'physics_tank_kg_{k}')
                 for k, ub in enumerate(tank_ub)]
    mass_guard = max(1.e-5, 2.e-10*p['mass_upper_kg'])
    model.add(sum(tank_vars)+floor(constant_mass)
              <= ceil(F(p['mass_upper_kg']+mass_guard))+len(tank_vars))
    model.add(sum(tank_vars)+ceil(constant_mass)
              >= floor(F(p['mass_lower_kg']-mass_guard)))
    moment_terms, force_terms = [], []
    excluded = 0
    cache = {}
    for allocation in allocations:
        x, members, signature = allocation[:3]
        js = [j for j, count in signature for _ in range(count)]
        deck = any(T.stack_deck[j] for j in js)
        # Mixed hold/deck footprints are not real choices in current tables.
        if len({bool(T.stack_deck[j]) for j in js}) != 1:
            raise ValueError('A physical footprint crosses hold/deck')
        base_z = min(float(T.stack_base_z[j]) for j in js)
        if len(allocation) == 4:
            base_z = max(base_z, float(allocation[3]))
        records = []
        zcoeff = []
        for bid in members:
            box = boxes[bid]
            # Stability's point mass uses ordinary half height, whereas
            # lashing uses .45 of the expanded physical OOG height.
            zcoeff.append(F(box.gross_mass_kg)*(F(base_z)+F(box.height_m)/2))
            physical_height = box.height_m+(box.oog.over_height_m if box.oog else 0.)
            key = (bid, deck, base_z)
            if key not in cache:
                cache[key] = envelopes.securing_coefficients(instance, box,
                    on_deck=deck, z_lower=base_z+.45*physical_height, gm_lower=0.)
            records.append(cache[key])
        moment_terms.append(floor(min(zcoeff))*x)
        # Taking minima covers groups that forgot sub-kg mass/OOG details.
        if any(min(r[key] for r in records) > 1.+1.e-7 for key in
               ('own_fitting_ratio', 'own_mass_ratio')):
            model.add(x == 0)
            excluded += 1
        if deck:
            force = min(r['transverse_force_n']*r['per_column_share_factor'] for r in records)
            force_terms.append(floor(force)*x)
    rhs = ceil(kg_upper*constant_mass-fixed_z)+rounding_guard
    model.add(sum(moment_terms)+sum(c*u for c,u in zip(tank_coeff,tank_vars)) <= rhs)
    # A global sum is safe even for shifted long chains connecting odd bays.
    # Each physical deck column has at most two end-chain roots, each with
    # transverse capacity 2*(.85*210000), hence 714000 N per column.
    columns = sum(bool(T.stack_deck[j]) and bool(T.stack_cells[j]) for j in range(len(T.stack_ids)))
    model.add(sum(force_terms) <= ceil(columns*714000.*(1.+1.e-7)))
    return dict(scope='departure necessary outward rows; full free-trim righting area relaxed',
        tank_integer_rounding='ceil kg; each upper row widened for positive coefficients',
        tank_variables=len(tank_vars), weather_physical_kg_upper_m=float(kg_upper),
        global_deck_transverse_force_capacity_n=columns*714000.,
        allocations_excluded_by_necessary_own_securing=excluded,
        lashing_gm_lower_m=0., area_envelope='not used: no tight certified finite-angle envelope',
        mass_guard_kg=mass_guard)
