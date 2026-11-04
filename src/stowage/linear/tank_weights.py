"""Upright liquid-weight integrals, preserving a tank's actual quadrature.

Rectangular tanks have uniform longitudinal load. Hull-section tanks retain
their supplied wet-area times Gauss-weight point masses, as checker 3.2 does;
neighbouring samples may have gaps and must not become invented panels.
Sources: the production strength reference; Biran & López-Pulido (2014), ch. 4.
"""
import numpy as np


def unit_tank_weight_integrals(solid, volume, stations):
    """Cumulative unit mass and first lever (m), at an upright fixed fill."""
    stations=np.asarray(stations,dtype=float)
    if solid.is_rectangular:
        left,right=solid.x_min_m,solid.x_max_m
        length=np.clip(stations-left,0.,right-left)
        return length/(right-left),(length*(stations-left)-length**2/2)/(right-left)
    x,weights,areas=solid.section_liquid_areas(volume,0.,0.)
    masses=np.asarray(weights)*np.asarray(areas)
    total=float(masses.sum())
    if total<=0:
        raise ValueError('unit tank load requires positive liquid volume')
    masses=masses/total
    return ((stations[:,None]>=x)@masses,
            np.maximum(stations[:,None]-x,0.)@masses)
