"""Solve the actual liquid/hull equilibrium, including slowly moving liquids.

Fixed-point iteration is cheap for ordinary conditions, but can converge very
slowly near low GM. A coupled mass-centroid root then solves the same moment
balance, without accepting the residual of an unfinished iteration.
"""
import numpy as np
from scipy.optimize import root
from stowage.checker.stability_budget import check_budget


def floating_equilibrium(solver, condition, rho, length_m, liquid_cg):
    cg = np.array([condition.lcg_m, condition.tcg_m, condition.kg_m],dtype=float)
    def evaluate(candidate):
        check_budget('liquid/hull equilibrium')
        floating = solver.equilibrium(condition.mass_kg,*candidate,rho)
        actual = np.asarray(liquid_cg(condition,floating.heel_rad,
                                      np.arctan(floating.trim_m/length_m)))
        check_budget('liquid/hull equilibrium')
        return actual-candidate,floating
    for _ in range(6):
        residual,floating = evaluate(cg)
        if np.linalg.norm(residual) < 1e-7:
            return floating
        cg += residual
    solution = root(lambda value:evaluate(value)[0],cg,method='hybr',
                    options={'xtol':1e-9,'maxfev':80})
    residual,floating = evaluate(solution.x)
    if not np.all(np.isfinite(solution.x)) or np.linalg.norm(residual) >= 1e-7:
        raise ValueError('liquid/hull equilibrium root did not converge')
    return floating
