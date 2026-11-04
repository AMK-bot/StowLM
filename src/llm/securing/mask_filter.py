"""Opt-in IRS securing filter around the unchanged production feasibility mask.

A weight-legal stack can overload its fittings or lashings. The mask uses the
shared necessary envelope: the checker's IRS forces at GM 0.15 m and the
vessel's deepest documented draught. Unlike the solver's design-GM gold
filter, it uses a deliberately lenient force condition for prefix screening.
Single-tier components are exempt during partial construction; terminal state
checking includes every remaining tier. Unknown instance/cargo bounds fail open to the final
checker; absent or unverified declared vessel assets raise at construction. Prefixes with possible repairing discharge or future hold access also
defer, because hold access can restow deck boxes. Screen diagnostics record
uncertainty. The policy is a cheap partial-state screen, with a final checker
gate required for any completed plan.
Source: IRS-G-DES-19 §§3.3–3.6; see ``solver.securing_screen`` for the
calculation, condition assumptions and source details. Final ballast finishing,
full checking and production-mask replay remain mandatory admission gates.

APPROXIMATION: a necessary envelope is a partial feasibility screen, not a
proof that a rejected prefix has no possible completion or a certificate of
the actual departure condition.

Only this proxy's ``push``/``pop`` should mutate its owned inner engine. Copies
share the instance-scoped screen/cache, while their expanders and action state
remain independent. Wrapping an already advanced engine replays its actual
``actions()``; direct inner changes are also detected on the next query.
"""
from __future__ import annotations

from dataclasses import dataclass
import os
import time
from typing import Any

import numpy as np

from llm.securing.assets import validate_securing_assets

from stowage.actions.expander import Expander
from stowage.actions.space import Place
from stowage.conventions import cells_of_slot, format_slot, parse_slot
from stowage.feasibility import DeadEnd, IllegalActionError
from stowage.solver.securing_screen import NecessaryDeckSecuringScreen

_RULE = 'iacs.member_irs.container_securing'


@dataclass(frozen=True)
class _LegalSet:
    flat: np.ndarray
    actions: tuple
    members: frozenset
    discharges: tuple
    rejected: int
    reason: str | None


@dataclass
class _StateCache:
    # Copies of one prefix may be made before any legal query. Publish the
    # immutable result once for all such copies; mutations detach the holder.
    legal: _LegalSet | None = None
    terminal: Any = None


def _fallback_slots(instance):
    """Stable index tables for protocol test doubles without native arrays."""
    cells = {c.id: c for c in instance.vessel.cells}
    slots = set(cells)
    for cid in cells:
        a = parse_slot(cid)
        for bay in (a.bay - 1, a.bay + 1):
            if bay > 0:
                slot = format_slot(bay, a.row, a.tier)
                if all(c in cells for c in cells_of_slot(slot)):
                    slots.add(slot)
    return tuple(sorted(slots))


class SecuringEngine:
    """One cached, consistently filtered legal set across every mask interface.

    ``screen`` can be supplied to share a cache with sibling decoders. The
    instance and its physical data must remain immutable during decoding.
    A partial state may inherit an unsafe onboard stack pending discharge:
    placement queries inspect only the affected component, allowing those
    discharges to repair it. Completion checks every remaining deck component
    before EOS or ``to_plan`` can succeed.
    ``engine_wrapper`` composition belongs to ``securing_decoder_factory``;
    for example, pass ``lambda e: instruction_wrap(e, constrained)`` there.
    """

    def __init__(self, inner: Any, *, screen=None, expander=None):
        self.inner = inner
        self.instance = inner.instance
        validate_securing_assets(self.instance)
        self.screen = screen if screen is not None else NecessaryDeckSecuringScreen(self.instance)
        if getattr(self.screen, 'instance', self.instance) is not self.instance:
            raise ValueError('screen and engine must share the same immutable instance')
        self.securing_screen = self.screen
        self._actions = tuple(inner.actions())
        self.expander = expander if expander is not None else self._replay(self._actions)
        self._indexed = hasattr(inner, 'legal_index') and hasattr(inner, 'index_slots')
        self.index_boxes = tuple(inner.index_boxes) if hasattr(inner, 'index_boxes') else tuple(
            sorted(b.id for b in self.instance.containers))
        self.index_slots = tuple(inner.index_slots) if self._indexed else _fallback_slots(self.instance)
        self.n_slots = len(self.index_slots)
        self._boxes = {b: i for i, b in enumerate(self.index_boxes)}
        self._slots = {s: i for i, s in enumerate(self.index_slots)}
        self._cache = _StateCache()
        self._history = []
        # These concrete implementations own a consistent, already intersected
        # legal set. Rechecking each member can call an outer wrapper's _sync
        # (and reconstruct its complete action history) once per candidate.
        # Arbitrary proxies and subclasses still use the defensive intersection:
        # their secondary legal-index methods may delegate past a restriction.
        from stowage.feasibility._engine import IncrementalEngine
        from llm.lookahead.mask_filter import LookaheadEngine
        self._consistent_source = type(inner) in (IncrementalEngine, LookaheadEngine, SecuringEngine)

    def __getattr__(self, name):
        return getattr(self.inner, name)

    def _replay(self, actions):
        ex = Expander(self.instance)
        for action in actions:
            ex.step(action)
        return ex

    def _sync(self):
        actions = tuple(self.inner.actions())
        if actions != self._actions:
            self.expander = self._replay(actions)
            self._actions = actions
            self._history.clear()
            self._invalidate()

    def _invalidate(self):
        self._cache = _StateCache()

    def _terminal(self):
        self._sync()
        if self._cache.terminal is None:
            self._cache.terminal = self.screen.check_state(self.expander)
        return self._cache.terminal

    def _could_finish_next(self):
        """Cheap tail test before considering a full Expander trial.

        A hold action can restow deck boxes, so only its actual expanded state
        can establish completion. The count is an optimistic trigger, never a
        completion claim; the trial below checks loads, buffer and discharges.
        """
        ex = self.expander
        unplaced = {cid for cid in self.instance.load_list
                    if ex.where.get(cid, (None,))[0] != 'ship'} | set(ex.buffer)
        pending = sum(ex.where.get(cid, (None,))[0] == 'ship'
                      for cid in self.instance.discharge_list)
        return len(unplaced) <= 1 and pending <= 1

    def _final_action_result(self, action):
        trial = self.expander.copy()
        trial.step(action)
        complete = (not trial.buffer and
                    all(trial.where.get(cid, (None,))[0] == 'ship'
                        for cid in self.instance.load_list) and
                    all(trial.where.get(cid, (None,))[0] != 'ship'
                        for cid in self.instance.discharge_list))
        return self.screen.check_state(trial) if complete else None

    def _legal(self):
        self._sync()
        if self._cache.legal is not None:
            return self._cache.legal
        # A terminal securing failure must expose neither EOS nor actions even
        # if a custom inner engine retains stale legal pairs at completion.
        if self.inner.is_complete():
            flat = np.empty(0, dtype=np.int64)
            flat.flags.writeable = False
            result = self._terminal()
            self._cache.legal = _LegalSet(flat, (), frozenset(), (), 0,
                                          None if result.passed else result.reason)
            return self._cache.legal
        if self._consistent_source:
            source = self.inner.legal_actions()
        elif self._indexed:
            source = [self.decode_index(int(f)) for f in self.inner.legal_index()]
            source.extend(self.inner.legal_discharges())
        else:
            source = self.inner.legal_actions()
        accepted, rejected, reason = [], 0, None
        could_finish = self._could_finish_next()
        for action in source:
            # Intersection preserves constraints from an inner instruction
            # wrapper even if its secondary query methods delegate inward.
            if not self._consistent_source and not self.inner.is_legal(action):
                continue
            if isinstance(action, Place):
                result = self.screen.check_placement(self.expander, action)
                if not result.passed:
                    rejected += 1
                    reason = result.reason or 'IRS necessary securing screen rejected placement'
                    continue
            # The last action may be in a hold or be a Discharge, leaving an
            # earlier deferred deck component unchanged. Remove that action
            # before token construction if the full terminal screen rejects.
            if could_finish:
                result = self._final_action_result(action)
                if result is not None and not result.passed:
                    rejected += 1
                    reason = result.reason or 'IRS necessary securing screen rejects final action'
                    continue
            accepted.append(action)
        accepted = tuple(sorted(set(accepted), key=self.inner.space.sort_key))
        flat = np.asarray(sorted(self.index_of(a) for a in accepted if isinstance(a, Place)), dtype=np.int64)
        flat.flags.writeable = False
        discharges = tuple(a for a in accepted if not isinstance(a, Place))
        self._cache.legal = _LegalSet(flat, accepted, frozenset(accepted), discharges, rejected, reason)
        return self._cache.legal

    def legal_index(self):
        return self._legal().flat

    def legal_pairs(self):
        flat = self.legal_index()
        return flat // self.n_slots, flat % self.n_slots

    def decode_index(self, flat):
        f = int(flat)
        return Place(self.index_boxes[f // self.n_slots], self.index_slots[f % self.n_slots])

    def index_of(self, action):
        if not isinstance(action, Place):
            return None
        b, s = self._boxes.get(action.box), self._slots.get(action.slot)
        return None if b is None or s is None else b * self.n_slots + s

    def legal_actions(self):
        return list(self._legal().actions)

    def legal_discharges(self):
        return self._legal().discharges

    def legal_slots_for_box(self, box):
        return tuple(a.slot for a in self._legal().actions if isinstance(a, Place) and a.box == box)

    def legal_box_ids(self):
        return tuple(sorted({a.box for a in self._legal().actions if isinstance(a, Place)}))

    def legal_candidates(self, candidates):
        members = self._legal().members
        return (a for a in candidates if a in members)

    def iter_legal_actions(self, deadline=float('inf')):
        for action in self._legal().actions:
            if time.monotonic() >= deadline:
                break
            yield action

    def is_legal(self, action):
        return action in self._legal().members

    def is_complete(self):
        self._sync()
        return self.inner.is_complete() and self._terminal().passed

    def completion_check(self):
        method = getattr(self.inner, 'completion_check', None)
        result = dict(method()) if method is not None else {}
        result['securing_screen'] = (self._terminal().passed if self.inner.is_complete()
                                     else bool(self._legal().actions))
        return result

    def dead_end(self):
        self._sync()
        if self.inner.is_complete():
            result = self._terminal()
            return None if result.passed else DeadEnd(self.depth,
                'IRS necessary securing screen rejects completed plan: ' + (result.reason or 'force limit exceeded'),
                (_RULE,))
        dead = self.inner.dead_end()
        if dead is not None:
            return dead
        legal = self._legal()
        if not legal.actions:
            if not legal.rejected:
                return DeadEnd(self.depth, 'inner feasibility engine leaves no legal action')
            return DeadEnd(self.depth, 'IRS necessary securing screen leaves no legal action' +
                           (': ' + legal.reason if legal.reason else ''), (_RULE,))
        return None

    def push(self, action):
        if not self.is_legal(action):
            raise IllegalActionError(f'IRS necessary securing mask rejects action: {action!r}')
        trial = self.expander.copy()
        trial.step(action)
        self.inner.push(action)
        self._history.append((self._actions, self.expander, self._cache))
        self.expander = trial
        self._actions = tuple(self.inner.actions())
        self._invalidate()

    def pop(self):
        action = self.inner.pop()
        self._actions = tuple(self.inner.actions())
        if self._history and self._history[-1][0] == self._actions:
            _, self.expander, self._cache = self._history.pop()
        else:
            self.expander = self._replay(self._actions)
            self._history.clear()
            self._invalidate()
        return action

    def copy(self):
        self._sync()
        copied = object.__new__(SecuringEngine)
        copied.__dict__.update(self.__dict__)
        copied.inner = self.inner.copy()
        copied.expander = self.expander.copy()
        copied._history = list(self._history)
        return copied

    def to_plan(self):
        if not self.is_complete():
            raise ValueError('to_plan requires completion under the IRS securing screen')
        return self.inner.to_plan()

    def securing_metrics(self):
        """Snapshot only; never query legal actions or evaluate a component."""
        metrics = self.screen.metrics
        return dict(securing_calls=int(metrics.get('calls', 0)),
                    evaluations=int(metrics.get('evaluations', 0)),
                    unavailable=sum(int(v) for k, v in metrics.items() if k.startswith('unavailable:')),
                    fail_open=sum(int(v) for k, v in metrics.items() if k.startswith('fail_open:')),
                    securing_active=bool(getattr(self.screen, 'bounds_available', False)),
                    metrics_scope=f'{os.getpid()}:{id(self.screen)}')

    def screen_diagnostics(self):
        method = getattr(self.inner, 'screen_diagnostics', None)
        result = dict(method()) if method is not None else {}
        self._sync()
        cached = self._cache.legal
        result['securing_screen'] = dict(self.screen.metrics, **self.securing_metrics(),
            policy=getattr(self.screen, 'policy_name', type(self.screen).__name__),
            rejected_current_state=cached.rejected if cached else 0,
            last_reason=cached.reason if cached else None,
            evaluated_current_state=cached is not None)
        return result


def wrap_engine(engine, *, screen=None):
    """Opt in explicitly; the production feasibility engine is unchanged."""
    return SecuringEngine(engine, screen=screen)


def securing_decoder_factory(*, engine_wrapper=None):
    """Build production decoders over securing-filtered engines.

    ``engine_wrapper(engine)`` optionally applies instruction constraints first.
    Each sample owns an independent engine/expander; all samples share the
    immutable instance and the securing calculation cache. No model is loaded.
    """
    from llm.stowlm.mask import MaskedDecoder

    def factory(instance, engine, codec, mask_config, n):
        if engine.instance is not instance:
            raise ValueError('decoder and engine must share the same immutable instance')
        if n < 1:
            raise ValueError('n must be positive')
        wrapped = wrap_engine(engine_wrapper(engine) if engine_wrapper is not None else engine)
        return [MaskedDecoder(wrapped if i == 0 else wrapped.copy(), codec, mask_config) for i in range(n)]

    factory.name = 'irs-securing-necessary-envelope'
    return factory
