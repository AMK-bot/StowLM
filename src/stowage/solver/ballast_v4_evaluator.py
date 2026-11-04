"""Cached exact ballast probes; only the unchanged full checker accepts.

Cheap equilibrium/GM probes precede curves and securing at every D57 sample.
Selected-margin calls support the existing successive-linear search without
rechecking unrelated parts. Missing calculations never supply positive margins.
"""
from __future__ import annotations

import time
from collections import Counter

import numpy as np

from stowage.model import BallastCondition
from stowage.solver.ballast_arrival import arrival_condition, mid_voyage_condition
from stowage.solver.ballast_physics import ExactContext
from stowage.solver.ballast_search import Deadline, Found


LOADING_KEYS = frozenset('stability/' + key for key in (
    'gm_m', 'trim_m', 'trim_min_m', 'displacement_minimum_kg',
    'displacement_maximum_kg', 'list_rad', 'draught_aft_m',
    'draught_fwd_m', 'draught_fwd_min_m', 'port_draught_m'))


def phase_prefix(phase):
    """Keep departure names compatible with shared GM/list search helpers."""
    return '' if phase == 'departure' else phase+'/'


def conditions(instance, ship):
    """Use the same existing D57 derivation as the ordinary finisher."""
    mid_instance, mid_ship = mid_voyage_condition(instance, ship)
    arrival_instance, arrival_ship = arrival_condition(instance, ship)
    return [('departure', instance, ship),
            ('mid_voyage', mid_instance, mid_ship),
            ('arrival', arrival_instance, arrival_ship)]


def checkers():
    from stowage.checker.stability import check_stability
    from stowage.checker.strength import check_strength
    from stowage.checker.visibility import check_visibility
    from stowage.checker.lashing import check_lashing
    return dict(stability=check_stability, strength=check_strength,
                visibility=check_visibility, lashing=check_lashing)


class Evaluator:
    """One fixed stow; caches have no lifetime beyond this solve."""

    def __init__(self, instance, plan, ship, deadline, diagnostics):
        self.instance, self.plan, self.ship = instance, plan, ship
        self.deadline, self.diagnostics = deadline, diagnostics
        self.tanks = sorted((t for t in instance.vessel.tanks if t.adjustable),
                            key=lambda tank: tank.id)
        self.capacity = np.array([t.capacity_m3*t.content_density_kg_m3
                                  for t in self.tanks])
        self.fixed = {t.id: ship.tank_masses_kg.get(t.id, 0.)
                      for t in instance.vessel.tanks if not t.adjustable}
        self.initial = np.divide(
            [ship.tank_masses_kg.get(t.id, 0.) for t in self.tanks],
            self.capacity, out=np.zeros(len(self.tanks)), where=self.capacity > 0)
        self.initial = np.clip(self.initial, 0., 1.)
        self.contexts, self.cache, self.part_cache = [], {}, {}
        self.probe_failures = {}
        self.candidate_identities = set()
        self.recipe_identities = set()
        self.accepted, self.accepted_report = None, None
        self.best_parts, self.best_merit = {}, float('inf')
        self.measured = {}
        self.functions = checkers()
        from stowage.checker.stability_loading import CONSUMABLE_KINDS
        variable_consumables = any(t.kind in CONSUMABLE_KINDS for t in self.tanks)
        if variable_consumables:
            # Re-derive voyage liquids for each candidate instead of freezing
            # a phase whose consumable drawdown could change with the decision.
            diagnostics['cheap_screen_disabled'] = 'adjustable consumable tanks'
        elif instance.vessel.hull is not None:
            started = time.monotonic()
            try:
                for phase, inst, state in conditions(instance, ship):
                    self.checkpoint()
                    context = ExactContext(inst, state, deadline)
                    if [t.id for t in context.tanks] != [t.id for t in self.tanks]:
                        raise ValueError('D57 adjustable tank order changed')
                    self.contexts.append((phase, context))
            except (ValueError, ArithmeticError, KeyError, TypeError, FileNotFoundError) as error:
                # Exact public checker calls remain available. No replacement
                # linear table or unqualified approximation is substituted.
                self.contexts = []
                diagnostics['cheap_screen_disabled'] = f'{type(error).__name__}: {error}'
            finally:
                diagnostics['context_setup_s'] = time.monotonic()-started

    def checkpoint(self):
        if time.monotonic() >= self.deadline:
            raise Deadline

    def fractions(self, values):
        values = np.asarray(values, dtype=float)
        if values.shape != self.capacity.shape or not np.isfinite(values).all():
            raise ValueError('invalid ballast fraction vector')
        values = np.clip(values, 0., 1.).copy()
        values[self.capacity == 0.] = 0.
        values[values == 0.] = 0.  # canonicalize signed zero in recipe identity
        return values

    def candidate(self, fractions):
        masses = {tank.id: float(value*capacity)
                  for tank, value, capacity in zip(self.tanks, fractions, self.capacity)}
        ship = self.ship.model_copy(update={'tank_masses_kg': {**self.fixed, **masses}})
        plan = self.plan.model_copy(update={'ballast': BallastCondition(
            tank_masses_kg=masses, source='finisher')})
        return ship, plan

    def _part(self, identity, phase, inst, ship, name):
        key = (identity, phase, name)
        if key not in self.part_cache:
            self.checkpoint()
            self.diagnostics['stage'] = f'physics:{phase}/{name}'
            before = time.monotonic()
            report = self.functions[name](inst, ship, call_index=inst.call_index)
            self.checkpoint()
            self.part_cache[key] = report
            self.diagnostics['physics_part_calls'] += 1
            self.diagnostics['physics_part_s'] += time.monotonic()-before
        return self.part_cache[key]

    def _margins(self, phase, inst, name, report):
        from stowage.solver.ballast import _normal_margin
        prefix = phase_prefix(phase)+name+'/'
        margins = {prefix+key: _normal_margin(name, key, value, inst, report)
                   for key, value in report.margins.items()}
        hard = any(v.severity == 'hard' for v in report.violations)
        if not report.ran or report.error:
            margins[prefix+'unavailable'] = -1.
        elif hard and not any(value < 0. for value in margins.values()):
            margins[prefix+'hard_violation'] = -1.
        return margins

    def _remember(self, identity, values, reports):
        merit = sum(min(float(value), 0.)**2 for value in values.values())
        self.measured[identity] = merit
        if merit < self.best_merit:
            self.best_merit, self.best_parts = merit, dict(reports)

    def _defer_loading(self, context, key, value):
        """Table boundaries and unavailable probes belong to public stability.

        Reuse the checker's numerical guard, not a relaxed hard-rule margin.
        Floating probes already refine draft/trim/list near their limits; this
        additional deferral also protects upright GM and coupled-root recovery.
        """
        if key.endswith(('probe_unavailable', 'equilibrium_unavailable')):
            return True
        if value >= 0.:
            return False
        from stowage.checker import stability
        instance = getattr(context, 'instance', self.instance)
        name = key.split('/', 1)[-1]
        metrics = {}
        if name == 'gm_m':
            try:
                minimum = max(stability._minimum_loading_gm(instance),
                    float(instance.rule_overrides.get('iscode.a2_2.initial_gm', {}).get('minimum', .15)))
                metrics['gm_m'] = value+minimum
            except (ValueError, TypeError, AttributeError, KeyError):
                return True  # unknown policy cannot justify table rejection
        if name == 'list_rad':
            value *= max(instance.vessel.hull.max_list_rad, 1e-8)
        return stability._near_table_limit(metrics, {name:value})

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
        for phase, inst, state in conditions(self.instance, ship):
            for name in self.functions:
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
