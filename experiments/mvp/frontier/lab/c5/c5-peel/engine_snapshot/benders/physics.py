# Copied from experiments/mvp/frontier/lab/c3/c2-benders/physics.py; unchanged native separation oracle.
"""Exact production-physics separation for fixed-stow ballast search.

Calls ordinary check_stability/check_lashing for the ordinary D57 derivation;
the same qualified hydrostatic/liquid tables and direct near-limit fallbacks
remain active. No copied physics, checker hooks, new interpolation, rounded
cache keys, or rule changes. `physics_feasible` is not a full-checker pass.
Only a fresh unchanged stowage.checker.check may accept a final plan.

The normalized vector is for numerical search: containership areas/levers
are divided by their CURRENT Code threshold, securing margins are already
1-ratio, other loading margins use explicit SI scales. No infeasibility proof
is obtained from this nonlinear separation oracle or its local derivatives.
"""
from __future__ import annotations

from collections import Counter, OrderedDict
from collections.abc import Mapping
from copy import deepcopy
import hashlib
import math
import os
from pathlib import Path
import time

import numpy as np


PHASES = ('departure', 'mid_voyage', 'arrival')
PARTS = ('stability', 'lashing')


def _safe(value):
    """JSON-safe telemetry: unavailable numbers are null, never NaN/Infinity."""
    if isinstance(value, Mapping):
        return {str(k): _safe(v) for k, v in value.items()}
    if isinstance(value, (tuple, list)):
        return [_safe(v) for v in value]
    if isinstance(value, np.ndarray):
        return _safe(value.tolist())
    if isinstance(value, (float, np.floating)):
        return float(value) if math.isfinite(value) else None
    if isinstance(value, np.integer):
        return int(value)
    return value


def margin_scale(part, name, value, instance, report):
    """Positive physical scaling only; the sign of every raw margin survives."""
    if part == 'lashing':
        return 1.
    if name.startswith('containership_'):
        metric = name.removeprefix('containership_')
        if metric in report.metrics:
            # Every B2.3 row is a lower bound: margin=measured-current_limit.
            # Current limit includes the actual form factor and any tightening.
            limit = float(report.metrics[metric])-float(value)
            if math.isfinite(limit) and limit > 0.:
                return limit
    if 'kg' in name:
        return max(float(report.metrics.get('displacement_kg', 1.)), 1.)
    if name == 'list_rad':
        return max(instance.vessel.hull.max_list_rad, .01)
    if 'mrad' in name:
        return .05
    if name.endswith('rad'):
        return .1
    return 1.


class PhysicsEvaluator:
    """One immutable, replayed cargo stow with exact-mass candidate memoization.

    `evaluate` accepts a full adjustable-tank mass vector ordered by tank_ids,
    or a mapping whose omitted adjustable tanks retain their inherited masses.
    The `margins` dict is authoritative for named alignment of finite differences;
    `margin_names` and `margin_vector` are its sorted JSON-safe convenience view.
    Missing/unavailable reports set completed=False and add an unavailable
    margin. Their changing vector shape must never be treated as zero slack.
    """

    def __init__(self, instance, plan, *, cache_size=256):
        from stowage.frozen import active_stubs
        from stowage.checker.full import _ballast
        from stowage.solver.ballast import _validated_replay
        if active_stubs():
            raise ValueError('PhysicsEvaluator requires ordinary frozen providers')
        self.instance = instance.model_copy(deep=True)
        self.plan = plan.model_copy(deep=True)
        replayed = _validated_replay(self.instance, self.plan).final_state.ship
        self.ship, invalid = _ballast(self.instance, self.plan, replayed)
        if invalid:
            raise ValueError('invalid initial ballast declaration: '+'; '.join(v.message for v in invalid))
        self.tanks = tuple(sorted((tank for tank in self.instance.vessel.tanks if tank.adjustable),
                                  key=lambda tank: tank.id))
        self.tank_ids = tuple(tank.id for tank in self.tanks)
        self.capacity_kg = np.array([tank.capacity_m3*tank.content_density_kg_m3 for tank in self.tanks])
        self.initial_masses_kg = np.array([self.ship.tank_masses_kg.get(tank.id, 0.) for tank in self.tanks])
        self.initial_fractions = np.divide(self.initial_masses_kg, self.capacity_kg,
            out=np.zeros_like(self.capacity_kg), where=self.capacity_kg > 0.)
        self.fixed_masses_kg = {tank.id: self.ship.tank_masses_kg.get(tank.id, 0.)
                               for tank in self.instance.vessel.tanks if not tank.adjustable}
        self.cache_size = max(0, int(cache_size))
        self.cache = OrderedDict()
        self.evaluations = self.cache_hits = 0
        from stowage.checker import (stability, stability_criteria, stability_curve,
                                    stability_loading, stability_geometry,
                                    stability_tables, stability_liquid, lashing, visibility)
        from stowage.vessel import tanks, hydrostatics
        from stowage.solver import ballast_arrival
        modules = (stability, stability_criteria, stability_curve, stability_loading,
                   stability_geometry, stability_tables, stability_liquid,
                   tanks, hydrostatics, lashing, visibility, ballast_arrival)
        self.source_sha256 = {module.__name__: hashlib.sha256(Path(module.__file__).read_bytes()).hexdigest()
                              for module in modules}

    def _masses(self, values):
        if isinstance(values, Mapping):
            unknown = set(values)-set(self.tank_ids)
            if unknown:
                raise ValueError('non-adjustable or unknown tank identifiers: '+', '.join(sorted(unknown)))
            masses = np.array([values.get(tank_id, float(initial))
                               for tank_id, initial in zip(self.tank_ids, self.initial_masses_kg)], float)
        else:
            masses = np.asarray(values, dtype=float)
        if masses.shape != self.capacity_kg.shape or not np.isfinite(masses).all():
            raise ValueError('expected one finite mass per adjustable tank')
        if np.any(masses < -1e-6) or np.any(masses > self.capacity_kg+1e-6):
            raise ValueError('adjustable tank mass outside physical capacity')
        return np.minimum(np.maximum(masses, 0.), self.capacity_kg)

    def candidate_plan(self, tank_masses_kg):
        from stowage.model import BallastCondition
        masses = self._masses(tank_masses_kg)
        return self.plan.model_copy(update={'ballast': BallastCondition(
            tank_masses_kg=dict(zip(self.tank_ids, map(float, masses))), source='finisher')})

    def evaluate_fractions(self, fractions, **kwargs):
        values = np.asarray(fractions, float)
        if values.shape != self.capacity_kg.shape or not np.isfinite(values).all():
            raise ValueError('expected one finite fraction per adjustable tank')
        return self.evaluate(values*self.capacity_kg, **kwargs)

    def evaluate(self, tank_masses_kg, *, parts=PARTS, phases=PHASES, deadline=None):
        """Evaluate exact requested parts/phases; optional deadline is cooperative.

        No process or numerical thread is launched. The caller owns any hard
        watchdog and CPU admission. An ongoing native part may outlive deadline;
        only its completed result is retained and remaining parts are unknown.
        """
        from stowage.checker.stability import check_stability
        from stowage.checker import stability, stability_loading
        from stowage.checker.lashing import check_lashing
        from stowage.checker.perf import evaluation_cache
        from stowage.frozen import active_stubs
        from stowage.solver.ballast_v4_evaluator import conditions
        started = time.monotonic()
        masses = self._masses(tank_masses_kg)
        if active_stubs():
            raise ValueError('PhysicsEvaluator requires ordinary frozen providers')
        parts, phases = tuple(parts), tuple(phases)
        if not parts or not phases or len(set(parts)) != len(parts) or len(set(phases)) != len(phases):
            raise ValueError('parts and phases must be nonempty and contain no duplicates')
        if set(parts)-set(PARTS) or set(phases)-set(PHASES):
            raise ValueError('unsupported physics part or voyage phase')
        environment = tuple(sorted((key, value) for key, value in os.environ.items()
                                   if key.startswith('STOWAGE_')))
        key = (masses.tobytes(), parts, phases, environment,
               id(check_stability), id(check_lashing), id(conditions),
               id(stability._context), id(stability_loading.loading_condition))
        if key in self.cache:
            self.cache_hits += 1
            self.cache.move_to_end(key)
            out = deepcopy(self.cache[key])
            out.update(cache_hit=True, wall_s=time.monotonic()-started,
                       original_wall_s=out['wall_s'], part_times_s={})
            return out
        self.evaluations += 1
        ship = self.ship.model_copy(update={'tank_masses_kg': {
            **self.fixed_masses_kg, **dict(zip(self.tank_ids, map(float, masses)))}})
        out = dict(status='evaluating', completed=True, physics_feasible=False,
            acceptance='search separation only; full checker still required',
            tank_ids=list(self.tank_ids), tank_masses_kg=dict(zip(self.tank_ids, map(float, masses))),
            phases=list(phases), parts=list(parts), all_three_states=set(phases) == set(PHASES),
            margins={}, raw_margins={}, margin_scales={}, metrics={}, part_reports={}, raw_loading={},
            violations=[], part_times_s={}, cache_hit=False, source_sha256=self.source_sha256,
            environment=dict(environment))
        functions = {'stability': check_stability, 'lashing': check_lashing}
        # All candidate-dependent consumable drawdown is re-derived. In
        # particular adjustable consumable tanks are not frozen in a context.
        with evaluation_cache():
            for phase, inst, state in conditions(self.instance, ship):
                if phase not in phases:
                    continue
                # Upright centroids are the raw loading coordinates, before
                # finite-angle liquid redistribution or equilibrium list.
                # They matter because the worst-side GZ curve sees raw TCG,
                # even when a tank-dependent equilibrium list is nearly zero.
                # Telemetry only: never inject these fields into a PartReport.
                raw = {}
                if deadline is None or time.monotonic() < deadline:
                    before = time.monotonic()
                    try:
                        geometry, params = stability._context(inst.vessel)
                        loading = stability_loading.loading_condition(inst, state,
                            geometry=geometry, ship_params=params,
                            free_surface_method=int(os.environ.get('STOWAGE_STABILITY_FREE_SURFACE_METHOD', '1')),
                            precomputed_soundings=True, small_tank_exemption=True)
                        raw = dict(mass_kg=float(loading.mass_kg), lcg_m=float(loading.lcg_m),
                                   tcg_m=float(loading.tcg_m), kg_m=float(loading.kg_m),
                                   fsc_m=float(loading.fsc_m), fsm_kgm=float(loading.fsm_kgm),
                                   errors=list(loading.errors))
                    except (ValueError, ArithmeticError, KeyError, TypeError, OSError) as error:
                        raw = {'error': f'{type(error).__name__}: {error}'}
                    out['part_times_s'][phase+'/raw_loading'] = time.monotonic()-before
                out['raw_loading'][phase] = raw
                for name in parts:
                    prefix = phase+'/'+name
                    if deadline is not None and time.monotonic() >= deadline:
                        out['completed'] = False
                        out['margins'][prefix+'/unavailable'] = -1.
                        out['raw_margins'][prefix+'/unavailable'] = -1.
                        continue
                    before = time.monotonic()
                    report = functions[name](inst, state, call_index=inst.call_index)
                    out['part_times_s'][prefix] = time.monotonic()-before
                    out['part_reports'][prefix] = report.model_dump(mode='json')
                    out['metrics'][prefix] = dict(report.metrics)
                    out['metrics'][prefix].update({'raw_'+key: value for key, value in raw.items()
                                                   if key in ('mass_kg', 'lcg_m', 'tcg_m', 'kg_m', 'fsc_m', 'fsm_kgm')})
                    hard = [v for v in report.violations if v.severity == 'hard']
                    out['violations'].extend({'phase': phase, 'part': name, **v.model_dump(mode='json')}
                                             for v in hard)
                    finite = all(math.isfinite(float(value)) for value in report.margins.values())
                    for metric, value in report.margins.items():
                        full = prefix+'/'+metric
                        scale = margin_scale(name, metric, value, inst, report)
                        out['raw_margins'][full] = float(value)
                        out['margin_scales'][full] = scale
                        out['margins'][full] = float(value)/scale if math.isfinite(value) else -1e6
                    if not report.ran or report.error or not finite or not report.margins:
                        out['completed'] = False
                        out['margins'][prefix+'/unavailable'] = -1.
                        out['raw_margins'][prefix+'/unavailable'] = -1.
                    elif hard and not any(float(value) < 0. for value in report.margins.values()):
                        # Discrete validity errors cannot disappear merely
                        # because their report has no negative scalar margin.
                        out['margins'][prefix+'/unrepresented_hard_violation'] = -1.
                        out['raw_margins'][prefix+'/unrepresented_hard_violation'] = -1.
        out['physics_feasible'] = bool(out['completed'] and not out['violations'] and
                                      all(value >= -1e-10 for value in out['raw_margins'].values()))
        out['status'] = ('physics_feasible' if out['physics_feasible'] else
                         'violated' if out['completed'] else 'unknown')
        out['violation_counts'] = dict(Counter(v['rule_id'] for v in out['violations']))
        out['margin_names'] = sorted(out['margins'])
        out['margin_vector'] = [out['margins'][name] for name in out['margin_names']]
        out['wall_s'] = time.monotonic()-started
        out = _safe(out)
        if out['completed'] and self.cache_size:
            self.cache[key] = deepcopy(out)
            while len(self.cache) > self.cache_size:
                self.cache.popitem(last=False)
        return out


def evaluate_physics(instance, plan, tank_masses_kg, **kwargs):
    """One-shot convenience entry. Reuse PhysicsEvaluator for optimization."""
    return PhysicsEvaluator(instance, plan).evaluate(tank_masses_kg, **kwargs)
