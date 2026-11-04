"""Find low-free-surface ballast basins before taking local LP steps.

Pressing symmetric port/starboard tanks full adds low weight without leaving
many slack liquid surfaces. Each such discrete base is trimmed by changing one
forward or aft peak group, using a bracketed one-dimensional root search on the
actual finite-liquid floating equilibrium. A heeling pair can then correct list.
Only the caller's complete exact checks may accept a candidate; failure of this
small deterministic search is never an infeasibility proof.

The free-surface motivation is IMO MSC.267(85), Part B 3.1. Root bracketing is
bisection with safeguarded secant steps (Brent, Algorithms for Minimization
Without Derivatives, 1973). The selected tank subsets are a search heuristic,
not an approximation to hull equilibrium, liquid transfer, or checker limits.
"""
from __future__ import annotations

from time import monotonic

import numpy as np

from stowage.solver.ballast_search import Deadline
from stowage.solver.ballast_deadline import exact_budget


def _equilibrium(context, fill):
    """Use the identical finite-liquid fixed point as ExactContext.margins."""
    return context.equilibrium(fill)


def _groups(tanks):
    """Match mirrored full-tank geometry, with names only for tank purpose."""
    rows = [tank.table[-1] for tank in tanks]
    pairs, used = [], set()
    for i, row in enumerate(rows):
        if i in used:
            continue
        matches = [j for j in range(i+1, len(rows)) if j not in used
            and row.tcg_m*rows[j].tcg_m < 0.
            and np.allclose([row.lcg_m, row.tcg_m, row.vcg_m, tanks[i].capacity_m3],
                            [rows[j].lcg_m, -rows[j].tcg_m, rows[j].vcg_m,
                             tanks[j].capacity_m3], rtol=1e-5, atol=1e-4)]
        if matches:
            j = matches[0]
            pairs.append((i, j))
            used.update((i, j))
        elif abs(row.tcg_m) < 1e-5:
            pairs.append((i,))
            used.add(i)
    # Recognised purpose tags are common tank roles, not ship-specific IDs.
    peak = [group for group in pairs if all(any(tag in tanks[i].id.upper()
            for tag in ('FPT', 'APT', 'PEAK', 'FWD-DEEP')) for i in group)]
    if not peak and len(pairs) >= 3:
        # Geometry-only fallback: the most forward/aft mirrored groups.
        ordered = sorted(pairs, key=lambda group: np.mean([rows[i].lcg_m for i in group]))
        peak = [ordered[0], ordered[-1]]
    ordinary = [group for group in pairs if group not in peak]
    def tagged(tags):
        return [i for group in ordinary for i in group
                if any(tanks[i].id.upper().startswith(tag) for tag in tags)]
    db, heel, wings = tagged(('DB',)), tagged(('HEEL',)), tagged(('WING',))
    forward_wings = [i for i in wings if rows[i].lcg_m >= np.median([r.lcg_m for r in rows])]
    bases = [db+heel, db+forward_wings, db+wings, db+wings+heel]
    if not db:
        ordered = sorted(ordinary, key=lambda group: np.mean([rows[i].vcg_m for i in group]))
        bases = [[i for group in ordered[:n] for i in group]
                 for n in sorted({max(1, len(ordered)//2), len(ordered)})]
    heeling = [group for group in ordinary if len(group) == 2 and
               any(tanks[i].id.upper().startswith('HEEL') for i in group)]
    if not heeling:
        heeling = sorted([group for group in ordinary if len(group) == 2],
            key=lambda group: -abs(rows[group[0]].tcg_m))[:1]
    # Light conditions often have adequate GM already: one or two pressed
    # pairs can correct their trim without exceeding the available draught.
    db_pairs = [group for group in ordinary if all(i in db for i in group)]
    sparse = [[], heel, *[list(group) for group in db_pairs]]
    sparse.extend([*left, *right] for index, left in enumerate(db_pairs)
                  for right in db_pairs[index+1:])
    return sparse, bases, peak, heeling


def search(tanks, initial, evaluate, context, deadline):
    """Return useful fills; ``evaluate`` alone accepts, by raising ``Found``.

    Deadline and Found exceptions propagate. All fills remain in [0, 1]. Tank
    order must equal ExactContext order. No random choices or witness inputs
    are used. Numeric failures skip a basin without claiming impossibility.
    """
    if context is None or not tanks:
        return []
    initial = np.asarray(initial, dtype=float)
    if initial.shape != (len(tanks),) or not np.isfinite(initial).all():
        raise ValueError('pressed ballast starts must match the finite tank vector')
    sparse, heavy, peaks, heeling = _groups(tanks)
    cache, examined, candidates = {}, {}, []

    def checkpoint():
        if monotonic() >= deadline:
            raise Deadline

    def floating(fill):
        checkpoint()
        key = fill.tobytes()
        if key not in cache:
            cache[key] = _equilibrium(context, fill)
        checkpoint()
        return cache[key]

    def complete(fill):
        checkpoint()
        identity = fill.tobytes()
        if identity in examined:
            return examined[identity]
        margins = evaluate(fill)
        examined[identity] = margins
        score = sum(min(float(value), 0.)**2 for value in margins.values())
        candidates.append((score, fill.copy()))
        checkpoint()
        return margins

    def root(base, direction, quantity, tolerance):
        """Safeguarded secant; charge actual slack FSM at every trial."""
        lo, hi = 0., 1.
        left, right = floating(base), floating(np.clip(base+direction, 0., 1.))
        a, b = getattr(left, quantity), getattr(right, quantity)
        best, value = (base.copy(), a) if abs(a) <= abs(b) else (np.clip(base+direction, 0., 1.), b)
        if a*b > 0.:
            return best
        for _ in range(7):
            checkpoint()
            if abs(value) <= tolerance:
                break
            fraction = (lo*b-hi*a)/(b-a) if abs(b-a) > 1e-12 else (lo+hi)/2
            fraction = float(np.clip(fraction, lo+.1*(hi-lo), hi-.1*(hi-lo)))
            trial = np.clip(base+fraction*direction, 0., 1.)
            measured = getattr(floating(trial), quantity)
            if abs(measured) < abs(value):
                best, value = trial, measured
            if measured*a <= 0.:
                hi, b = fraction, measured
            else:
                lo, a = fraction, measured
        return best

    # Available even-keel displacement is only an ordering heuristic. It is
    # neither a loading margin nor proof: real candidates still solve trim,
    # heel, liquid transfer and every complete checker part independently.
    heavy_first = False
    try:
        checkpoint()
        with exact_budget(deadline):
            hull = context.instance.vessel.hull
            draft = hull.max_draught_m
            port_draft = context.instance.port.max_draught_m
            if port_draft is not None:
                draft = min(draft, port_draft)
            displacement = context.direct_geometry.full_submerged(
                draft, draft, 0., context.rho).displacement_kg
            remaining = displacement-context.condition(initial).mass_kg
            capacities = [tank.capacity_m3*tank.content_density_kg_m3 for tank in tanks]
            heavy_mass = sum(capacities[i] for i in set(heavy[0])) if heavy else float('inf')
            heavy_first = remaining >= heavy_mass > 0.
        checkpoint()
    except (AttributeError, ValueError, ArithmeticError, TypeError):
        pass
    def make_starts(groups):
        result = []
        for indices in groups:
            checkpoint()
            base = np.zeros(len(tanks))
            base[indices] = 1.
            result.append(base)
        return result
    light_starts = [np.clip(initial, 0., 1.), *make_starts(sparse)]
    heavy_starts = make_starts(heavy)
    starts = heavy_starts+light_starts if heavy_first else light_starts+heavy_starts
    seen = set()
    for base in starts:
        checkpoint()
        if base.tobytes() in seen:
            continue
        seen.add(base.tobytes())
        try:
            margins = complete(base)
            if margins.get('stability/gm_m', 0.) < 0.:
                # A nonpositive-stiffness equilibrium may be unavailable or
                # expensive. Keep this seed for the LP but do not root it.
                continue
            eq = floating(base)
            # Correct the end whose lever opposes the measured signed trim.
            ordered_peaks = sorted(peaks, key=lambda group: np.mean(
                [tanks[i].table[-1].lcg_m for i in group]), reverse=eq.trim_m > 0.)
            # A forward deep tank can be the inboard continuation of a peak.
            # Try its extreme peak, cascaded continuation and uniform fill.
            forward = [group for group in ordered_peaks if all(
                any(tag in tanks[i].id.upper() for tag in ('FPT', 'FWD-DEEP'))
                for i in group)]
            peak_options = [(base, group) for group in ordered_peaks[:1]]
            if eq.trim_m > 0. and len(forward) > 1:
                # Use the longest lever first. Once it is pressed full, root
                # the next group; also retain the uniform-fill alternative.
                cascaded = base.copy()
                for index, group in enumerate(forward):
                    checkpoint()
                    if index:
                        peak_options.append((cascaded.copy(), group))
                    cascaded[list(group)] = 1.
                peak_options.append((base, tuple(i for group in forward for i in group)))
            for trim_base, group in peak_options:
                checkpoint()
                direction = np.zeros(len(tanks))
                direction[list(group)] = 1.-trim_base[list(group)]
                trimmed = root(trim_base, direction, 'trim_m', .025)
                trimmed_margins = complete(trimmed)
                list_clearance = trimmed_margins.get('stability/list_rad')
                other_list_dependent_failure = any(value < 0. and
                    (key.startswith('visibility/') or key.startswith('lashing/'))
                    for key, value in trimmed_margins.items())
                if (list_clearance is not None and list_clearance >= 0. and
                        not other_list_dependent_failure):
                    continue
                if abs(floating(trimmed).heel_rad) <= 1e-5:
                    continue
                for pair in heeling[:1]:
                    checkpoint()
                    # Exchange equal MASS, with both actual tank surfaces
                    # recomputed. Max exchange ends when one tank hits a bound.
                    cap = np.array([t.capacity_m3*t.content_density_kg_m3 for t in tanks])
                    i, j = pair
                    if tanks[i].table[-1].tcg_m < tanks[j].table[-1].tcg_m:
                        i, j = j, i
                    sign = -1. if floating(trimmed).heel_rad > 0. else 1.
                    source, dest = (i, j) if sign < 0. else (j, i)
                    transfer = min(trimmed[source]*cap[source], (1.-trimmed[dest])*cap[dest])
                    if transfer <= 0.:
                        # A pressed/empty pair cannot exchange. Remove weight
                        # from the heeled side, or add it to the opposite side.
                        direction = np.zeros(len(tanks))
                        if trimmed[source] > 0.:
                            direction[source] = -trimmed[source]
                        else:
                            direction[dest] = 1.-trimmed[dest]
                    else:
                        direction = np.zeros(len(tanks))
                        direction[source], direction[dest] = -transfer/cap[source], transfer/cap[dest]
                    if np.any(direction):
                        corrected = root(trimmed, direction, 'heel_rad', 1e-5)
                        complete(corrected)
        except (ValueError, ArithmeticError):
            continue
    candidates.sort(key=lambda item: item[0])
    return [fill for _, fill in candidates]
