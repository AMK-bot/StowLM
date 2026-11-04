"""Checker-derived column moments, local hydrostatic rows, and full-check margins.

Moments use tonnes and metres; bending is the cargo contribution in N m.
These are construction surrogates, never replacement checker rules.
"""
from __future__ import annotations

from dataclasses import dataclass
import math

import numpy as np
from scipy.optimize import brentq
from stowage.conventions import cells_of_slot, parse_slot


@dataclass(frozen=True)
class LinearRow:
    name: str
    coefficients: np.ndarray
    lower: float = -math.inf
    upper: float = math.inf
    note: str = ''


class HydroFeatures:
    """Attach to a page Model. ``vector(pattern)`` is additive across patterns.

    ``rows`` returns LinearRow objects in the same feature coordinates. Bounds
    are at default tanks and full cargo mass, not at a rounded partial ship.
    """
    def __init__(self, model):
        self.model = model
        self.instance = model.p.instance
        self.cells = {c.id: c for c in self.instance.vessel.cells}
        self.boxes = {b.id: b for b in self.instance.containers}
        self.bays = sorted({parse_slot(c.id).bay for c in self.cells.values()})
        self.feature_names = ('displacement_t', 'lcg_tm', 'tcg_tm', 'vertical_tm',
                              'cargo_midship_bending_nm', *('bay_mass_t:'+str(b) for b in self.bays))
        cargo_t = sum(self.boxes[b].gross_mass_kg for b in self.instance.load_list)/1000.
        hull = self.instance.vessel.hull
        self.feature_scales = np.array([cargo_t, cargo_t*hull.lpp_m, cargo_t*hull.beam_m,
                                        cargo_t*hull.depth_m, cargo_t*1000.*9.80665*hull.lpp_m,
                                        *([cargo_t/max(1,len(self.bays))]*len(self.bays))])
        self._cache = {}
        self._rows = None
        self.notes = []

    def vector(self, column):
        cache_key = (column.page, tuple(column.boxes))
        if cache_key in self._cache:
            return self._cache[cache_key].copy()
        m = self.model
        page = m.pages[column.page]
        out = np.zeros(len(self.feature_names))
        # Physical support height accumulates actual occupied box heights.
        bottom = float(m.T.stack_base_z[page.stacks[0]])
        half_length = self.instance.vessel.hull.lpp_m/2.
        for tier, b in enumerate(column.boxes):
            box = self.boxes[m.T.box_ids[b]]
            ids = cells_of_slot(m.T.slot_ids[page.slots[tier]])
            mass = box.gross_mass_kg/1000.
            x = sum(self.cells[c].lcg_m for c in ids)/len(ids)
            y = sum(self.cells[c].tcg_m for c in ids)/len(ids)
            out[:4] += mass*np.array([1., x, y, bottom+box.height_m/2.])
            # strength.py splits each long-box mass among its supporting cells;
            # downward cargo force produces positive raw BM aft of each point.
            for cid in ids:
                share = mass/len(ids)
                out[4] += share*1000.*9.80665*max(half_length-self.cells[cid].lcg_m, 0.)
                out[5+self.bays.index(parse_slot(cid).bay)] += share
            bottom += box.height_m
        self._cache[cache_key] = out
        return out.copy()

    __call__ = vector

    def total(self, chosen):
        return sum((self.vector(self.model.columns[k]) for k in chosen), np.zeros(len(self.feature_names)))

    def fractional(self, x):
        return sum((value*self.vector(self.model.columns[k]) for k, value in x.items()),
                   np.zeros(len(self.feature_names)))

    def rows(self):
        """Linear hydrostatic boundary rows and unbounded preservation rows.

        At a fixed displaced mass, each trim/heel boundary is exactly a moment
        plane for a frozen liquid CG. Trim uses zero heel, heel uses zero trim;
        simultaneous attitudes and physical liquid migration remain nonlinear.
        GM uses even-keel KB+BM and is explicitly a local surrogate.
        """
        if self._rows is not None:
            return self._rows
        from stowage.checker.stability import _context, _loading_policy, _minimum_loading_gm
        from stowage.checker.stability_loading import loading_condition
        i = self.instance
        geometry, params = _context(i.vessel)
        bg = loading_condition(i, i.onboard, geometry=geometry, ship_params=params,
                               precomputed_soundings=True, small_tank_exemption=True)
        if bg.errors:
            raise ValueError('; '.join(bg.errors))
        mass = bg.mass_kg+sum(self.boxes[b].gross_mass_kg for b in i.load_list)
        rho = i.port.water_density_kg_m3
        trim_min, trim_max, mass_min, mass_max = _loading_policy(i)
        dim = len(self.feature_names)
        bgmom = bg.mass_kg/1000.*np.array([bg.lcg_m,bg.tcg_m,bg.kg_m])
        def immersion(trim=0., heel=0.):
            bound = geometry.max_z_m+geometry.beam_m*2
            q = brentq(lambda q: geometry.submerged(q+trim/2,q-trim/2,heel,rho).displacement_kg-mass,
                       -bound, bound, xtol=1e-8)
            ta, tf = q+trim/2, q-trim/2
            return ta, tf, geometry.submerged(ta,tf,heel,rho)
        rows = []
        coeff = np.zeros(dim); coeff[0] = 1.
        rows.append(LinearRow('displacement', coeff,
                             (mass_min-bg.mass_kg)/1000., (mass_max-bg.mass_kg)/1000.,
                             'Exact independent displacement policy at fixed tanks.'))
        # Port and hull draft limits impose a maximum even-keel displacement.
        max_draft = min(i.vessel.hull.max_draught_m,
                        i.port.max_draught_m if i.port.max_draught_m is not None else math.inf)
        draft_mass = geometry.submerged(max_draft,max_draft,0.,rho).displacement_kg
        rows.append(LinearRow('draft_even_keel', coeff.copy(), upper=(draft_mass-bg.mass_kg)/1000.,
                             note='Even-keel necessary construction proxy; actual draft checked at solved attitudes.'))
        for label, trim, is_lower in (('trim_max',trim_max,True), ('trim_min',trim_min,False)):
            ta, tf, submerged = immersion(trim=trim)
            _, axis, _ = geometry._axes(ta,tf,0.)
            coeff = np.zeros(dim); coeff[1:4] = axis
            rhs = mass/1000.*np.dot(submerged.centroid_m,axis)-np.dot(bgmom,axis)
            rows.append(LinearRow(label, coeff, lower=rhs if is_lower else -math.inf,
                                  upper=math.inf if is_lower else rhs,
                                  note='Zero-heel fixed-liquid boundary moment plane.'))
        for sign in (-1, 1):
            heel = sign*i.vessel.hull.max_list_rad
            ta, tf, submerged = immersion(heel=heel)
            _, _, axis = geometry._axes(ta,tf,heel)
            coeff = np.zeros(dim); coeff[1:4] = axis
            rhs = mass/1000.*np.dot(submerged.centroid_m,axis)-np.dot(bgmom,axis)
            rows.append(LinearRow('heel_'+str(sign),coeff,lower=rhs if sign<0 else -math.inf,
                                  upper=rhs if sign>0 else math.inf,
                                  note='Zero-trim fixed-liquid boundary moment plane.'))
        ta, tf, _ = immersion()
        upright = geometry.full_submerged(ta,tf,0.,rho)
        kg_max = upright.vcb_m+upright.bm_t_m-_minimum_loading_gm(i)-bg.fsm_kgm/mass
        coeff = np.zeros(dim); coeff[3] = 1.
        rows.append(LinearRow('gm_even_keel',coeff,upper=mass/1000.*kg_max-bgmom[2],
                              note='Even-keel tangent GM proxy at fixed mass and free surface.'))
        # Bending/bay rows receive LP target bands in rounding; no capacity is
        # fabricated because buoyancy and other loads determine legal BM.
        for k, name in enumerate(self.feature_names[4:],4):
            coeff = np.zeros(dim); coeff[k] = 1.
            rows.append(LinearRow(name,coeff,note='Preserve LP value; no invented legal capacity.'))
        self._rows = rows
        return rows

    def metadata(self):
        return dict(feature_names=list(self.feature_names),
                    rows=[dict(name=r.name, coefficients=r.coefficients.tolist(),
                               lower=r.lower if math.isfinite(r.lower) else None,
                               upper=r.upper if math.isfinite(r.upper) else None,
                               note=r.note) for r in self.rows()])


def checker_margins(report):
    """Minimum signed checker margins across every reported condition.

    Units stay separate. ``all`` retains each individual checker margin by part
    and key; family minima report the requested common margins. Unknown means
    None, never a fabricated pass. A failed/unavailable part is also listed.
    """
    values = {}
    def add(name, value):
        value = float(value)
        if not math.isnan(value):
            values[name] = min(values.get(name, math.inf),value)
    unavailable = []
    all_margins = {}
    for name, part in report.parts.items():
        if not part.ran:
            unavailable.append(dict(part=name,error=part.error))
        for key,value in part.margins.items():
            all_margins[name+'::'+key] = float(value)
            key = key.rsplit('.',1)[-1]
            if name.startswith('lashing') or ':lashing' in name:
                if key.startswith('max_'):
                    add('securing_ratio',value)
            if 'trim' in key and key in ('trim_m','trim_min_m'):
                add('trim_m',value)
            if key in ('draught_aft_m','draught_fwd_m','draught_fwd_min_m','draught_mean_m','port_draught_m'):
                add('draft_m',value)
            if key in ('displacement_minimum_kg','displacement_maximum_kg'):
                add('displacement_kg',value)
            if key.startswith('containership_'):
                if 'area_' in key:
                    add('second_check_area_mrad',value)
                elif key.endswith('_rad'):
                    add('second_check_angle_rad',value)
                else:
                    add('second_check_gz_m',value)
            if key == 'gm_m':
                add('gm_m',value)
            if key == 'list_rad':
                add('heel_rad',value)
            if key == 'blind_distance_m':
                add('sight_m',value)
            if key in ('total_blind_sector_rad','min_clear_sector_rad'):
                add('sight_rad',value)
            if key == 'bm_ratio':
                add('bending_ratio',value)
        # Visibility omits individual-sector margins on passing sectors; retain
        # exact negative violations when they exist. Do not mix metres/radians.
        for v in part.violations:
            if 'visibility' in v.rule_id and v.measured is not None and v.limit is not None:
                if 'clear angle' in v.message:
                    signed = v.measured-v.limit
                else:
                    signed = v.limit-v.measured
                add('sight_rad' if v.unit=='rad' else 'sight_m',signed)
            if 'securing' in v.rule_id and v.measured is not None and v.limit is not None and v.unit=='ratio':
                add('securing_ratio',v.limit-v.measured)
    families = ('displacement_kg','second_check_area_mrad','second_check_angle_rad','second_check_gz_m','securing_ratio','trim_m','sight_m','sight_rad','draft_m','gm_m','heel_rad','bending_ratio')
    return dict(passed=report.passed, shut_out=list(report.shut_out),
                margins={key:values.get(key) for key in families},
                rules=sorted({v.rule_id for v in report.violations}),
                unavailable=unavailable, all=all_margins)
