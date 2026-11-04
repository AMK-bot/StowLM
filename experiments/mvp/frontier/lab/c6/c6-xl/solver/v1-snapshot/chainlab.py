"""Exact per-chain securing terms at any motion, for diagnosis and search.

Deck branch copied from `_check_lashing` in src/stowage/checker/lashing.py (read only). It returns a dict of
ratio by key and the joint, for one bottom-up end chain, instead of folding them into one maximum. The hold branch
and `_longitudinal_reactions` stay with the checker; `reconcile` compares the two on a whole stow.
"""
import math

import numpy as np

from stowage.checker import lashing
from stowage.checker import visibility


def condition_terms(instance, ship):
    """Motions and the checker's own load graph for one ship state."""
    hull = instance.vessel.hull
    condition = visibility._loading_condition(instance, ship)
    draught = (condition.t_aft_m+condition.t_fwd_m)/2
    volume = condition.geometry.submerged(hull.scantling_draught_m, hull.scantling_draught_m).volume_m3
    cb = volume/(hull.lpp_m*hull.beam_m*hull.scantling_draught_m)
    return dict(gm=condition.gm_m, draught=draught, cb=cb, hull=hull)


def motions(terms, gm=None):
    return lashing._motions(terms['hull'], terms['draught'], terms['gm'] if gm is None else gm, terms['cb'])


def chain_ratios(chain, *, wind=True):
    """Worst ratio of each securing key on one deck end chain, over both end stiffnesses."""
    qs = [q for q, _ in chain]
    heights = [q.height for q in qs]
    cg = [q.cg_height for q in qs]
    shares = [.5+(-1 if end == 0 else 1)*q.dx/q.length for q, end in chain]
    trans = [abs(q.mass*q.at*f) for q, f in zip(qs, shares)]
    longs = [abs(q.mass*q.al*f) for q, f in zip(qs, shares)]
    wind_share = [.5 if getattr(q.container, 'background', False) else f for q, f in zip(qs, shares)]
    wt = [abs(q.wind_t*f) if wind else 0. for q, f in zip(qs, wind_share)]
    wl = [abs(q.wind_l*f) if wind else 0. for q, f in zip(qs, wind_share)]
    vertical = [q.mass*(lashing.G+q.av)*f/2 for q, f in zip(qs, shares)]
    gravity = [q.mass*lashing.G*f/2 for q, f in zip(qs, shares)]
    out = dict(racking=0., long_racking=0., post=0., tension=0., rod=0., fit_tension=0., fit_compression=0., fit_shear=0.)
    joints = {}

    def up(key, value, joint=None):
        if value > out[key]:
            out[key] = float(value)
            joints[key] = joint
    for stiffness in (15.7e6, 3.7e6):
        rack, rod, co, si, anchor = lashing._end_response(trans, wt, heights, stiffness, cg_heights=cg)
        up('racking', max(abs(rack))/150000, int(np.argmax(abs(rack))))
        f = np.array(cg)/np.array(heights)
        eq = f*np.array(longs)+.5*np.array(wl)
        eq[:-1] += (1-f[1:])*np.array(longs[1:])+.5*np.array(wl[1:])
        up('long_racking', max(np.cumsum(eq[::-1]))/75000)
        up('rod', max(rod*co/150000, rod*si/225000, rod/(.85*lashing.ROD_SWL)))
        for i in range(len(qs)):
            hw = np.cumsum([0.]+heights[i:-1])+np.array(cg[i:])
            hc = np.cumsum([0.]+heights[i:-1])+.5*np.array(heights[i:])
            moment = float(np.dot(trans[i:], hw)+np.dot(wt[i:], hc))
            down = rod*si if i <= anchor else 0.
            if i <= anchor:
                moment -= rod*co*sum(heights[i:anchor+1])
            static = sum(2*v*q.dy for v, q in zip(vertical[i:], qs[i:]))
            grav = sum(2*v*q.dy for v, q in zip(gravity[i:], qs[i:]))
            comp = sum(vertical[i:])+down+(abs(moment)+abs(static))/lashing.WIDTH
            tens = max(0., (abs(moment)+abs(grav))/lashing.WIDTH-sum(gravity[i:]))
            up('post', comp/848000, i)
            up('tension', tens/250000, i)
            up('fit_tension', tens/(.85*lashing.LOCK_TENSION_SWL), i)
            up('fit_compression', comp/(.85*lashing.LOCK_COMPRESSION_SWL), i)
            up('fit_shear', ((sum(trans[i:])+sum(wt[i:]))/2)/(.85*lashing.LOCK_SHEAR_SWL), i)
    return out, joints
