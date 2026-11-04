"""Securing at fixed motion as linear rows in tier masses and tier occupancy.

Copied in form from the deck branch of `_check_lashing` in src/stowage/checker/lashing.py (read only). For a stack of
N nominal tiers every load the checker computes is linear in (mass_k, occupied_k): `_end_response` is a linear solve
with a constant matrix, and moments are sums of forces times fixed levers. An empty upper tier is mass 0 and wind 0,
so one row set serves every stack height from 2 to N. A single box anchors its rod at tier 1, not 2; the exact check
in `chainlab` covers that case and those rows are only ever pessimistic there.
"""
import numpy as np

from stowage.checker import lashing

HEIGHT = 2.591
CG = .45
TENSION = .85*lashing.LOCK_TENSION_SWL     # .85*250 kN is tighter than the post's 250 kN
COMPRESSION = 848000.                      # post, tighter than the fitting's 850 kN
SHEAR = .85*lashing.LOCK_SHEAR_SWL


def _quantities(trans, wind_t, longs, wind_l, vertical, gravity, heights):
    """Every securing load of one chain, as {name: value}, signs kept so that each is linear."""
    n = len(trans)
    h = np.array(heights)
    f = np.full(n, CG)
    out = {}
    eq_l = f*np.array(longs)+.5*np.array(wind_l)
    eq_l[:-1] += (1-f[1:])*np.array(longs[1:])+.5*np.array(wind_l[1:])
    for i, v in enumerate(np.cumsum(eq_l[::-1])):
        out[('long_rack', i)] = v/75000
    for name, stiffness in (('stiff', 15.7e6), ('soft', 3.7e6)):
        rack, rod, co, si, anchor = lashing._end_response(list(trans), list(wind_t), list(heights), stiffness,
                                                          cg_heights=list(CG*h))
        for i, v in enumerate(rack):
            out[('rack+', name, i)] = v/150000
            out[('rack-', name, i)] = -v/150000
        out[('rod', name)] = rod*max(co/150000, si/225000, 1/(.85*lashing.ROD_SWL))
        for i in range(n):
            hw = np.cumsum(np.r_[0., h[i:-1]])+CG*h[i:]
            hc = np.cumsum(np.r_[0., h[i:-1]])+.5*h[i:]
            moment = float(np.dot(trans[i:], hw)+np.dot(wind_t[i:], hc))
            down = rod*si if i <= anchor else 0.
            if i <= anchor:
                moment -= rod*co*float(h[i:anchor+1].sum())
            comp = float(np.sum(vertical[i:]))+down+moment/lashing.WIDTH
            tens = moment/lashing.WIDTH-float(np.sum(gravity[i:]))
            out[('tension', name, i)] = tens/TENSION
            out[('comp', name, i)] = comp/COMPRESSION
            out[('shear', name, i)] = ((sum(trans[i:])+sum(wind_t[i:]))/2)/SHEAR
    return out


def stack_rows(motions, x, y, base, tiers, heights=None):
    """Rows of one deck stack: (A_mass per kg, B_transverse-wind per N of table force, B_long per N, names).

    Each row reads  sum_k A[r,k]*mass_k + Bt[r,k]*wind_t_k + Bl[r,k]*wind_l_k  <=  1.
    """
    heights = list(heights if heights is not None else [HEIGHT]*tiers)
    z = base+np.r_[0., np.cumsum(heights[:-1])]+CG*np.array(heights)
    acc = [motions.at(x, y, float(q)) for q in z]
    zero = [0.]*tiers
    names, A, Bt, Bl = None, [], [], []

    def column(trans=zero, wind_t=zero, longs=zero, wind_l=zero, vertical=zero, gravity=zero):
        return _quantities(trans, wind_t, longs, wind_l, vertical, gravity, heights)
    for k in range(tiers):
        al, at, av = acc[k]
        unit = [0.]*tiers
        def only(value):
            out = list(unit)
            out[k] = value
            return out
        mass = column(trans=only(.5*at), longs=only(.5*al), vertical=only(.25*(lashing.G+av)), gravity=only(.25*lashing.G))
        wt = column(wind_t=only(.5))
        wl = column(wind_l=only(.5))
        names = names or list(mass)
        A.append([mass[n] for n in names])
        Bt.append([wt[n] for n in names])
        Bl.append([wl[n] for n in names])
    return np.array(A).T, np.array(Bt).T, np.array(Bl).T, names
