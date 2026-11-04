"""D64 per-lift physics with worked-tank FSM and equilibrium continuation.

Continuation changes the initial guess only. The exact mass/moment equations,
liquid redistribution and immersion calculation remain those of the primitives.
A finite failed local root falls back to a cold solve. Nonfinite inputs,
iterates, liquid-CG residuals and returned roots fail internally without retry.
A margin within 0.01 m GM,
0.02 m draught/trim or 0.2 degrees list/immersion is cold re-solved with direct
geometry; these bands trigger work and never relax a physical limit. Warm and
cold reports promise equal verdicts, not identical floating-point metrics.
"""
from __future__ import annotations

from collections import OrderedDict, deque
from copy import deepcopy
from concurrent.futures import ProcessPoolExecutor
from dataclasses import dataclass
import math
import multiprocessing
import os
import re
from hashlib import sha256
from statistics import median
from time import monotonic

import numpy as np
from scipy.optimize import root

from stowage.checker.stability_batch import InPortBatchResult, _failed
from stowage.checker.stability_budget import budget, check_budget
from stowage.model import PartReport, Violation

RULE = 'port.stability.cargo_operations'


class _NonfiniteNumericalError(RuntimeError):
    """Invalid arithmetic is neither physical P1 nor a reason to retry a solve."""


def _finite(value, label):
    array = np.asarray(value, dtype=float)
    if not np.isfinite(array).all():
        raise _NonfiniteNumericalError(f'nonfinite {label}')
    return array


def _finite_norm(value, label):
    array = _finite(value, label)
    with np.errstate(over='ignore', invalid='ignore'):
        norm = float(np.linalg.norm(array))
    _finite(norm, f'{label} norm')
    return norm


def _checked_floating(floating):
    _finite([floating.t_aft_m, floating.t_fwd_m, floating.heel_rad,
             floating.residual_norm], 'floating equilibrium state')
    submerged = floating.submerged
    if submerged is not None:
        _finite([submerged.volume_m3, *submerged.centroid_m], 'floating submerged state')
    return floating


def _nonfinite_error(error):
    return isinstance(error, _NonfiniteNumericalError) or bool(re.search(
        r'\b(?:non[- ]?finite|not finite|nans?|inf(?:s|inite|inity|inities)?)\b',
        str(error), re.IGNORECASE))


def _checked_table_equilibrium(geometry, mass, lcg, tcg, kg, rho, *,
                               heel=None, initial_drafts=None, cg_callback=None):
    """TableGeometry's same equilibrium equations, with nonfinite retry guards.

    Only finite failed roots may take the primitive's bounded least-squares
    retry. Calling its original _condition would hide an invalid first root
    before the checked coupled-equilibrium boundary could observe it.
    """
    from stowage.checker import stability_tables as tables
    from stowage.vessel.hydrostatics import FloatingCondition

    _finite([mass, lcg, tcg, kg, rho], 'table equilibrium loading')
    if mass < geometry.qualified_min_mass_kg:
        raise tables.OutsideTable('mass below vessel lightship is outside qualified table domain')
    if mass > geometry.qualified_max_mass_kg:
        raise tables.OutsideTable('mass above upright scantling displacement is outside qualified table domain')
    if rho != geometry.density_kg_m3:
        raise tables.OutsideTable('different water density requires its own table')
    qlo, qhi = geometry.bounds['immersion_m']
    tlo, thi = geometry.bounds['trim_m']
    phimax = geometry.grid['heel_rad'][-1]
    _finite([qlo, qhi, tlo, thi, phimax], 'table equilibrium bounds')
    fixed = heel is not None
    if fixed:
        _finite(heel, 'table prescribed heel')
        if abs(heel) > phimax+1e-10:
            raise tables.OutsideTable('heel exceeds cached cross curves')
    q = np.clip(mass/rho/(geometry.lpp_m*geometry.beam_m*.7), qlo+1e-7, qhi-1e-7)
    seed = [q, 0.] if fixed else [q, 0., 0.]
    if initial_drafts is not None:
        ta, tf = _finite(initial_drafts, 'table equilibrium initial draughts')
        seed = [(ta+tf)/2*np.cos(heel), ta-tf]
    seed = _finite(seed, 'table equilibrium seed')

    def residual(value):
        value = _finite(value, 'table equilibrium trial/root')
        q, trim = value[:2]
        angle = heel if fixed else value[2]
        volume, mx, my, mz = _finite(geometry._values(q, trim, angle), 'table hull integrals')
        with np.errstate(over='ignore', invalid='ignore'):
            centroid = np.array([mx, my, mz])/max(volume, 1e-20)
            mean = q/np.cos(angle)
            ta, tf = mean+trim/2, mean-trim/2
            _finite([ta, tf, *centroid], 'table equilibrium attitude/centroid')
            _, along, transverse = geometry._axes(ta, tf, angle)
            _finite([along, transverse], 'table equilibrium axes')
            physical = (_finite(cg_callback(angle, np.arctan(trim/geometry.lpp_m)),
                'table equilibrium liquid CG') if cg_callback else [lcg, tcg, kg])
            difference = centroid-physical
            answer = [(volume-mass/rho)/(mass/rho),
                      np.dot(difference, along)/geometry.lpp_m]
            if not fixed:
                answer.append(np.dot(difference, transverse)/geometry.beam_m)
        _finite_norm(answer, 'table equilibrium residual')
        return answer

    try:
        solved = tables.root(residual, seed, tol=1e-9)
        _finite(solved.x, 'table equilibrium returned root')
        error = _finite_norm(residual(solved.x), 'table equilibrium residual')
    except tables.OutsideTable:
        error = None  # A finite out-of-domain trial can use bounded retry.
    if error is None or error > 2e-6:
        lower = [qlo, tlo] if fixed else [qlo, tlo, -phimax]
        upper = [qhi, thi] if fixed else [qhi, thi, phimax]
        seed = np.clip(seed, np.array(lower)+1e-9, np.array(upper)-1e-9)
        solved = tables.least_squares(residual, seed, bounds=(lower, upper), diff_step=1e-4,
            xtol=2e-10, ftol=2e-10, gtol=2e-10, max_nfev=100)
        _finite(solved.x, 'table equilibrium least-squares returned root')
        error = _finite_norm(residual(solved.x), 'table equilibrium residual')
    if error > 2e-6:
        raise tables.OutsideTable('no equilibrium inside cached table')
    q, trim = solved.x[:2]
    angle = heel if fixed else solved.x[2]
    mean = q/np.cos(angle)
    ta, tf = float(mean+trim/2), float(mean-trim/2)
    return _checked_floating(FloatingCondition(ta, tf, float(angle),
        geometry.submerged(ta, tf, angle, rho), error))


def _checked_equilibrium(geometry, *values):
    from stowage.checker.stability_tables import TableGeometry
    _finite(values, 'equilibrium loading/root')
    if isinstance(geometry, TableGeometry):
        return _checked_table_equilibrium(geometry, *values)
    return _checked_floating(geometry.equilibrium(*values))


def _checked_coupled_equilibrium(geometry, condition, rho, length):
    """Validate the primitive's coupled solver without changing its equations.

    The primitive evaluates the returned CG root through equilibrium before
    assigning its generic nonconvergence error. This local proxy catches an
    invalid root there; it also validates every liquid-CG residual and norm
    before the primitive can retry it or turn it into physical P1.
    """
    from stowage.checker.stability_equilibrium import floating_equilibrium
    from stowage.checker.stability_loading import liquid_cg

    class CheckedSolver:
        candidate = None

        def equilibrium(self, *args):
            _finite(args, 'coupled equilibrium input/root')
            self.candidate = np.asarray(args[1:4], dtype=float)
            return _checked_equilibrium(geometry, *args)

    solver = CheckedSolver()

    def checked_liquid_cg(condition, heel, trim):
        _finite([heel, trim], 'liquid-CG attitude')
        actual = _finite(liquid_cg(condition, heel, trim), 'liquid CG')
        with np.errstate(over='ignore', invalid='ignore'):
            residual = actual-solver.candidate
        _finite_norm(residual, 'coupled equilibrium residual')
        return actual

    _finite([condition.mass_kg, condition.lcg_m, condition.tcg_m,
             condition.kg_m, rho, length], 'coupled equilibrium loading')
    return floating_equilibrium(solver, condition, rho, length, checked_liquid_cg)


def _checked_direct_upright(geometry, mass, lcg, kg, rho, callback, initial_drafts):
    """The primitive's zero-heel free-trim solve, with checked retry boundaries.

    Preserve its immersion/trim coordinates, batched forward Jacobian, root and
    least-squares settings, and nested heave/trim bracket for finite failures.
    """
    from scipy.optimize import least_squares, brentq
    from stowage.vessel.hydrostatics import FloatingCondition

    guess = _finite(initial_drafts if initial_drafts is not None else
        [geometry.design_draught_m, geometry.design_draught_m], 'upright initial draughts')
    _finite([mass, lcg, kg, rho], 'upright loading')
    states, jacobians = {}, {}

    def drafts(value):
        q, trim = _finite(value, 'upright trial/root')
        return _finite([q+trim/2, q-trim/2], 'upright draughts')

    def remember(ta, tf, submerged):
        _finite([submerged.displacement_kg, *submerged.centroid_m], 'upright submerged state')
        _, along, _ = geometry._axes(ta, tf, 0.)
        _finite(along, 'upright longitudinal axis')
        cg = _finite(callback(0., np.arctan((ta-tf)/geometry.lpp_m)) if callback else
            [lcg, 0., kg], 'upright liquid CG')
        with np.errstate(over='ignore', invalid='ignore'):
            answer = [(submerged.displacement_kg-mass)/mass,
                np.dot(np.asarray(submerged.centroid_m)-cg, along)/geometry.lpp_m]
        _finite_norm(answer, 'upright residual')
        states[ta, tf] = answer, submerged
        return answer

    def residual(value):
        check_budget('checked upright free-trim residual')
        ta, tf = drafts(value)
        if (ta, tf) not in states:
            remember(ta, tf, geometry.submerged(ta, tf, 0., rho))
        return states[ta, tf][0]

    def jacobian(value):
        check_budget('checked upright free-trim finite differences')
        value = _finite(value, 'upright Jacobian trial')
        key = tuple(value)
        if key not in jacobians:
            step = 1e-4*np.abs(value)
            step[step == 0.] = 1e-4
            trials = np.repeat(value[None, :], 2, axis=0)
            trials[np.arange(2), np.arange(2)] += step
            pairs = [drafts(trial) for trial in trials]
            missing = list(dict.fromkeys(tuple(pair) for pair in pairs if tuple(pair) not in states))
            if missing:
                integrated = (geometry._submerged_many(missing, 0., rho)
                    if hasattr(geometry, '_submerged_many') else
                    [geometry.submerged(*pair, 0., rho) for pair in missing])
                for (ta, tf), submerged in zip(missing, integrated, strict=True):
                    remember(ta, tf, submerged)
            with np.errstate(over='ignore', invalid='ignore'):
                jacobians[key] = (np.asarray([states[tuple(pair)][0] for pair in pairs])-residual(value)).T/step
            _finite(jacobians[key], 'upright Jacobian')
        return jacobians[key]

    seed = _finite([np.mean(guess), guess[0]-guess[1]], 'upright seed')
    solved = root(residual, seed, jac=jacobian, tol=1e-8)
    _finite(solved.x, 'upright returned root')
    if _finite_norm(residual(solved.x), 'upright residual') > 2e-6:
        solved = least_squares(residual, seed, diff_step=1e-4,
            xtol=2e-10, ftol=2e-10, gtol=2e-10, max_nfev=100)
        _finite(solved.x, 'upright least-squares returned root')
    if _finite_norm(residual(solved.x), 'upright residual') > 2e-6:
        def at_trim(trim):
            _finite(trim, 'upright bracket trim')
            limit = geometry.max_z_m+2*geometry.beam_m+abs(trim)
            immersion = brentq(lambda q: residual([q, trim])[0], -limit, limit, xtol=1e-9)
            return _finite([immersion, trim], 'upright bracket immersion root')
        for extent in (.05, .2, .5, 1., 2., 10.):
            lo, hi = -geometry.lpp_m*extent, geometry.lpp_m*extent
            with np.errstate(over='ignore', invalid='ignore'):
                bracket_sign = residual(at_trim(lo))[1]*residual(at_trim(hi))[1]
            _finite(bracket_sign, 'upright bracket residual product')
            if bracket_sign <= 0:
                trim = brentq(lambda trim: residual(at_trim(trim))[1], lo, hi, xtol=1e-7)
                _finite(trim, 'upright bracket returned trim root')
                solved.x = at_trim(trim)
                break
    error = _finite_norm(residual(solved.x), 'upright residual')
    if error > 2e-6:
        raise ValueError('free-trim curve equilibrium did not converge at 0 degrees')
    ta, tf = drafts(solved.x)
    return _checked_floating(FloatingCondition(float(ta), float(tf), 0., states[ta, tf][1], error))


class _CheckedCriteriaGeometry:
    """Delegate all geometry except D64's single zero-heel upright solve."""

    def __init__(self, geometry):
        self._geometry = geometry

    def __getattr__(self, name):
        return getattr(self._geometry, name)

    def free_trim_curve(self, angles, mass, lcg, kg, density, cg_callback=None,
                        initial_drafts=None, stop_when=None):
        from stowage.checker.stability_tables import TableGeometry
        angles = _finite(angles, 'in-port upright angles')
        if angles.shape != (1,) or angles[0] != 0.:
            raise ValueError('D64 criteria adapter supports only the zero-heel upright state')
        geometry = self._geometry
        if isinstance(geometry, TableGeometry):
            floating = _checked_table_equilibrium(geometry, mass, lcg, 0., kg, density,
                heel=0., initial_drafts=initial_drafts, cg_callback=cg_callback)
        else:
            floating = _checked_direct_upright(geometry, mass, lcg, kg, density,
                                               cg_callback, initial_drafts)
        ta, tf = floating.t_aft_m, floating.t_fwd_m
        _, _, across = geometry._axes(ta, tf, 0.)
        lever = float(np.dot(np.asarray(floating.submerged.centroid_m)-[lcg, 0., kg], across))
        _finite(lever, 'upright righting lever')
        return np.asarray([lever]), np.asarray([[ta, tf]])


@dataclass(frozen=True)
class TimedInPortBatchResult(InPortBatchResult):
    """D64 A.4 state-solve measurements, kept outside deterministic reports.

    Durations align with returned reports in input order. They bracket one
    check_state call inside the evaluating process, including its cold retries
    but excluding worker startup, serialization, queues and parent waits.
    """

    state_times_s: tuple[float, ...] = ()

    @property
    def measured_state_count(self):
        return min(8, len(self.state_times_s))

    @property
    def measured_state_time_s(self):
        """Median of the first eight evaluated states; zero for an empty batch."""
        times = self.state_times_s[:8]
        return float(median(times)) if times else 0.


def near_limit(metrics, margins):
    from stowage.checker.stability import _near_table_limit
    return _near_table_limit(metrics, margins)


def equilibrium_nonconvergence(error):
    if getattr(error, '_in_port_numerical_failure', False) or _nonfinite_error(error):
        return False
    message = str(error).lower()
    return ('no converged floating equilibrium' in message
            or 'liquid/hull equilibrium root did not converge' in message
            or 'free-trim curve equilibrium did not converge' in message)


def draught_limits(instance, geometry, floating, mass):
    """P5a: density-adjusted amidships load line. P5b: low-side berth draught.

    APPROXIMATION: D64 uses summer FWA=displacement/(4*TPC) mm,
    interpolated linearly from seawater 1025 to freshwater 1000 kg/m3.
    Hydrostatic TPC is evaluated at summer, upright even keel, in seawater.
    """
    h = instance.vessel.hull
    rho = instance.port.water_density_kg_m3
    if not math.isfinite(rho) or rho <= 0:
        raise ValueError('positive finite port density required')
    allowance = 0.
    if rho != 1025.:
        summer = geometry.full_submerged(h.max_draught_m, h.max_draught_m, 0., 1025.)
        area = float(summer.waterplane_area_m2)
        if not math.isfinite(area) or area <= 0:
            raise ValueError('summer waterplane area unavailable for FWA')
        tpc_t_per_cm = 1025.*area/100_000.
        allowance = mass/1000./(4.*tpc_t_per_cm)/1000.*(1025.-rho)/25.
    mean = (floating.t_aft_m+floating.t_fwd_m)/2.
    limit = h.max_draught_m+allowance
    metrics = {'fresh_water_allowance_m': allowance, 'load_line_draught_limit_m': limit}
    margins = {'draught_mean_m': limit-mean}
    broken = []
    if margins['draught_mean_m'] < -1e-10:
        broken.append((mean, limit, 'm', 'draught_mean_m exceeds permissible cargo-operation limit'))
    if instance.port.max_draught_m is not None:
        draught = max(floating.t_aft_m, floating.t_fwd_m)+h.beam_m/2.*math.sin(abs(floating.heel_rad))
        metrics['berth_draught_m'] = draught
        margins['port_draught_m'] = instance.port.max_draught_m-draught
        if margins['port_draught_m'] < -1e-10:
            broken.append((draught, instance.port.max_draught_m, 'm',
                           'port_draught_m exceeds permissible cargo-operation limit'))
    return metrics, margins, broken


_COLD_PHYSICAL_CACHE_LIMIT = 256


def _array_fingerprint(array):
    values = np.asarray(array)
    return values.shape, values.dtype.str, sha256(values.tobytes()).digest()


def _cold_physical_key(geometry, condition, rho, length):
    """Exact physical identity for the fully grouped table-liquid path only.

    Direct liquid roots also own per-condition starting-level caches. Until
    those side effects are replayed, they cannot use this cache. Regulatory
    FSM is deliberately absent: actual liquids determine this equilibrium.
    Canonical source validation still runs before every state evaluation.
    """
    from stowage.checker.perf import reuse_enabled
    from stowage.checker import stability_equilibrium, stability_loading
    from stowage.checker import stability_tables, stability_liquid_batch
    if (not reuse_enabled() or condition.liquid_cg_groups is None or condition.cg_cache
            or not isinstance(geometry, stability_tables.TableGeometry)):
        return None
    try:
        generation = geometry.direct._edge_generation
        groups = tuple((bool(group.prismatic),
            _array_fingerprint(group.heels), _array_fingerprint(group.trims),
            _array_fingerprint(group.shift_grid)) for group in condition.liquid_cg_groups)
        hull = (id(geometry.direct), generation, geometry.density_kg_m3,
            geometry.qualified_min_mass_kg, geometry.qualified_max_mass_kg,
            tuple((name,_array_fingerprint(array)) for name,array in geometry.grid.items()),
            _array_fingerprint(geometry.values))
        tanks = tuple((tank.id,id(tank.geometry),tank.density_kg_m3,tank.volume_m3)
                      for tank in condition.actual_tanks)
        upright = tuple((key,row.model_dump_json())
                        for key,row in condition.upright_cache.items())
        providers = (_checked_coupled_equilibrium, _checked_equilibrium,
            _checked_table_equilibrium, stability_equilibrium.floating_equilibrium,
            stability_equilibrium.root, stability_loading.liquid_cg,
            stability_liquid_batch.evaluate, stability_liquid_batch._stencil,
            stability_tables.root, stability_tables.least_squares,
            geometry._values.__func__)
        return (hull, condition.mass_kg, condition.lcg_m, condition.tcg_m,
            condition.kg_m, rho, length, condition.precomputed_soundings,
            tanks, groups, tuple(condition.liquid_usage.items()), upright, providers)
    except (AttributeError,TypeError,ValueError):
        return None  # Custom providers retain the ordinary checked cold solve.


class _Continuation:
    def __init__(self):
        self.previous = None
        # Per evaluator/worker, bounded and discarded with its numerical state.
        # Entries retain geometry references, preventing object-id reuse.
        self._cold_physical = OrderedDict()

    def solve(self, geometry, condition, rho, length, method):
        from stowage.checker.stability_loading import liquid_cg
        from stowage.vessel.hydrostatics import FloatingCondition
        if self.previous is None:
            return self.cold(geometry, condition, rho, length, method)
        previous = self.previous
        _checked_floating(previous)
        _finite([condition.mass_kg, condition.lcg_m, condition.tcg_m,
                 condition.kg_m, condition.fsc_m, rho, length], 'continuation loading')
        physical_cg = ((lambda heel, trim: (condition.lcg_m, condition.tcg_m,
                    condition.kg_m+condition.fsc_m)) if method == 2 else
                    (lambda heel, trim: liquid_cg(condition, heel, trim)))
        def physical(heel, trim):
            _finite([heel, trim], 'continuation liquid-CG attitude')
            return _finite(physical_cg(heel, trim), 'continuation liquid CG')
        try:
            # Keep table integration where available, but own the warm root:
            # TableGeometry._condition retries nonfinite roots internally.
            # A scoped checked root prevents that invalid result being erased.
            submerged = (geometry.submerged if hasattr(geometry, '_condition') else
                         getattr(geometry, '_solver_submerged', geometry.submerged))
            target = float(_finite(condition.mass_kg/rho, 'continuation target volume'))
            def residual(value):
                check_budget('warm in-port equilibrium')
                ta, tf, heel = _finite(value, 'continuation trial/root')
                if abs(heel) >= math.radians(85):
                    raise ValueError('invalid continuation attitude')
                s = submerged(ta, tf, heel, rho)
                _finite([s.volume_m3, *s.centroid_m], 'continuation submerged state')
                _, along, across = geometry._axes(ta, tf, heel)
                _finite([along, across], 'continuation moment axes')
                cg = physical(heel, math.atan((ta-tf)/length))
                with np.errstate(over='ignore', invalid='ignore'):
                    diff = np.asarray(s.centroid_m)-cg
                    answer = [(s.volume_m3-target)/target,
                              float(diff@along)/length, float(diff@across)/geometry.beam_m]
                _finite_norm(answer, 'continuation residual')
                return answer
            solved = root(residual, [previous.t_aft_m, previous.t_fwd_m,
                                    previous.heel_rad], tol=1e-10)
            _finite(solved.x, 'continuation returned root')
            error = _finite_norm(residual(solved.x), 'continuation residual')
            if error > 2e-9:
                raise ValueError('continuation residual requires cold solve')
            ta, tf, heel = map(float, solved.x)
            return _checked_floating(FloatingCondition(ta, tf, heel,
                geometry.submerged(ta, tf, heel, rho), error))
        except _NonfiniteNumericalError:
            raise
        except (ValueError, ArithmeticError, RuntimeError) as error:
            if _nonfinite_error(error):
                raise _NonfiniteNumericalError(str(error)) from error
            return self.cold(geometry, condition, rho, length, method)

    def cold(self, geometry, condition, rho, length, method):
        if method == 2:
            values = _finite([condition.mass_kg, condition.lcg_m,
                condition.tcg_m, condition.kg_m+condition.fsc_m, rho], 'equilibrium loading')
            return _checked_equilibrium(geometry, *values)
        key = _cold_physical_key(geometry, condition, rho, length)
        if key is not None and key in self._cold_physical:
            self._cold_physical.move_to_end(key)
            snapshot, references = self._cold_physical[key]
            floating, usage, cg, upright = deepcopy(snapshot)
            condition.liquid_usage.clear()
            condition.liquid_usage.update(usage)
            condition.cg_cache.clear()
            condition.cg_cache.update(cg)
            condition.upright_cache.clear()
            condition.upright_cache.update(upright)
            return _checked_floating(floating)
        direct_before = condition.liquid_usage.get('direct',0)
        floating = _checked_floating(
            _checked_coupled_equilibrium(geometry, condition, rho, length))
        if key is not None and condition.liquid_usage.get('direct',0) == direct_before:
            snapshot = deepcopy((floating, condition.liquid_usage,
                                 condition.cg_cache, condition.upright_cache))
            # The key uses the direct source's id, not this freshly loaded table
            # wrapper. Retain only that source: each wrapper owns a large parsed
            # metadata tree and independent grid arrays, already fingerprinted.
            references = (geometry.direct, tuple(tank.geometry for tank in condition.actual_tanks))
            self._cold_physical[key] = snapshot, references
            while len(self._cold_physical) > _COLD_PHYSICAL_CACHE_LIMIT:
                self._cold_physical.popitem(last=False)
        return floating


def _evaluate(instance, ship, context, continuation, *, warm_start=True, force_direct=False):
    from stowage.checker import stability
    from stowage.checker.stability_liquid import attach_tables
    from stowage.checker.stability_tables import cached_geometry
    from zipfile import BadZipFile
    h = instance.vessel.hull
    if h is None or ship.vessel_id != instance.vessel.id:
        raise ValueError('missing hull or mismatched ship vessel')
    if not 0 <= instance.call_index < len(instance.rotation.calls):
        raise ValueError('Call index outside rotation')
    if instance.rotation.calls[instance.call_index].locode != instance.port.locode:
        raise ValueError('Port density/profile for requested call is unavailable')
    stability._canonical_fleet_policy(instance.vessel)
    # The liquid context binds this exact vessel for the evaluator lifetime.
    # Reuse its immutable parsed geometry instead of reparsing the full tank
    # payload at every lift. Minimal test contexts may still delegate normally.
    if hasattr(context, 'geometry') and hasattr(context, 'params'):
        geometry, params = context.geometry, context.params
    else:
        geometry, params = stability._context(instance.vessel)
    direct = force_direct or os.environ.get('STOWAGE_STABILITY_DIRECT') == '1'
    method = int(os.environ.get('STOWAGE_STABILITY_FREE_SURFACE_METHOD', '1'))
    if method not in (1, 2):
        raise ValueError('free-surface method must be 1 or 2')
    if ship.covers_off:
        coamings = {c['id']: c for c in params.get('envelope', {}).get('hatch_coamings', [])}
        for cid in ship.covers_off:
            c = coamings[cid]
            if not all(math.isfinite(float(c[k])) for k in
                       ('x_min_m', 'x_max_m', 'y_min_m', 'y_max_m', 'z_max_m')):
                raise ValueError('nonfinite open coaming geometry')
    condition = stability.loading_condition(instance, ship, geometry=geometry,
        ship_params=params, free_surface_method=method,
        precomputed_soundings=True, small_tank_exemption=True)
    if condition.errors:
        raise ValueError('; '.join(condition.errors))
    condition = context.correct_loading(condition)
    _finite([condition.mass_kg, condition.lcg_m, condition.tcg_m, condition.kg_m,
             condition.fsm_kgm, condition.fsc_m], 'in-port loading condition')
    if not direct and method == 1 and os.environ.get('STOWAGE_STABILITY_LIQUID_DIRECT') != '1':
        condition = attach_tables(condition, params,
            vessel_hash=getattr(geometry, '_stability_vessel_hash', None),
            _metadata_value=getattr(geometry, '_stability_liquid_metadata', None))
    table_used = False
    if not direct:
        try:
            accelerated = cached_geometry(geometry, params, instance.port.water_density_kg_m3)
        except (OSError, ValueError, KeyError, TypeError, BadZipFile):
            accelerated = None
        if accelerated is not None:
            geometry, table_used = accelerated, True
    try:
        floating = (continuation.solve if warm_start else continuation.cold)(
            geometry, condition, instance.port.water_density_kg_m3, h.lpp_m, method)
        _checked_floating(floating)
        metrics, margins, broken = stability._in_port_criteria(
            instance, ship, _CheckedCriteriaGeometry(geometry), params, condition, floating, method)
        _finite(list(metrics.values()), 'in-port criteria metrics')
        _finite(list(margins.values()), 'in-port criteria margins')
        _finite([item[:2] for item in broken], 'in-port criteria violation values')
        # Rev3 P5 replaces the primitive's two endpoint load-line tests.
        for key in ('draught_aft_m', 'draught_fwd_m', 'port_draught_m'):
            margins.pop(key, None)
        broken = [item for item in broken if not item[3].startswith(
            ('draught_aft_m ', 'draught_fwd_m ', 'port_draught_m '))]
        m5, limits5, b5 = draught_limits(instance, geometry, floating, condition.mass_kg)
        _finite(list(m5.values()), 'in-port draught metrics')
        _finite(list(limits5.values()), 'in-port draught margins')
        metrics.update(m5)
        margins.update(limits5)
        broken.extend(b5)
        if ((warm_start and continuation.previous is not None)
            or (not direct and (table_used or condition.liquid_tables))) and near_limit(metrics, margins):
            report = _evaluate(instance, ship, context, continuation,
                               warm_start=False, force_direct=True)
            report.metrics['in_port_cold_limit_fallback'] = 1.
            return report
        continuation.previous = floating
        metrics.update(geometry_table_used=float(table_used),
                       in_port_warm_start=float(warm_start),
                       in_port_max_fsm_tanks=float(len(context.max_fsm_tank_ids)))
        return PartReport(name='in_port', metrics=metrics, margins=margins,
            violations=[Violation(rule_id=RULE, severity='hard', call_index=instance.call_index,
                                  measured=v, limit=limit, unit=unit, message=message)
                        for v, limit, unit, message in broken])
    except _NonfiniteNumericalError:
        raise
    except (ValueError, ArithmeticError, KeyError, TypeError, RuntimeError) as error:
        if _nonfinite_error(error):
            raise _NonfiniteNumericalError(str(error)) from error
        if table_used and not direct:
            return _evaluate(instance, ship, context, continuation,
                             warm_start=False, force_direct=True)
        raise


def check_state(instance, ship, context, continuation=None, *, warm_start=True,
                force_direct=False, heel_intervals=False):
    """One independently budgeted state; only equilibrium non-convergence is P1."""
    continuation = continuation or _Continuation()
    try:
        seconds = float(os.environ.get('STOWAGE_STABILITY_TIME_BUDGET_S', '30'))
        with budget(seconds):
            report = _evaluate(instance, ship, context, continuation,
                               warm_start=warm_start, force_direct=force_direct)
            if heel_intervals and not _failed(report):
                return _interval_report(instance, ship, context, report)
            return report
    except Exception as error:
        physical = equilibrium_nonconvergence(error)
        message = f'P1: {error}' if physical else f'{type(error).__name__}: {error}'
        return PartReport(name='in_port', ran=physical, error=None if physical else message,
            violations=[Violation(rule_id=RULE if physical else 'checker.internal_error',
                severity='hard', call_index=instance.call_index, message=message)])


def _worker_init(instance, context, warm_start, heel_intervals=False, reuse=True):
    global _worker_instance, _worker_context, _worker_warm, _worker_continuation, _worker_intervals, _worker_reuse
    _worker_instance, _worker_context, _worker_warm = instance, context, warm_start
    _worker_continuation = _Continuation()
    _worker_intervals = heel_intervals
    # Spawn does not inherit the parent's ContextVar value. Retain its snapshot
    # as plain worker configuration; enter the public scope only around tasks.
    _worker_reuse = reuse


def _worker_reuse_scope():
    from contextlib import nullcontext
    from stowage.checker.perf import without_reuse
    return nullcontext() if _worker_reuse else without_reuse()


def _worker_check(ship):
    with _worker_reuse_scope():
        return _timed_check(_worker_instance, ship, _worker_context,
                            _worker_continuation, warm_start=_worker_warm,
                            heel_intervals=_worker_intervals)


def _interval_report(instance, ship, context, ideal_report):
    """Enrich a copy; endpoint errors never alter the base prepass evidence."""
    report = ideal_report.model_copy(deep=True)
    if _failed(report):
        return report
    try:
        seconds = float(os.environ.get('STOWAGE_STABILITY_TIME_BUDGET_S', '30'))
        with budget(seconds):
            from stowage.checker.in_port_heel import compute_heel_interval
            interval = compute_heel_interval(instance, ship, context, report)
            report.metrics.update(interval.metrics())
            # B.1: an empty approximate band belongs to the plan-level R2
            # sweep (heel_step/linearised_miss), not a new P-criterion. The
            # base physical evidence remains unchanged; numerical proof
            # failures still take the internal-error path below.
        return report
    except Exception as error:
        message = f'HEEL interval {type(error).__name__}: {error}'
        report.ran, report.error = False, message
        report.violations.append(Violation(rule_id='checker.internal_error', severity='hard',
            call_index=instance.call_index, message=message))
        return report


def _timed_interval(instance, ship, context, ideal_report):
    started = monotonic()
    return _interval_report(instance, ship, context, ideal_report), monotonic()-started


def _worker_interval_check(ship, ideal_report):
    with _worker_reuse_scope():
        return _timed_interval(_worker_instance, ship, _worker_context, ideal_report)


def _timed_check(instance, ship, context, continuation, *, warm_start,
                 heel_intervals=False):
    started = monotonic()
    report = check_state(instance, ship, context, continuation,
                         warm_start=warm_start, heel_intervals=heel_intervals)
    return report, monotonic()-started


class InPortEvaluator:
    """Reusable independent workers, bounded submissions and ordered reports.

    Input ships already have their phase ballast and HEEL redistribution. Callers
    own the activation snapshot and transfer diagnostics. Workers retain their
    previous physical root as a guess; cold mode is bit-identical across worker
    counts. Warm mode promises verdict equivalence, with direct cold limit guards.
    Default workers=1 respects the shared-machine compute allocation. Set the
    replay's STOWAGE_IN_PORT_WORKERS only under an admitted reservation.
    """
    def __init__(self, instance, context, *, workers=1, warm_start=True,
                 heel_intervals=False):
        if isinstance(workers, bool) or not isinstance(workers, int) or workers < 1:
            raise ValueError('workers must be a positive integer')
        self.instance, self.context = instance.model_copy(deep=True), context
        self.workers, self.warm_start = workers, warm_start
        self.heel_intervals = heel_intervals
        self._pool = None
        self._pool_owner = None
        self._continuation = _Continuation()
        self._closed = False

    def __enter__(self):
        if self._closed:
            raise RuntimeError('in-port evaluator is closed')
        return self

    def __exit__(self, *args):
        self.close()

    def close(self):
        if self._pool is not None and self._pool_owner is None:
            self._pool.shutdown(wait=True, cancel_futures=True)
        self._pool, self._closed = None, True

    def for_context(self, context, *, heel_intervals=False):
        """Borrow this replay's workers with a task-local arrival/departure FSM.

        Only a phase view of the same liquid context is accepted. Its physical
        geometry, candidate ballast and immutable instance remain bound to the
        owner's lifetime; closing a view never closes the shared workers.
        """
        owner = self._pool_owner or self
        if owner._closed or self._closed:
            raise RuntimeError('in-port evaluator is closed')
        if any(getattr(context,key,None) is not getattr(owner.context,key,None)
               for key in ('geometry','params','departure_ship')):
            raise ValueError('phase views require the same liquid context')
        view = object.__new__(type(self))
        view.instance, view.context = owner.instance, context
        view.workers, view.warm_start = owner.workers, owner.warm_start
        view.heel_intervals = heel_intervals
        view._pool, view._pool_owner = None, owner
        view._continuation, view._closed = owner._continuation, False
        return view

    def _ensure_pool(self):
        owner = self._pool_owner or self
        if owner._closed:
            raise RuntimeError('in-port evaluator is closed')
        if owner._pool is None:
            from stowage.checker.perf import reuse_enabled
            owner._pool = ProcessPoolExecutor(max_workers=self.workers,
                mp_context=multiprocessing.get_context('spawn'), initializer=_worker_init,
                initargs=(owner.instance, owner.context, owner.warm_start,
                          owner.heel_intervals, reuse_enabled()))
        self._pool = owner._pool
        return self._pool

    def _submit_check(self, pool, ship):
        if self._pool_owner is None:
            return pool.submit(_worker_check,ship)
        return pool.submit(_worker_phase_check,ship,
            self.context.max_fsm_tank_ids,self.heel_intervals)

    def _submit_interval(self, pool, ship, report):
        if self._pool_owner is None:
            return pool.submit(_worker_interval_check,ship,report)
        return pool.submit(_worker_phase_interval,ship,report,
            self.context.max_fsm_tank_ids)

    def evaluate(self, states, *, stop_at_first_failure=False):
        if self._closed or (self._pool_owner is not None and self._pool_owner._closed):
            raise RuntimeError('in-port evaluator is closed')
        ships = [getattr(s, 'ship', s) for s in states]
        reports, times, failure = [], [], None
        if self.workers == 1:
            for index, ship in enumerate(ships):
                report, duration = _timed_check(self.instance, ship, self.context,
                    self._continuation, warm_start=self.warm_start,
                    heel_intervals=self.heel_intervals)
                reports.append(report)
                times.append(duration)
                if _failed(report):
                    if failure is None:
                        failure = index
                    if stop_at_first_failure:
                        break
        elif ships:
            if any(os.environ.get(k) != '1' for k in ('OPENBLAS_NUM_THREADS', 'OMP_NUM_THREADS')):
                raise RuntimeError('parallel in-port requires OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1')
            pool = self._ensure_pool()
            pending, next_index = deque(), 0
            try:
                for _ in range(min(self.workers, len(ships))):
                    pending.append((next_index, self._submit_check(pool, ships[next_index])))
                    next_index += 1
                while pending:
                    index, future = pending.popleft()
                    report, duration = future.result()
                    reports.append(report)
                    times.append(duration)
                    if _failed(report):
                        if failure is None:
                            failure = index
                        if stop_at_first_failure:
                            break
                    if next_index < len(ships):
                        pending.append((next_index, self._submit_check(pool, ships[next_index])))
                        next_index += 1
            finally:
                for _, future in pending:
                    future.cancel()
        return TimedInPortBatchResult(True, tuple(reports), len(ships), failure, tuple(times))

    def evaluate_intervals(self, states, ideal_reports):
        """Ordered endpoint-only enrichment using this evaluator's worker pool.

        The caller's prepass reports are copied, so candidate-specific endpoint
        failures cannot change k. Timings include only enrichment; no base
        state is solved a second time. The original phase context is retained.
        """
        if self._closed or (self._pool_owner is not None and self._pool_owner._closed):
            raise RuntimeError('in-port evaluator is closed')
        ships = [getattr(state, 'ship', state) for state in states]
        supplied = list(ideal_reports)
        if len(ships) != len(supplied):
            raise ValueError('HEEL interval states and ideal reports must have equal length')
        reports, times, failure = [], [], None
        if self.workers == 1:
            for index, (ship, ideal) in enumerate(zip(ships, supplied, strict=True)):
                report, duration = _timed_interval(self.instance, ship, self.context, ideal)
                reports.append(report)
                times.append(duration)
                if failure is None and _failed(report):
                    failure = index
        elif ships:
            if any(os.environ.get(key) != '1' for key in ('OPENBLAS_NUM_THREADS', 'OMP_NUM_THREADS')):
                raise RuntimeError('parallel in-port requires OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1')
            pool = self._ensure_pool()
            pending, next_index = deque(), 0
            try:
                for _ in range(min(self.workers, len(ships))):
                    pending.append((next_index, self._submit_interval(pool,
                        ships[next_index], supplied[next_index])))
                    next_index += 1
                while pending:
                    index, future = pending.popleft()
                    report, duration = future.result()
                    reports.append(report)
                    times.append(duration)
                    if failure is None and _failed(report):
                        failure = index
                    if next_index < len(ships):
                        pending.append((next_index, self._submit_interval(pool,
                            ships[next_index], supplied[next_index])))
                        next_index += 1
            finally:
                for _, future in pending:
                    future.cancel()
        return TimedInPortBatchResult(True, tuple(reports), len(ships), failure, tuple(times))


def _worker_phase_context(fsm_ids):
    for phase in ('arrival','departure'):
        context = _worker_context.for_phase(phase)
        if context.max_fsm_tank_ids == fsm_ids:
            return context
    raise ValueError('FSM phase does not belong to this replay context')


def _worker_phase_check(ship, fsm_ids, heel_intervals):
    with _worker_reuse_scope():
        return _timed_check(_worker_instance,ship,_worker_phase_context(fsm_ids),
            _worker_continuation,warm_start=_worker_warm,heel_intervals=heel_intervals)


def _worker_phase_interval(ship, report, fsm_ids):
    with _worker_reuse_scope():
        return _timed_interval(_worker_instance,ship,_worker_phase_context(fsm_ids),report)
