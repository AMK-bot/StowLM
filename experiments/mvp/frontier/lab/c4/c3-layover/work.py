"""Deterministic construction admission. Limits never depend on elapsed seconds."""
from dataclasses import dataclass, field
import math


class WorkExhausted(RuntimeError):
    pass


DEFAULT_LIMITS = dict(branches=4, layouts=8, greedy_trials=16, pricing_calls=160,
    patterns=6000, lp_calls=24, lp_iterations=24000, dive_steps=80,
    cp_calls=8, balance_trials=1200)


@dataclass
class WorkBudget:
    limits: dict = field(default_factory=lambda: dict(DEFAULT_LIMITS))
    used: dict = field(default_factory=dict)
    exhausted: list = field(default_factory=list)
    cp_deterministic_ticks: float = .04
    cp_conflicts: int = 10000
    lp_maxiter: int = 1000
    parent: object = field(default=None, repr=False)

    @classmethod
    def from_context(cls, context):
        config = dict(context.get('work_config', {}))
        allowed = {'limits', 'cp_deterministic_ticks', 'cp_conflicts', 'lp_maxiter'}
        unknown = set(config)-allowed
        if unknown:
            raise ValueError('unknown work configuration: '+', '.join(sorted(unknown)))
        supplied = dict(config.get('limits', {}))
        unknown = set(supplied)-set(DEFAULT_LIMITS)
        if unknown:
            raise ValueError('unknown work counters: '+', '.join(sorted(unknown)))
        limits = dict(DEFAULT_LIMITS)
        limits.update({k: max(0, int(v)) for k, v in supplied.items()})
        ticks = float(config.get('cp_deterministic_ticks', .04))
        conflicts = int(config.get('cp_conflicts', 10000))
        iterations = int(config.get('lp_maxiter', 1000))
        if not math.isfinite(ticks) or ticks < 0. or conflicts < 0 or iterations < 0:
            raise ValueError('solver work limits must be finite and nonnegative')
        return cls(limits=limits, cp_deterministic_ticks=ticks,
                   cp_conflicts=conflicts, lp_maxiter=iterations)

    def take(self, name, amount=1):
        count = self.used.get(name, 0)+amount
        if count > self.limits[name]:
            if name not in self.exhausted:
                self.exhausted.append(name)
            # A zero/exhausted global cap is also globally diagnostic. A
            # merely narrower branch reservation must not mark the parent.
            if self.parent is not None and amount > self.parent.remaining(name):
                try:
                    self.parent.take(name, amount)
                except WorkExhausted:
                    pass
            raise WorkExhausted(name)
        if self.parent is not None:
            self.parent.take(name, amount)
        self.used[name] = count

    def for_branch(self, label, branches_left):
        """Reserve construction counts for later arrival-prefix choices.

        Preserving prefixes cannot spend the broad-restow fallback's work.
        All-prefixes receives most of what remains, then selective branches
        share the remainder. Both layouts in one branch consume this scope.
        Every local admission also charges the global parent exactly once.
        """
        limits = {}
        for name in self.limits:
            remaining = self.remaining(name)
            if label == 'preserve_prefixes':
                allowance = max(1, int(.20*self.limits[name])) if remaining else 0
            elif label == 'all_prefixes':
                allowance = math.ceil(.60*remaining)
            else:
                allowance = math.ceil(remaining/max(1, branches_left))
            limits[name] = min(remaining, allowance)
        return WorkBudget(limits=limits, parent=self,
            cp_deterministic_ticks=self.cp_deterministic_ticks,
            cp_conflicts=self.cp_conflicts, lp_maxiter=self.lp_maxiter)

    def remaining(self, name):
        return self.limits[name]-self.used.get(name, 0)

    def snapshot(self):
        return dict(limits=dict(self.limits), used=dict(self.used), exhausted=list(self.exhausted),
            cp_deterministic_ticks_per_solve=self.cp_deterministic_ticks,
            cp_conflicts_per_solve=self.cp_conflicts, lp_maxiter_per_solve=self.lp_maxiter,
            elapsed_seconds_are_not_a_stopping_rule=True)
