# Copied from experiments/mvp/frontier/lab/c1/fx-finish/fast_finish.py; engine integration changes are local.
"""Experimental V4 finisher: exact rectangular-tank slice and lazy part order.

The complete checker remains unmodified and every cache/analytic helper below
is inactive while it runs. The worker stays under the native owned watchdog.
Copied evaluate() from src/stowage/solver/ballast_v4_evaluator.py, changing only
phase-major to part-major ordering. The wrapper below supplies an optional
inherited-recipe full-check lane before the ordinary V4 search.
"""
from __future__ import annotations

from collections import Counter, OrderedDict
from contextlib import contextmanager, ExitStack
from contextvars import ContextVar
from copy import copy
from functools import wraps
import math
import os
import time

import numpy as np

from stowage.solver.ballast_v4_evaluator import LOADING_KEYS, conditions, phase_prefix
from stowage.solver.ballast_search import Found

_ACTIVE = ContextVar("fx_finish_public_part", default=None)
ORDER = ("lashing", "visibility", "stability", "strength")


def rectangular_slice(solid, volume_m3, heel_rad, trim_rad):
    """Exact liquid integral when the plane cuts all four vertical box edges.

    Return None if any side/top/bottom clipping occurs. In centred coordinates,
    the free surface is z=d+p*x+q*y, with liquid depth H=d+c. Integrating this
    affine depth over the rectangle gives all three first moments directly.
    We retain the original surface-inertia routine on the four actual corners.
    No angle approximation, geometry replacement, or relaxed root tolerance is
    involved. This changes search arithmetic only; the final checker uses its
    original 64-iteration box clipping root.
    """
    from stowage.vessel.tanks import TankGeometry, LiquidCondition, _surface
    if type(solid) is not TankGeometry:
        return None
    solid._validate_volume(volume_m3)
    if not all(math.isfinite(v) and abs(v) < math.pi/2 for v in (heel_rad, trim_rad)):
        return None
    if heel_rad == trim_rad == 0 or volume_m3 in (0., solid.capacity_m3):
        return None
    centre = np.array([(getattr(solid, f'{axis}_min_m')+
                        getattr(solid, f'{axis}_max_m'))/2 for axis in 'xyz'])
    a, b, c = [(getattr(solid, f'{axis}_max_m')-
                 getattr(solid, f'{axis}_min_m'))/2 for axis in 'xyz']
    depth = volume_m3/(4*a*b)
    d = depth-c
    p, q = -math.tan(trim_rad), math.tan(heel_rad)
    # Strict interior classification protects the discontinuous corner cases.
    clearance = c-abs(d)-abs(p)*a-abs(q)*b
    if clearance <= 1e-10*max(a, b, c, 1.):
        return None
    relative = np.array([p*a*a/(3*depth), q*b*b/(3*depth),
                         -c+depth/2+(p*p*a*a+q*q*b*b)/(6*depth)])
    normal = np.array([math.tan(trim_rad)*math.cos(heel_rad),
                       -math.sin(heel_rad), math.cos(heel_rad)])
    normal /= np.linalg.norm(normal)
    surface = np.array([(x, y, d+p*x+q*y)
                        for x, y in ((-a,-b), (a,-b), (a,b), (-a,b))])
    area, inertia = _surface(surface, normal, trim_rad)
    level = d*normal[2]+float(centre @ normal)
    return LiquidCondition(volume_m3, tuple(map(float, centre+relative)),
                           area, inertia, level)


def rectangular_root(solid, volume_m3, heel_rad, trim_rad):
    """Exact box-volume polynomial, with original final geometry validation.

    Projection coordinates t_i lie in [0,w_i]. A simplex has volume h**k/k!;
    inclusion-exclusion removes the parts beyond the box faces. The polynomial
    replaces only volume evaluations in the original 64-step bisection. Original
    convex-hull integration computes the final centroid and independently checks
    the root. Unreliable subtraction returns None for the unchanged fallback.
    """
    from itertools import product
    from stowage.vessel.tanks import TankGeometry, LiquidCondition, _clip, _surface
    if type(solid) is not TankGeometry:
        return None
    solid._validate_volume(volume_m3)
    if not all(math.isfinite(v) and abs(v) < math.pi/2 for v in (heel_rad, trim_rad)):
        return None
    if heel_rad == trim_rad == 0 or volume_m3 in (0., solid.capacity_m3):
        return None
    centre = np.array([(getattr(solid, f'{axis}_min_m')+
                        getattr(solid, f'{axis}_max_m'))/2 for axis in 'xyz'])
    half = np.array([(getattr(solid, f'{axis}_max_m')-
                      getattr(solid, f'{axis}_min_m'))/2 for axis in 'xyz'])
    normal = np.array([math.tan(trim_rad)*math.cos(heel_rad),
                       -math.sin(heel_rad), math.cos(heel_rad)])
    normal /= np.linalg.norm(normal)
    widths = (2*half*np.abs(normal)).astype(np.longdouble)
    widths = widths[widths != 0.]
    k = len(widths)
    masks = np.array(list(product((0, 1), repeat=k)))
    shifts = masks @ widths
    signs = np.where(masks.sum(axis=1) % 2, -1, 1).astype(np.longdouble)
    denominator = math.factorial(k)*np.prod(widths)
    total_width = widths.sum()
    capacity = np.longdouble(solid.capacity_m3)
    corners = np.array(list(product((-1., 1.), repeat=3)))*half
    edges = [(i, j) for i in range(8) for j in range(i+1, 8)
             if np.count_nonzero(corners[i] != corners[j]) == 1]
    projections = corners @ normal
    low, high = float(projections.min()), float(projections.max())
    radius = np.sum(half.astype(np.longdouble)*np.abs(normal.astype(np.longdouble)))
    epsilon = np.finfo(np.longdouble).eps
    for _ in range(64):
        level = (low+high)/2
        h = np.longdouble(level)+radius
        complement = h > total_width/2
        h = total_width-h if complement else h
        terms = np.maximum(h-shifts, np.longdouble(0.))**k
        numerator = np.sum(signs*terms)
        uncertainty = np.sum(np.abs(terms))*epsilon*32
        if uncertainty > denominator*np.longdouble(1e-12) or numerator < 0.:
            return None
        fraction = numerator/denominator
        if not 0. <= fraction <= 1.:
            return None
        volume = capacity*(1-fraction if complement else fraction)
        if volume < volume_m3:
            low = level
        else:
            high = level
    volume, centroid, surface = _clip(corners, edges, projections, level)
    # Original permits 1e-9*capacity; retain a stricter independent guard.
    if abs(volume-volume_m3) > 1e-10*solid.capacity_m3:
        return None
    area, inertia = _surface(surface, normal, trim_rad)
    result = LiquidCondition(volume_m3, tuple(map(float, centroid+centre)),
                            area, inertia, level+float(centre @ normal))
    if not np.isfinite([*result.centroid_m, area, inertia, result.plane_level_m]).all():
        return None
    return result


def _state_key(instance, ship, *, cargo_only=False):
    """Bind all actual mutable inputs; never round tank fills or geometry."""
    return (instance.model_dump_json(round_trip=True),
            ship.model_dump_json(round_trip=True,
                                 exclude={'tank_masses_kg'} if cargo_only else None))


@contextmanager
def search_acceleration():
    """Request-local hooks, enabled only inside Evaluator._part callbacks.

    Full checking, cheap probes, static legality, intermediate-port checks and
    seed construction keep their original helpers. A completed public report
    still supplies every rejection; only callback order changes.
    """
    from stowage.checker import visibility
    from stowage.solver import ballast_v4_evaluator as module
    from stowage.vessel.tanks import TankGeometry
    original_evaluate = module.Evaluator.evaluate
    original_part = module.Evaluator._part
    original_checkers = module.checkers
    original_loading = visibility._loading_condition
    original_cargo = visibility._all_cargo_geometry
    original_liquid = TankGeometry.liquid
    loadings, cargos, liquids = OrderedDict(), OrderedDict(), OrderedDict()

    def cached(bank, key, compute, name, limit):
        diagnostics = _ACTIVE.get()
        if diagnostics is None:
            return compute()
        counts = diagnostics.setdefault('fx_exact_reuse', {})
        if key in bank:
            bank.move_to_end(key)
            result, elapsed = bank[key]
            counts[name+'_hits'] = counts.get(name+'_hits', 0)+1
            counts[name+'_avoided_s'] = counts.get(name+'_avoided_s', 0.)+elapsed
            return result
        before = time.monotonic()
        result = compute()
        bank[key] = result, time.monotonic()-before
        counts[name+'_misses'] = counts.get(name+'_misses', 0)+1
        if len(bank) > limit:
            bank.popitem(last=False)
        return result

    @wraps(original_loading)
    def loading(instance, ship):
        if _ACTIVE.get() is None:
            return original_loading(instance, ship)
        return copy(cached(loadings, _state_key(instance, ship),
                    lambda: original_loading(instance, ship), 'loading', 128))

    @wraps(original_cargo)
    def cargo(instance, ship):
        if _ACTIVE.get() is None:
            return original_cargo(instance, ship)
        return cached(cargos, _state_key(instance, ship, cargo_only=True),
                      lambda: original_cargo(instance, ship), 'cargo', 16)

    @wraps(original_liquid)
    def liquid(solid, volume_m3, heel_rad=0., trim_rad=0.):
        diagnostics = _ACTIVE.get()
        if diagnostics is None or type(solid) is not TankGeometry:
            return original_liquid(solid, volume_m3, heel_rad, trim_rad)
        # Frozen dataclass fields and exact floats bind the complete function.
        key = (solid, float(volume_m3), float(heel_rad), float(trim_rad))
        def compute():
            result = rectangular_slice(solid, volume_m3, heel_rad, trim_rad)
            counters = diagnostics.setdefault('fx_exact_reuse', {})
            name = 'rectangular_slice'
            if result is None:
                result = rectangular_root(solid, volume_m3, heel_rad, trim_rad)
                name = 'rectangular_root' if result is not None else 'rectangular_fallback'
            counters[name] = counters.get(name, 0)+1
            return result if result is not None else original_liquid(
                solid, volume_m3, heel_rad, trim_rad)
        return cached(liquids, key, compute, 'liquid', 8192)

    @wraps(original_part)
    def part(evaluator, identity, phase, instance, ship, name):
        token = _ACTIVE.set(evaluator.diagnostics)
        # Real helper identities are restored between public parts, so cheap
        # probes and complete checks see unmodified numerical qualifications.
        visibility._loading_condition = loading
        visibility._all_cargo_geometry = cargo
        TankGeometry.liquid = liquid if name in ('visibility', 'lashing') else original_liquid
        try:
            with ExitStack() as contexts:
                if name in ('visibility', 'lashing'):
                    if os.environ.get('FX_FINISH_GATHER') == '1':
                        from .shaped_liquid import gather_context
                        contexts.enter_context(gather_context())
                    if os.environ.get('FX_FINISH_SECTION_POLY') == '1':
                        from .shaped_liquid import context as section_context
                        counters = evaluator.diagnostics.setdefault('fx_exact_reuse', {})
                        contexts.enter_context(section_context(counters))
                return original_part(evaluator, identity, phase, instance, ship, name)
        finally:
            TankGeometry.liquid = original_liquid
            visibility._all_cargo_geometry = original_cargo
            visibility._loading_condition = original_loading
            _ACTIVE.reset(token)

    def checkers():
        functions = original_checkers()
        return {name: functions[name] for name in ORDER if name in functions} | {
            name: callback for name, callback in functions.items() if name not in ORDER}

    module.Evaluator.evaluate = evaluate
    module.Evaluator._part = part
    module.checkers = checkers
    try:
        yield
    finally:
        TankGeometry.liquid = original_liquid
        visibility._all_cargo_geometry = original_cargo
        visibility._loading_condition = original_loading
        module.checkers = original_checkers
        module.Evaluator._part = original_part
        module.Evaluator.evaluate = original_evaluate


def worker(instance, plan, *, time_limit_s, seed, deadline):
    """Pickleable worker body; the native dispatcher owns its hard watchdog."""
    from stowage.solver import ballast_physics
    from stowage.solver import ballast_proof
    from stowage.solver.ballast_deadline import ExactDeadline
    from stowage.solver.ballast_v4 import finish as native_finish
    from .fast_proof import prove_infeasible
    original_budget = ballast_physics.exact_budget
    original_proof = ballast_proof.prove_infeasible

    @contextmanager
    def budget_without_event_tracing(local_deadline):
        # ExactContext checks its clock at each tank/iteration. Its native
        # parent watchdog bounds any single opaque numerical call. Avoid the
        # extra Python profiler callback on every event in those numerical
        # loops; retain the original context's entry/exit clock behavior.
        if time.monotonic() >= local_deadline:
            raise ExactDeadline
        yield
        if time.monotonic() >= local_deadline:
            raise ExactDeadline

    ballast_physics.exact_budget = budget_without_event_tracing
    ballast_proof.prove_infeasible = prove_infeasible
    try:
        with search_acceleration():
            return native_finish(instance, plan, time_limit_s=time_limit_s, seed=seed,
                                 _deadline=deadline)
    finally:
        ballast_physics.exact_budget = original_budget
        ballast_proof.prove_infeasible = original_proof


def worker_inherited(instance, plan, *, time_limit_s, seed, deadline):
    """Optional direct gate for inherited medium ballast before exact search.

    This lane can save duplicate public probes when the first recipe passes.
    A failed check remains only a rejected recipe; the ordinary search follows
    under the same absolute deadline. The primary worker does not enable this
    policy because it can spend extra checking time on a failing first recipe.
    """
    from stowage.model import BallastCondition
    from stowage.solver.ballast_v4 import BallastV4Outcome, finish as native_finish
    from stowage.solver.ballast_v4_seeds import trim_seed_recipes
    if plan.ballast is not None or len(instance.load_list) <= 16:
        return worker(instance, plan, time_limit_s=time_limit_s, seed=seed, deadline=deadline)
    from stowage.solver.ballast import _provenance, _validated_replay
    from .early_bound import securing_bound
    started = time.monotonic()
    replay = _validated_replay(instance, plan)
    bound = securing_bound(instance, replay.final_state.ship)
    if bound.get('proof') is not None:
        return BallastV4Outcome(status='infeasible_proven', proof=bound['proof'],
            provenance=_provenance(instance, plan, seed),
            wall_time_s=time.monotonic()-started,
            diagnostics={'fx_securing_certificate': bound,
                         'fx_securing_bound_s': time.monotonic()-started,
                         'full_checker_passed': None})
    inherited = trim_seed_recipes(instance)[0]
    candidate = plan.model_copy(update={'ballast': BallastCondition(
        tank_masses_kg=inherited.tank_masses_kg, source='finisher')})
    before = time.monotonic()
    first = native_finish(instance, candidate, time_limit_s=time_limit_s,
                          seed=seed, _deadline=deadline)
    elapsed = time.monotonic()-before
    if first.status in ('ok', 'kept'):
        return first.model_copy(update={'diagnostics': {
            **first.diagnostics, 'fx_inherited_fullcheck_first_s': elapsed}})
    outcome = worker(instance, plan, time_limit_s=max(0., deadline-time.monotonic()),
                     seed=seed, deadline=deadline)
    return outcome.model_copy(update={'diagnostics': {
        **outcome.diagnostics, 'fx_inherited_fullcheck_first_s': elapsed,
        'fx_inherited_fullcheck_first_status': first.status}})


def install(worker_name='worker'):
    """Install only this worker dispatcher; return an idempotent restore hook."""
    import importlib
    from pathlib import Path
    from stowage.solver import ballast_v4_dispatch
    # score.py loads plugins under a generated module name. A spawned native
    # worker must instead import this file by its ordinary stable module name.
    canonical_worker = getattr(importlib.import_module(__name__), worker_name)
    original = ballast_v4_dispatch._worker
    ballast_v4_dispatch._worker = canonical_worker
    def restore():
        if ballast_v4_dispatch._worker is canonical_worker:
            ballast_v4_dispatch._worker = original
    return restore


def finish(instance, plan, *, seed=0, finalize_s=30., label='fx-finish', context=None):
    """Lab plug-in, matching Frontier's finalize plus independent-check wrapper."""
    del context
    return _lab_finish(instance, plan, seed=seed, finalize_s=finalize_s, label=label,
                       worker_name='worker')


def finish_inherited(instance, plan, *, seed=0, finalize_s=30., label='fx-finish', context=None):
    """Optional lab lane with inherited-recipe full checking first."""
    del context
    return _lab_finish(instance, plan, seed=seed, finalize_s=finalize_s, label=label,
                       worker_name='worker_inherited')


def _lab_finish(instance, plan, *, seed, finalize_s, label, worker_name):
    import hashlib
    from pathlib import Path
    from ..frozen import planner as frozen_planner
    _finish = frozen_planner._finish
    from stowage.solver import ballast_v4_dispatch
    restore = install(worker_name)
    original_dispatch = ballast_v4_dispatch.finish
    captured = {}
    directory = Path(__file__).parent
    source_files = ('fast_finish.py', 'fast_proof.py', 'early_bound.py', 'shaped_liquid.py')
    sources = {name: hashlib.sha256((directory/name).read_bytes()).hexdigest()
               for name in source_files}

    @wraps(original_dispatch)
    def dispatch(*args, **kwargs):
        outcome = original_dispatch(*args, **kwargs)
        captured.update(outcome.diagnostics)
        return outcome

    ballast_v4_dispatch.finish = dispatch
    try:
        result, metadata = _finish(instance, plan, seed=seed, finalize_s=finalize_s, label=label)
        return result, {**metadata, 'fx_finish_diagnostics': captured,
                        'fx_finish_sources': sources}
    finally:
        ballast_v4_dispatch.finish = original_dispatch
        restore()


def selftest(seed=19820526, count=40):
    """Deterministic kernel regression, safe to run before map benchmarks.

    Cases include translated/non-square boxes, signed angles, nearly empty/full
    states, zero angles, and top/bottom clipping. Comparisons use the unchanged
    provider, independently of every patched context and checker acceptance.
    """
    from stowage.vessel.tanks import TankGeometry
    rng = np.random.default_rng(seed)
    maximum = np.zeros(6)
    interior, fallback, general = 0, 0, 0
    timings = dict(slice_original_s=0., slice_accelerated_s=0.,
                   root_original_s=0., root_accelerated_s=0.)
    for index in range(count):
        origin = rng.uniform([-200., -20., -5.], [200., 20., 5.])
        extents = rng.uniform([2., 1., 1.], [20., 8., 5.])
        solid = TankGeometry(**{f'{axis}_{bound}_m': float(origin[j]+(extents[j] if bound == 'max' else 0.))
                    for j, axis in enumerate('xyz') for bound in ('min', 'max')})
        fraction = rng.uniform(.2, .8)
        heel, trim = rng.uniform(-.02, .02, 2)
        volume = float(fraction*solid.capacity_m3)
        before = time.perf_counter()
        actual = rectangular_slice(solid, volume, heel, trim)
        accelerated_s = time.perf_counter()-before
        before = time.perf_counter()
        expected = solid.liquid(volume, heel, trim)
        original_s = time.perf_counter()-before
        if actual is None:
            fallback += 1
            continue
        interior += 1
        timings['slice_accelerated_s'] += accelerated_s
        timings['slice_original_s'] += original_s
        a = np.r_[actual.centroid_m, actual.plane_level_m,
                  actual.free_surface_area_m2, actual.fsm_m4]
        b = np.r_[expected.centroid_m, expected.plane_level_m,
                  expected.free_surface_area_m2, expected.fsm_m4]
        maximum = np.maximum(maximum, np.abs(a-b))
        np.testing.assert_allclose(a, b, atol=2e-10, rtol=2e-10,
                                   err_msg=f'rectangular liquid case {index}')
        for f, h, t in ((0., heel, trim), (1., heel, trim),
                        (.5, 0., 0.), (1e-8, .1, .1),
                        (1.-1e-8, -.1, -.1)):
            assert rectangular_slice(solid, f*solid.capacity_m3, h, t) is None
            fallback += 1
        # General angled cuts include side clipping, one exactly zero normal
        # component, near-tip wedges, complement volumes and cancellation.
        f = float(rng.choice([1e-6, .01, .09, .4, .8, .99, 1.-1e-6]))
        h, t = rng.uniform(-.6, .6, 2)
        if index % 4 == 0:
            h = 0.
        if index % 7 == 0:
            t = 0.
        before = time.perf_counter()
        trial = rectangular_root(solid, f*solid.capacity_m3, h, t)
        accelerated_s = time.perf_counter()-before
        if trial is not None:
            before = time.perf_counter()
            reference = solid.liquid(f*solid.capacity_m3, h, t)
            timings['root_original_s'] += time.perf_counter()-before
            timings['root_accelerated_s'] += accelerated_s
            a = np.r_[trial.centroid_m, trial.plane_level_m,
                      trial.free_surface_area_m2, trial.fsm_m4]
            b = np.r_[reference.centroid_m, reference.plane_level_m,
                      reference.free_surface_area_m2, reference.fsm_m4]
            np.testing.assert_allclose(a, b, atol=2e-8, rtol=2e-8,
                                       err_msg=f'general box-liquid case {index}')
            maximum = np.maximum(maximum, np.abs(a-b))
            general += 1
        else:
            fallback += 1
    assert interior > 0
    # Ensure hooks are restored after exceptional exits, and full checking
    # cannot accidentally enable the slice merely because hooks are installed.
    original = TankGeometry.liquid
    solid = TankGeometry(0., 10., -2., 2., 0., 3.)
    try:
        with search_acceleration():
            assert _ACTIVE.get() is None
            assert TankGeometry.liquid is original
            assert TankGeometry.liquid(solid, 60., .01, .01) == original(solid, 60., .01, .01)
            raise RuntimeError('expected restoration exercise')
    except RuntimeError:
        pass
    assert TankGeometry.liquid is original
    assert general > 0
    timings['slice_speedup'] = timings['slice_original_s']/max(timings['slice_accelerated_s'], 1e-12)
    timings['root_speedup'] = timings['root_original_s']/max(timings['root_accelerated_s'], 1e-12)
    return dict(seed=seed, interior_cases=interior, general_cases=general, fallback_cases=fallback,
                maximum_absolute_errors=maximum.tolist(), timings=timings)


def evaluate(self, fractions, active_keys=None):
    self.checkpoint()
    fractions = self.fractions(fractions)
    identity = fractions.tobytes()
    if identity not in self.recipe_identities:
        self.recipe_identities.add(identity)
        self.diagnostics['candidate_count'] = self.diagnostics.get('candidate_count', 0)+1
    selected = None if active_keys is None else frozenset(active_keys)
    key = (identity, selected)
    if key in self.cache:
        self.diagnostics['cache_hits'] += 1
        return dict(self.cache[key])
    self.diagnostics['evaluations'] += 1
    if selected is None:
        if identity not in self.candidate_identities:
            self.candidate_identities.add(identity)
            self.diagnostics['candidates_evaluated'] = self.diagnostics.get('candidates_evaluated', 0)+1
    else:
        self.diagnostics['selective_margin_evaluations'] = self.diagnostics.get('selective_margin_evaluations', 0)+1
    margins, reports, deferred = {}, {}, set()
    for phase, context in self.contexts:
        prefix = phase_prefix(phase)
        local = None if selected is None else {
            key[len(prefix):] for key in selected
            if key.startswith(prefix) and key[len(prefix):] in LOADING_KEYS}
        if selected is not None and not local:
            continue
        self.checkpoint()
        self.diagnostics['stage'] = f'loading:{phase}'
        before = time.monotonic()
        if (identity, phase) in self.probe_failures:
            values = {'stability/probe_unavailable':-1.}
        else:
            try:
                values = context.margins(fractions, active_keys=local)
            except (ValueError, ArithmeticError) as error:
                # Cache this recipe's numerical failure, then defer its
                # phase to ordinary stability, which has a coupled-root
                # fallback absent from the cheap Picard-only probe.
                self.probe_failures[(identity, phase)] = str(error)
                self.diagnostics['cheap_probe_errors'] = self.diagnostics.get('cheap_probe_errors', 0)+1
                self.diagnostics['last_cheap_probe_error'] = f'{phase}: {type(error).__name__}: {error}'
                values = {'stability/probe_unavailable':-1.}
            self.checkpoint()
            self.diagnostics['cheap_probe_calls'] += 1
            self.diagnostics['cheap_probe_s'] += time.monotonic()-before
        margins.update({prefix+key: float(value) for key, value in values.items()})
        deferred.update(prefix+key for key, value in values.items()
                        if self._defer_loading(context, key, value))
    if selected is not None and selected <= margins.keys():
        self.cache[key] = dict(margins)
        return dict(margins)
    if selected is None and any(value < -1e-9 for key, value in margins.items() if key not in deferred):
        self.diagnostics['cheap_rejections'] += 1
        # A different definite loading failure already rejects this fill.
        # Do not teach the local optimizer a permanent numerical-unknown
        # constraint that no ordinary PartReport can later supply.
        margins = {key:value for key, value in margins.items()
                   if not key.endswith(('probe_unavailable', 'equilibrium_unavailable'))}
        self._remember(identity, margins, reports)
        self.cache[key] = dict(margins)
        return margins

    ship, candidate = self.candidate(fractions)
    # Part-major lazy rejection: every phase of cheap parts precedes curves.
    states = conditions(self.instance, ship)
    for name in self.functions:
        for phase, inst, state in states:
            prefix = phase_prefix(phase)+name+'/'
            requested = None if selected is None else {k for k in selected if k.startswith(prefix)}
            if selected is not None and not requested:
                continue
            # A selected loading margin already computed above does not
            # need a second full stability curve for that phase.
            if requested is not None and requested <= margins.keys():
                continue
            report = self._part(identity, phase, inst, state, name)
            reports[f'{phase}/{name}'] = report
            if name == 'stability':
                # Actual public stability replaces numerical probe flags;
                # never carry a cheap unknown forward as a hard failure.
                for suffix in ('probe_unavailable', 'equilibrium_unavailable'):
                    margins.pop(prefix+suffix, None)
            values = self._margins(phase, inst, name, report)
            margins.update(values if selected is None else
                           {k: v for k, v in values.items() if k in selected})
            hard = any(v.severity == 'hard' for v in report.violations)
            if selected is None and (not report.ran or report.error or hard):
                # First completed rejection supplies exact feedback; the
                # other eleven expensive parts cannot make it acceptable.
                self.diagnostics['physics_rejections'] += 1
                self._remember(identity, margins, reports)
                self.cache[key] = dict(margins)
                return margins
    if selected is not None:
        # Unknown keys must not disappear into an empty successful vector.
        for missing in selected-margins.keys():
            margins[missing] = -1.
        self.cache[key] = dict(margins)
        return margins

    from stowage.checker import check
    self.checkpoint()
    self.diagnostics['stage'] = 'full_checker'
    before = time.monotonic()
    report = check(self.instance, candidate)
    self.checkpoint()
    self.diagnostics['full_checker_calls'] += 1
    self.diagnostics['full_checker_s'] += time.monotonic()-before
    self.diagnostics['full_checker_passed'] = report.passed
    self.diagnostics['last_full_checker_rule_counts'] = dict(Counter(
        v.rule_id for v in report.violations))
    if report.passed:
        self.accepted, self.accepted_report = candidate, report
        raise Found
    margins['full_checker/hard_violation'] = -1.
    self._remember(identity, margins, report.parts)
    self.cache[key] = dict(margins)
    return margins


if __name__ == '__main__':
    import argparse
    import json
    parser = argparse.ArgumentParser()
    parser.add_argument('--selftest', action='store_true')
    args = parser.parse_args()
    if args.selftest:
        print(json.dumps(selftest(), sort_keys=True))
