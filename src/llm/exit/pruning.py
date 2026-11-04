"""Conservative D109 pruning, independent of MLX.

Only committed current-call lifts form the lower bound. The move engine adds a
future-port projection to the final objective; that projection can *decrease*
after another placement and is deliberately absent here. Nonnegative D11
weights make the retained relocation/restow/hatch sums monotone for append-only
decoding. A checked complete incumbent is needed because validity outranks cost.

``PruningDecoder`` uses the sampler's existing terminal-status contract. Its
empty allowed set / non-running status makes ``generate_masked`` physically
remove the row with ``_keep(cache, rows)``; no sampler patch is required. Keep
greedy fitness samples unwrapped so pruning cannot bias paired pass counts.
"""
from __future__ import annotations

from dataclasses import dataclass
import ast
import hashlib
import inspect
import textwrap
import math
import threading
from typing import Any

PRUNED_STATUS = 'pruned_worse_than_incumbent'
PRUNED_LABEL = 'pruned (worse than incumbent)'
_COST_FIELDS = {
    'relocate': 'relocate',
    'restow_shift': 'restow', 'restow_off': 'restow', 'restow_on': 'restow',
    'hatch_cover_off': 'hatch_cover', 'hatch_cover_on': 'hatch_cover',
    'load': None, 'discharge': None,
}
_WEIGHT_FIELDS = ('relocate', 'restow', 'hatch_cover', 'future_restow', 'future_hatch_cover')


# Reviewed append-only sampler semantic identity. Any new sampler control flow,
# including tonight's backtracking change, disables pruning until reviewed.
# Whitespace/comments do not change this AST digest. No MLX import is needed.
_APPEND_ONLY_SAMPLER_AST = '10c231b3bdf794cab52c20777852ef2e9a96f32ef4a00f7cc87955ae579a7ca7'


def sampler_is_append_only(sampler) -> bool:
    """Fail closed on unreviewed/recovering sampler implementations."""
    try:
        source = ast.parse(textwrap.dedent(inspect.getsource(sampler)))
        functions = [node for node in source.body if isinstance(node, ast.FunctionDef)]
        if len(functions) != 1:
            return False
        digest = hashlib.sha256(ast.dump(functions[0], include_attributes=False).encode()).hexdigest()
        return digest == _APPEND_ONLY_SAMPLER_AST
    except (OSError, TypeError, SyntaxError):
        return False


def _number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) and value >= 0


@dataclass(frozen=True)
class Incumbent:
    """A measured complete plan admitted by the full checker and layered replay."""
    weighted_moves: float
    source: str = 'checked'
    verified: bool = True
    complete: bool = True

    def __post_init__(self):
        if not _number(self.weighted_moves) or not self.verified or not self.complete:
            raise ValueError('pruning incumbent requires a finite nonnegative cost and complete verified plan')


def committed_lift_lower_bound(instance, moves) -> float | None:
    """D11 irrevocable cost, or None when its monotonicity cannot be certified.

    Negative future weights also disable the bound: a negative terminal term
    could otherwise lower the completed total beneath current committed costs.
    Unknown/simulator move kinds fail closed rather than guessing a cost.
    """
    weights = instance.objective
    if any(not _number(getattr(weights, name, None)) for name in _WEIGHT_FIELDS):
        return None
    counts = dict(relocate=0, restow=0, hatch_cover=0)
    for move in moves:
        kind = move.get('kind') if isinstance(move, dict) else getattr(move, 'kind', None)
        if kind not in _COST_FIELDS:
            return None
        field = _COST_FIELDS[kind]
        if field is not None:
            counts[field] += 1
    # Match the move engine's grouped multiplication and addition order to avoid
    # a repeated-addition rounding excess at an exact objective tie.
    total = (weights.relocate * counts['relocate'] + weights.restow * counts['restow']
             + weights.hatch_cover * counts['hatch_cover'])
    return total if math.isfinite(total) else None


def _committed_moves(engine):
    """Read existing securing/lookahead expander or production engine state."""
    ex = getattr(engine, 'expander', None)
    if ex is None:
        ex = getattr(getattr(engine, '_cur', None), 'ex', None)
    return getattr(ex, 'moves', None)


def _can_rewind(decoder):
    # A future backtracking hook must declare its non-append-only semantics;
    # dropping a costly branch before the search can rewind would be unsound.
    for obj in (decoder, getattr(decoder, 'config', None)):
        for name in ('backtracking', 'allow_backtracking', 'backtrack_budget', 'max_backtracks'):
            if getattr(obj, name, False):
                return True
    return False


class PruningController:
    """One map's thread-safe incumbent shared across its stochastic samples."""
    def __init__(self, instance, incumbent: Incumbent | None = None, *, incumbent_provider=None):
        self.instance = instance
        self.incumbent_provider = incumbent_provider
        self.provider_error = None
        self._lock = threading.RLock()
        self.incumbent = None
        self.events: list[dict[str, Any]] = []
        if incumbent is not None:
            self.update(incumbent)

    def update(self, incumbent: Incumbent) -> bool:
        if not isinstance(incumbent, Incumbent):
            raise TypeError('pass a checked Incumbent, not an unchecked numeric score')
        with self._lock:
            if self.incumbent is None or incumbent.weighted_moves < self.incumbent.weighted_moves:
                self.incumbent = incumbent
                return True
        return False

    def refresh(self):
        """Nonblocking provider may expose newly finished CPU verification."""
        if self.incumbent_provider is not None:
            try:
                incumbent = self.incumbent_provider()
                if incumbent is not None:
                    self.update(incumbent)
            except Exception as error:
                # Keep the last certified incumbent; an unavailable update is
                # never a reason to terminate a decode or invent a score.
                self.provider_error = type(error).__name__

    def wrap_decoder(self, decoder, *, sample_id=None, append_only=True, exempt=False):
        return PruningDecoder(decoder, self, sample_id=sample_id, append_only=append_only, exempt=exempt)

    def summary(self):
        with self._lock:
            return {'pruned_samples': len(self.events), 'status': PRUNED_STATUS,
                    'label': PRUNED_LABEL, 'events': list(self.events),
                    'dead_ends_added': 0, 'dpo_rejected_examples_added': 0,
                    'sampler_row_drop': True,
                    'gpu_time_saved_s': None, 'provider_error': self.provider_error}


class PruningDecoder:
    """Terminal-status proxy; the existing sampler drops its token/KV rows.

    The standard mask is append-only. Explicitly pass ``append_only=False`` for
    rewind/search decoders; known backtracking flags additionally disable it.
    Complete plans and true dead ends retain their original status. A rewind
    observed at a prefix disables pruning for the remainder of that sample.
    """
    def __init__(self, decoder, controller, *, sample_id=None, append_only=True, exempt=False):
        self.inner, self.controller = decoder, controller
        self.sample_id = sample_id
        self.enabled = bool(append_only and not exempt and not _can_rewind(decoder))
        self.disabled_reason = None if self.enabled else 'nonmonotone_or_fitness_exempt'
        self._pruned = False
        self._previous_moves = ()
        self._cached_key = None
        self._lower_bound = None

    def __getattr__(self, name):
        return getattr(self.inner, name)

    @property
    def status(self):
        return PRUNED_STATUS if self._pruned else self.inner.status

    @property
    def dead_end(self):
        return None if self._pruned else getattr(self.inner, 'dead_end', None)

    def _consider(self):
        if self._pruned or not self.enabled or self.inner.status != 'running':
            return
        if _can_rewind(self.inner):
            self.enabled, self.disabled_reason = False, 'nonmonotone_backtracking'
            return
        self.controller.refresh()
        engine = self.inner.engine
        moves = _committed_moves(engine)
        if moves is None:
            self.enabled, self.disabled_reason = False, 'committed_moves_unavailable'
            return
        key = (id(engine), getattr(engine, 'depth', None), id(moves), len(moves))
        if key != self._cached_key:
            current = tuple(moves)
            if current[:len(self._previous_moves)] != self._previous_moves:
                self.enabled, self.disabled_reason = False, 'nonmonotone_prefix_rewritten'
                return
            self._previous_moves, self._cached_key = current, key
            self._lower_bound = committed_lift_lower_bound(self.controller.instance, current)
        with self.controller._lock:
            incumbent = self.controller.incumbent
            if self._lower_bound is not None and incumbent is not None and self._lower_bound > incumbent.weighted_moves:
                self._pruned = True
                self.controller.events.append({'sample_id': self.sample_id, 'status': PRUNED_STATUS,
                    'label': PRUNED_LABEL, 'partial_weighted_moves_lower_bound': self._lower_bound,
                    'incumbent_weighted_moves': incumbent.weighted_moves, 'incumbent_source': incumbent.source,
                    'excluded_from_dpo': True})

    def allowed_token_ids(self):
        self._consider()
        return () if self._pruned else self.inner.allowed_token_ids()

    def advance(self, token_id):
        action = self.inner.advance(token_id)
        self._consider()
        return action

    def prefetch(self):
        self._consider()
        return False if self._pruned else self.inner.prefetch()

    def pop_action(self):
        # The current decoder exposes an explicit rewind API. Once used, no
        # claim of append-only costs is valid for this sample.
        self.enabled, self.disabled_reason = False, 'nonmonotone_backtracking'
        return self.inner.pop_action()

    def diagnostics(self):
        query = getattr(self.inner, 'diagnostics', None)
        details = dict(query()) if callable(query) else {'status': self.inner.status}
        if self._pruned:
            details.update(status=PRUNED_STATUS, dead_end=None, dead_ends_by_rule={},
                           pruning={'reason': PRUNED_LABEL, 'excluded_from_dpo': True,
                                    'partial_weighted_moves_lower_bound': self._lower_bound})
        return details


def exclude_from_preferences(candidate) -> bool:
    """Incomplete cost-pruned prefixes are never preference rejected examples."""
    metadata = candidate.get('metadata', {})
    return candidate.get('status') == PRUNED_STATUS or metadata.get('status') == PRUNED_STATUS
