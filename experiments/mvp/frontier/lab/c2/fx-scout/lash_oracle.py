"""Exact per-column securing ratio: the checker's own lashing code, run on one pattern at a reference loading condition.

The checker's lashing loop works chain by chain, so a pattern's ratios depend on its boxes, its position and the ship's
motions. Motions come from the whole-ship condition, so we hold that fixed at a reference and run ``_check_lashing`` on a
ship holding one pattern. ``visibility._loading_condition`` is replaced only inside ``ratio``, then restored. No source changes.
APPROXIMATION: reference GM and draught come from an earlier stow. Wind has no shelter credit in the checker, so a lone stack
reads the same as in a full ship; this was confirmed against the full check on one stow.
"""
from __future__ import annotations

import stowage.checker.lashing as lashing
import stowage.checker.visibility as visibility
from stowage.model.ship import Placement

import math

import numpy as np

from stowage.conventions import cells_of_slot


KEYS = ('max_racking_ratio', 'max_longitudinal_racking_ratio', 'max_corner_post_ratio', 'max_corner_tension_ratio',
        'max_lashing_ratio', 'max_fitting_ratio', 'max_cell_guide_ratio')


class Oracle:
    def __init__(self, instance, ship):
        self.instance = instance
        self.empty = ship.model_copy(update={'placements': []})
        self.condition = visibility._loading_condition(instance, ship)
        self.cache = {}

    def ratio(self, assignment):
        """Largest securing ratio of the boxes in ``assignment`` ({box: slot}), alone on the ship."""
        key = tuple(sorted(assignment.items()))
        if key in self.cache:
            return self.cache[key]
        ship = self.empty.model_copy(update={'placements': [Placement(container_id=b, slot=s) for b, s in assignment.items()]})
        original = visibility._loading_condition
        visibility._loading_condition = lambda instance, ship: self.condition
        try:
            report = lashing._check_lashing(self.instance, ship, call_index=self.instance.call_index)
        finally:
            visibility._loading_condition = original
        value = max([report.metrics.get(k, 0.) for k in KEYS])
        self.cache[key] = value
        return value


class Fast:
    """Pure deck-column form of the same check, about 100 times faster, for use inside pricing.

    Copied from ``_check_lashing`` in src/stowage/checker/lashing.py: the deck branch of its chain loop, for an in-gauge box
    with its load centred, one end frame (both ends match then), and ``_longitudinal_reactions`` left out. The exact
    ``Oracle`` agrees to about 1 % on whole stows (see NOTES); the full checker still decides.
    """

    def __init__(self, instance, ship):
        hull = instance.vessel.hull
        condition = visibility._loading_condition(instance, ship)
        draught = (condition.t_aft_m+condition.t_fwd_m)/2
        volume_sc = condition.geometry.submerged(hull.scantling_draught_m, hull.scantling_draught_m).volume_m3
        cb = volume_sc/(hull.lpp_m*hull.beam_m*hull.scantling_draught_m)
        self.motions = lashing._motions(hull, draught, condition.gm_m, cb)
        self.cells = {c.id: c for c in instance.vessel.cells}
        self.boxes = {b.id: b for b in instance.containers}
        self.base = {s.id: s.base_z_m for s in instance.vessel.stacks}
        self.place = {}

    def _xy(self, slot):
        if slot not in self.place:
            cells = [self.cells[c] for c in cells_of_slot(slot)]
            self.place[slot] = (sum(c.lcg_m for c in cells)/len(cells), sum(c.tcg_m for c in cells)/len(cells),
                                self.base[cells[0].stack_id])
        return self.place[slot]

    def ratio(self, box_ids, slots):
        """Worst deck securing ratio of one bottom-up column."""
        boxes = [self.boxes[b] for b in box_ids]
        return self.ratio_of([b.gross_mass_kg for b in boxes], [b.height_m for b in boxes],
                             [b.length_ft for b in boxes], slots)

    def weights(self, length_ft, slots):
        """Fit ratio(m) = base + sum(a[k]*m[k]) around a typical full column, by finite differences (mass in kg)."""
        masses = [22000., 17000., 12000., 7000., 4000., 4000., 4000.][:len(slots)]
        heights, lengths = [2.896]*len(slots), [length_ft]*len(slots)
        base = self.ratio_of(masses, heights, lengths, slots)
        out = []
        for k in range(len(slots)):
            trial = list(masses)
            trial[k] += 2000.
            out.append(max(0., (self.ratio_of(trial, heights, lengths, slots)-base)/2000.))
        return base-sum(a*m for a, m in zip(out, masses)), out

    def weights_mixed(self, base, slots):
        """The same fit for a mixed column: 2*base paired 20-ft positions, then long boxes.

        A long box stands on the outer end frames of both 20-ft sides, so each side is its own chain. The ratio is the larger
        of the two, and a box on one side moves only its chain, so the two 20-ft weights are halved towards the average.
        """
        n = len(slots)
        mass = [22000., 22000., 17000., 17000.][:2*base]+[12000., 7000., 4000., 4000., 4000.][:n-2*base]
        heights = [2.591]*(2*base)+[2.896]*(n-2*base)

        def ratio(m):
            out = 0.
            for side in (0, 1):
                keep = [k for k in range(2*base) if k % 2 == side]+list(range(2*base, n))
                out = max(out, self.ratio_of([m[k] for k in keep], [heights[k] for k in keep],
                                             [20 if k < 2*base else 40 for k in keep], [slots[k] for k in keep]))
            return out
        start = ratio(mass)
        out = []
        for k in range(n):
            trial = list(mass)
            trial[k] += 2000.
            slope = max(0., (ratio(trial)-start)/2000.)
            out.append(slope*(.6 if k < 2*base else 1.))
        return start-sum(a*m for a, m in zip(out, mass)), out

    def ratio_of(self, gross, tall, lengths, slots):
        trans, longs, heights, cg, wind_t, wind_l, masses, av = [], [], [], [], [], [], [], []
        floor = None
        for gross_kg, height, length_ft, slot in zip(gross, tall, lengths, slots):
            x, y, base = self._xy(slot)
            floor = base if floor is None else floor
            length = lashing.LENGTH[length_ft]
            z = floor+.45*height
            al, at, a_v = self.motions.at(x, y, z)
            mass = max(gross_kg, 2500 if length_ft == 20 else 3500)
            trans.append(mass*at*.5); longs.append(mass*al*.5); heights.append(height); cg.append(.45*height)
            wind_t.append(max(15700 if length_ft == 20 else 31600, length*height*1000)*.5)
            wind_l.append(max(6400, lashing.WIDTH*height*1000)*.5)
            masses.append(mass); av.append(a_v)
            floor += height
        worst = 0.
        vertical = [m*(lashing.G+a)*.5/2 for m, a in zip(masses, av)]
        gravity = [m*lashing.G*.5/2 for m in masses]
        fractions = np.array(cg)/np.array(heights)
        for stiffness in (15.7e6, 3.7e6):
            rack, rod, co, si, anchor = lashing._end_response(trans, wind_t, heights, stiffness, cg_heights=cg)
            worst = max(worst, float(np.max(np.abs(rack)))/150000)
            equivalent = fractions*np.array(longs)+.5*np.array(wind_l)
            equivalent[:-1] += (1-fractions[1:])*np.array(longs[1:])+.5*np.array(wind_l[1:])
            worst = max(worst, float(np.max(np.cumsum(equivalent[::-1])))/75000)
            worst = max(worst, rod*co/150000, rod*si/225000, rod/(.85*lashing.ROD_SWL))
            for i in range(len(masses)):
                hw = np.cumsum([0.]+heights[i:-1])+np.array(cg[i:])
                hc = np.cumsum([0.]+heights[i:-1])+.5*np.array(heights[i:])
                moment = float(np.dot(trans[i:], hw)+np.dot(wind_t[i:], hc))
                rod_down = rod*si if i <= anchor else 0.
                if i <= anchor:
                    moment -= rod*co*sum(heights[i:anchor+1])
                comp = sum(vertical[i:])+rod_down+abs(moment)/lashing.WIDTH
                tens = max(0., abs(moment)/lashing.WIDTH-sum(gravity[i:]))
                shear = (sum(trans[i:])+sum(wind_t[i:]))/2
                worst = max(worst, comp/848000, tens/250000, tens/(.85*lashing.LOCK_TENSION_SWL),
                            comp/(.85*lashing.LOCK_COMPRESSION_SWL), shear/(.85*lashing.LOCK_SHEAR_SWL))
        return worst
