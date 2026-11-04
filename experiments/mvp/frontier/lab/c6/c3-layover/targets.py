"""Moment guidance derived from this map's true input ship; no stored result reads.

Native equilibrium and cargo geometry are read-only. Targets are heuristic inward
search preferences, never a checker substitute or an infeasibility certificate.
"""
from __future__ import annotations
from dataclasses import dataclass
import math
import numpy as np
from stowage.checker import lashing
from stowage.checker.visibility import _loading_condition, _cargo_geometry


@dataclass
class Targets:
    background_mass_t: float
    background_moments_tm: tuple
    cargo_mass_t: float
    departure_mass_t: float
    arrival_gm_m: float
    arrival_fsc_m: float
    arrival_draught_m: float
    arrival_cb: float
    departure_draught_m: float
    departure_lcb_m: float
    departure_km_m: float
    diagnostics: dict

    def settings(self, *, gm=None, shift_m=0., lcg_halfwidth_m=.5,
                 tcg_halfwidth_m=.02, vertical_scale=1.):
        """Settings kwargs for ordinary gap._moments, with background subtracted once."""
        gm = self.arrival_gm_m if gm is None else float(gm)
        x = self.departure_lcb_m+float(shift_m)
        max_kg = self.departure_km_m-self.arrival_fsc_m-gm
        return dict(background_mass_t=self.background_mass_t,
                    background_moments_tm=self.background_moments_tm,
                    lcg=(x-lcg_halfwidth_m, x+lcg_halfwidth_m),
                    tcg=float(tcg_halfwidth_m),
                    vertical_max_tm=self.departure_mass_t*max_kg*float(vertical_scale))

    def reference(self, hull, *, gm=None):
        """Arrival draught/block coefficient; true arrival GM unless outer loop replaces it."""
        value = self.arrival_gm_m if gm is None else float(gm)
        return lashing._motions(hull, self.arrival_draught_m, value, self.arrival_cb)


def level_draught(geometry, mass_kg, density, *, iterations=32):
    """Count-bounded native even-keel inverse, including actual port water density."""
    if not (math.isfinite(mass_kg) and mass_kg > 0. and density > 0.):
        raise ValueError('positive finite displacement and water density required')
    lo, hi = 0., float(geometry.depth_m)
    upper = geometry.submerged(hi, hi, 0., density)
    if upper.displacement_kg < mass_kg:
        raise ValueError('departure mass exceeds closed hull volume; no level-draft target')
    for _ in range(int(iterations)):
        mid = (lo+hi)/2
        point = geometry.submerged(mid, mid, 0., density)
        if point.displacement_kg < mass_kg:
            lo = mid
        else:
            hi = mid
    draft = (lo+hi)/2
    return draft, geometry.submerged(draft, draft, 0., density)


def derive(instance, model=None, *, departure_box_ids=None, bisection_iterations=32):
    """Use ORIGINAL instance/onboard even when model was built from a discharge/restow branch.

    All original decision cargo is subtracted from true arrival moments; modeled
    departure cargo (retained+new, excluding due) then enters gap variables. Fixed
    cargo outside the decision catalogs remains in the background exactly once.
    """
    ship = instance.onboard
    c = _loading_condition(instance, ship)
    density = float(instance.port.water_density_kg_m3)
    arrival_sub = c.geometry.submerged(c.t_aft_m, c.t_fwd_m, c.heel_rad, density)
    arrival_km = float(arrival_sub.vcb_m+arrival_sub.bm_t_m)
    fsc = arrival_km-float(c.kg_m)-float(c.gm_m)
    # Native cargo geometry uses actual support heights, plus asymmetric OOG
    # offsets for cargo CG. Match _loading_condition exactly when subtracting.
    input_ids = {p.container_id for p in ship.placements}
    cargo_mass, cargo_moment = 0., np.zeros(3)
    for placement, box, x, y, bottom, top in _cargo_geometry(instance, ship):
        if placement.container_id not in input_ids:
            continue
        if box.oog:
            x += (box.oog.over_length_fore_m-box.oog.over_length_aft_m)/2
            y += (box.oog.over_width_port_m-box.oog.over_width_starboard_m)/2
        mass = float(box.gross_mass_kg)
        cargo_mass += mass
        cargo_moment += mass*np.array([x, y, (bottom+top)/2])
    bg_mass = float(c.mass_kg)-cargo_mass
    bg_moments = float(c.mass_kg)*np.array([c.lcg_m, c.tcg_m, c.kg_m])-cargo_moment
    boxes = {b.id: b for b in instance.containers}
    if departure_box_ids is None:
        if model is not None:
            departure_box_ids = [model.T.box_ids[b] for b in model.boxes]
        else:
            departure_box_ids = ((input_ids | set(instance.load_list))-set(instance.discharge_list))
    ids = set(departure_box_ids)
    cargo_departure_mass = math.fsum(boxes[b].gross_mass_kg for b in ids)
    total = bg_mass+cargo_departure_mass
    draft, dep = level_draught(c.geometry, total, density, iterations=bisection_iterations)
    mean_draft = (float(c.t_aft_m)+float(c.t_fwd_m))/2
    # Native IRS securing defines CB at SCANTLING, not at current draft.
    hull = instance.vessel.hull
    scantling = float(hull.scantling_draught_m)
    scantling_volume = c.geometry.submerged(scantling, scantling).volume_m3
    cb = float(scantling_volume)/(float(hull.lpp_m)*float(hull.beam_m)*scantling)
    if not 0. < cb <= 1.:
        raise ValueError('true arrival block coefficient is nonphysical')
    diagnostics = dict(source='native true input equilibrium and physical cargo geometry',
                       original_arrival_mass_kg=float(c.mass_kg),
                       original_arrival_cg_m=[float(c.lcg_m), float(c.tcg_m), float(c.kg_m)],
                       original_arrival_drafts_m=[float(c.t_aft_m), float(c.t_fwd_m)],
                       original_arrival_gm_m=float(c.gm_m), original_arrival_km_m=arrival_km,
                       original_arrival_fsc_m=fsc, original_arrival_cb=cb,
                       securing_cb_basis='native scantling hull volume',
                       scantling_draught_m=scantling, scantling_volume_m3=float(scantling_volume),
                       removed_arrival_cargo_mass_kg=cargo_mass,
                       removed_arrival_cargo_moments_kgm=cargo_moment.tolist(),
                       background_mass_kg=bg_mass, background_moments_kgm=bg_moments.tolist(),
                       departure_cargo_mass_kg=cargo_departure_mass, departure_box_count=len(ids),
                       estimated_departure_mass_kg=total, estimated_departure_level_draught_m=draft,
                       estimated_departure_lcb_m=float(dep.lcb_m),
                       estimated_departure_km_m=float(dep.vcb_m+dep.bm_t_m),
                       water_density_kg_m3=density, bisection_iterations=int(bisection_iterations),
                       outward=False, physical_infeasibility_proven=False)
    return Targets(bg_mass/1000., tuple(map(float, bg_moments/1000.)), cargo_departure_mass/1000.,
                   total/1000., float(c.gm_m), fsc, mean_draft, cb, draft, float(dep.lcb_m),
                   float(dep.vcb_m+dep.bm_t_m), diagnostics)


def derive_from_plan(instance, plan, *, bisection_iterations=32):
    """Refresh guidance from a recipe produced in THIS cold invocation.

    This is an alternating-search target, not a fresh arrival condition. Parent
    keeps true original-arrival metadata/GM separately and carries this recipe
    into the next raw candidate before checking it. Departure decision cargo is
    subtracted from its actual physical geometry, leaving its updated liquids.
    """
    from stowage.checker.full import departure_condition
    ship = departure_condition(instance, plan)
    ids = [p.container_id for p in ship.placements]
    working = instance.model_copy(update={'onboard': ship, 'load_list': [], 'discharge_list': []})
    target = derive(working, departure_box_ids=ids, bisection_iterations=bisection_iterations)
    target.diagnostics.update(source='native current-invocation departure recipe refresh',
                              target_refresh_only=True, original_arrival_metadata_preserved_by_caller=True)
    return target
