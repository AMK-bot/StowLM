"""Meaningful source-matrix versus positive rod-envelope checks (small only)."""
from fractions import Fraction
import importlib.util
import math
from pathlib import Path

import numpy as np


spec = importlib.util.spec_from_file_location('feasproof_rod',Path(__file__).with_name('rod_bound.py'))
rod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(rod)


def _parameters(stiffness=3.7e6):
    from stowage.checker import lashing
    F = Fraction
    return dict(width=F(lashing.WIDTH),stiffness=F(stiffness),
                ea1=F(lashing._rod_stiffness(1.,1)),ea2=F(lashing._rod_stiffness(1.,2)))


def test_closed_chain_compliance_matches_source_matrix():
    from stowage.checker import lashing
    rng = np.random.default_rng(19620803)
    maximum_error = 0.
    for n in range(1,9):
        for stiffness in (3.7e6,15.7e6):
            for repeat in range(8):
                h = rng.uniform(2.,4.,n)
                fractions = rng.uniform(.05,.95,n)
                force = rng.uniform(0.,300000.,n)
                wind = rng.uniform(0.,50000.,n)
                _,source,co,si,anchor = lashing._end_response(force,wind,h,stiffness,cg_heights=fractions*h)
                a = anchor+1
                z = math.fsum(h[:a]); length = math.hypot(lashing.WIDTH,z)
                rk = lashing._rod_stiffness(length,a)
                equivalent = fractions*force+.5*wind
                equivalent[:-1] += (1-fractions[1:])*force[1:]+.5*wind[1:]
                K = rk*co/(stiffness+a*rk*co*co)
                closed = K*math.fsum(float(v)*min(j+1,a) for j,v in enumerate(equivalent))
                error = abs(source-closed)/max(1.,source,closed)
                maximum_error = max(maximum_error,error)
                assert error < 1.e-11
                assert math.isclose(si,z/length,rel_tol=1.e-12)
    assert maximum_error < 1.e-11


def test_endpoint_enclosures_cover_all_anchor_heights_and_components():
    from stowage.checker import lashing
    p = _parameters()
    rng = np.random.default_rng(19760101)
    for a in (1,2):
        for repeat in range(16):
            lo = rng.uniform(1.,4.)*a
            hi = lo+rng.uniform(.01,3.)*a
            for kind in ('resultant','horizontal','vertical'):
                floor = rod.coefficient_floor(anchor_count=a,z_lower=lo,z_upper=hi,parameters=p,kind=kind)
                for z in np.linspace(lo,hi,31):
                    length = math.hypot(lashing.WIDTH,z)
                    co,si = lashing.WIDTH/length,z/length
                    rk = lashing._rod_stiffness(length,a)
                    actual = rk*co/(3.7e6+a*rk*co*co)
                    actual *= dict(resultant=1.,horizontal=co,vertical=si)[kind]
                    assert floor <= actual


def test_tier_weighted_envelope_never_exceeds_source_rod():
    from stowage.checker import lashing
    rng = np.random.default_rng(19660101)
    p = _parameters()
    hmin,hmax = 2.,4.
    for n in range(1,9):
        for repeat in range(16):
            h = rng.uniform(hmin,hmax,n)
            force = rng.uniform(0.,300000.,n)
            wind = rng.uniform(0.,50000.,n)
            fractions = np.full(n,.45)
            _,source,co,si,_ = lashing._end_response(force,wind,h,3.7e6,cg_heights=fractions*h)
            for kind,component in (('resultant',1.),('horizontal',co),('vertical',si)):
                lower = []
                for j in range(n):
                    if j == 0:
                        cases = [(1,h[j],h[j]),(2,h[j]+hmin,h[j]+hmax)]
                        wt,ww = .45,.5
                    elif j == 1:
                        cases = [(2,h[j]+hmin,h[j]+hmax)]
                        wt,ww = 1.45,1.5
                    else:
                        cases = [(2,2*hmin,2*hmax)]
                        wt,ww = 2.,2.
                    K = min(rod.coefficient_floor(anchor_count=a,z_lower=lo,z_upper=hi,parameters=p,kind=kind)
                            for a,lo,hi in cases)
                    lower.append(K*(wt*force[j]+ww*wind[j]))
                assert math.fsum(lower) <= source*component
