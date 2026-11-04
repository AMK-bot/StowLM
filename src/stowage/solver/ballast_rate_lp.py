"""D74's conservative LP seed, minimising the lower-bound pumped mass P.

An LP (linear program) minimises a linear objective while retaining every
linear safety row. Nonnegative intake/discharge variables represent the two
signs of each tank change; P bounds their separate totals. An internal
transfer therefore counts its water once. This is the exact D74 lower bound,
not a relaxation of the stability/strength model. Source: in-port DESIGN
Addendum B.5; scipy.optimize.linprog's bounded linear inequality formulation.
The full checker remains the authority for a returned seed.
"""
from __future__ import annotations

import math

import numpy as np


def solve_rate_ballast_lp(model, ship, instance, *, arrival_masses, heel_ids,
                          capacity_kg, symmetry_pairs=(), direction='conservative',
                          margin_scale=1.001):
    """Return the usual BallastLPResult, with objective P in kg.

    HEEL variables retain their arrival contents here: the finisher separately
    performs closed-form redistribution at fixed pair total. Every original
    row and local tank window remains in force. This helper does not activate
    semantics itself; only the activation-gated finisher calls it. D74 reports
    pumping beyond the available crane time as delay, so capacity_kg is retained
    for caller compatibility but does not cap P or constrain the tank masses.
    """
    from stowage.linear.model import BallastLPResult, linear_constraints
    from stowage.linear.operations import _highs, mass_vectors

    if not math.isfinite(capacity_kg) or capacity_kg < 0:
        raise ValueError('rate capacity_kg must be finite and nonnegative')
    cargo, _ = mass_vectors(model, ship, instance, ignore_adjustable=True)
    cs = linear_constraints(model, direction=direction, margin_scale=margin_scale)
    idx = np.flatnonzero(model.tank_adjustable)
    nonheel = [j for j, i in enumerate(idx) if model.tank_ids[i] not in heel_ids]
    t, q = len(idx), len(nonheel)
    size = t+2*q+1
    pcol = size-1
    rows, rhs, names = [], [], []

    def add(coeff, upper, name):
        rows.append(coeff)
        rhs.append(float(upper))
        names.append(name)

    fixed = cs.a_cell@cargo+cs.const
    for r, name in enumerate(cs.names):
        coeff = np.zeros(size)
        coeff[:t] = cs.a_tank[r, idx]
        if np.isfinite(cs.ub[r]):
            add(coeff, cs.ub[r]-fixed[r], name)
        if np.isfinite(cs.lb[r]):
            add(-coeff, fixed[r]-cs.lb[r], name)

    tank_columns = {model.tank_ids[i]: j for j, i in enumerate(idx)}
    for port, starboard in symmetry_pairs:
        if port in heel_ids or starboard in heel_ids:
            raise ValueError('HEEL tanks must not be included in symmetry_pairs')
        # APPROXIMATION: D64 searches ordinary pairs at equal tank masses.
        # Keeping this inside the LP preserves the physical source rows when
        # choosing the smallest-P symmetric seed.
        coeff = np.zeros(size)
        coeff[tank_columns[port]], coeff[tank_columns[starboard]] = 1., -1.
        add(coeff, 0., f'symmetry@{port}:{starboard}')
        add(-coeff, 0., f'symmetry@{port}:{starboard}')

    windows = getattr(model, '_tank_mass_bounds_kg', {})
    bounds = []
    for i in idx:
        key = model.tank_ids[i]
        arrival = float(arrival_masses[key])
        capacity = float(model.tank_capacity_kg[i])
        if not math.isfinite(arrival) or not 0 <= arrival <= capacity:
            raise ValueError(f'invalid arrival mass for {key}')
        lower, upper = windows.get(key, (0., capacity))
        lower, upper = max(0., lower), min(capacity, upper)
        if key in heel_ids:
            # Intersect, rather than replace, source local operating windows.
            lower, upper = max(lower, arrival), min(upper, arrival)
        if lower > upper:
            return BallastLPResult('infeasible', {}, None, float(lower-upper),
                                   (f'tank_mass@{key}',))
        bounds.append((lower, upper))
    bounds.extend([(0., None)]*(2*q))
    bounds.append((0., None))

    for a, j in enumerate(nonheel):
        key = model.tank_ids[idx[j]]
        arrival = float(arrival_masses[key])
        # m_dep - u <= m_arr; -m_dep - v <= -m_arr.
        coeff = np.zeros(size)
        coeff[j], coeff[t+a] = 1., -1.
        add(coeff, arrival, f'rate_intake@{key}')
        coeff = np.zeros(size)
        coeff[j], coeff[t+q+a] = -1., -1.
        add(coeff, -arrival, f'rate_discharge@{key}')
    for start, name in ((t, 'rate_intake_total'), (t+q, 'rate_discharge_total')):
        coeff = np.zeros(size)
        coeff[start:start+q] = 1.
        # P bounds the larger pumping total solely for the objective; slower
        # pumping adds reported delay rather than making the seed infeasible.
        coeff[pcol] = -1.
        add(coeff, 0., name)

    matrix, rhs = np.asarray(rows), np.asarray(rhs)
    scale = np.maximum(1., np.maximum(np.abs(rhs), np.max(np.abs(matrix), axis=1)))
    aa, bb = matrix/scale[:, None], rhs/scale
    cost = np.zeros(size)
    cost[pcol] = 1.
    answer = _highs(cost, aa, bb, bounds)
    if answer.status == 2:
        count = len(rhs)
        repair = _highs(np.r_[np.zeros(size), scale],
            np.column_stack((aa, -np.eye(count))), bb,
            bounds+[(0., None)]*count)
        if not repair.success:
            return BallastLPResult('error', {}, None, None, ())
        slack = repair.x[size:]*scale
        return BallastLPResult('infeasible', {}, None, float(slack.sum()),
            tuple(sorted(set(names[r] for r in np.flatnonzero(slack > 1e-6)))))
    if not answer.success:
        return BallastLPResult('error', {}, None, None, ())
    residual = rhs-matrix@answer.x
    binding = tuple(sorted(set(names[r] for r in
        np.flatnonzero(np.abs(residual) <= 1e-7*scale))))
    masses = {model.tank_ids[i]: float(max(0., answer.x[j])) for j, i in enumerate(idx)}
    return BallastLPResult('optimal', masses, float(answer.x[pcol]), None, binding)
