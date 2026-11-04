"""Per-stack sight roofs and a securing reference taken from the current stow.

SOLAS V/22: a point at height h above the sea, at horizontal distance r from the conning eye, hides sea surface beyond R when
h_eye*r + R*h > R*h_eye, with R the eye-to-bow distance plus min(2 LOA, 500 m) (the form in ``checker.visibility``). So a stack
whose whole roof stays under h_eye*(1 - r/R) hides nothing beyond R, and it adds no blind sector. That gives each stack its own
roof from its own position: tall near the bridge, low near the bow.

This is sufficient, not necessary: the checker allows small blind sectors, so a roof can be lower than the rule demands.
``margin`` lowers every roof, for later voyage conditions at other draughts. The roofs come from one loading condition.
"""
from __future__ import annotations

import math

import numpy as np

import stowage.checker.visibility as visibility

from lash_oracle import Fast


def roofs(instance, ship, *, margin=0.):
    """{stack index in the feasibility tables: highest allowed roof, metres above the keel datum}."""
    vessel, hull, bridge = instance.vessel, instance.vessel.hull, instance.vessel.bridge
    c = visibility._loading_condition(instance, ship)
    n = c.geometry.waterplane_normal(c.t_aft_m, c.t_fwd_m, c.heel_rad)
    along = np.array([1., 0., 0.])-n*n[0]
    along /= np.linalg.norm(along)
    across = np.cross(n, along)
    eye = np.array([bridge.x_m-visibility.CONNING_EYE_SETBACK_M, 0., bridge.eye_z_m])
    height = float(n@eye-n[2]*c.t_aft_m)
    bow_z = c.t_aft_m+(c.t_fwd_m-c.t_aft_m)*bridge.bow_x_m/hull.lpp_m
    bow = float(along@(np.array([bridge.bow_x_m, 0., bow_z])-eye))
    reach = bow+min(2*hull.loa_m, 500.)
    cells = {cell.id: cell for cell in vessel.cells}
    out = {}
    for stack in vessel.stacks:
        if not stack.on_deck:
            continue
        cell = cells[stack.cell_ids[0]]
        top = math.inf
        for x in (cell.lcg_m-3.029, cell.lcg_m+3.029):
            for y in (cell.tcg_m-1.219, cell.tcg_m+1.219):
                p = np.array([x, y, 0.])-eye
                forward = along@p
                if forward <= 0:
                    continue
                r = math.hypot(forward, across@p)
                allowed = height*(1-r/reach)
                # Height above the sea at this roof point is n@p + height, linear in z.
                top = min(top, (allowed-height-float(n@p))/float(n[2]))
        out[stack.id] = top-margin
    return out


class Reference:
    """Securing motions and sight roofs of one stow, so that each rung reads the ship it last built."""

    def __init__(self, instance, ship, tables, *, margin=0.):
        self.oracle = Fast(instance, ship)
        tops = roofs(instance, ship, margin=margin)
        self.roofs = {tables.stack_index[k]: v for k, v in tops.items()}
