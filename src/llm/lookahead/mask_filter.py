"""Explicit opt-in future-capacity intersection over the existing mask.

Unprobed actions and unknown calculations remain legal. Copies have separate
mutable state and share only immutable geometry; no checker/engine is modified.
All future domains are optimistic geometric domains, never today's legal set.
"""
from __future__ import annotations

from dataclasses import dataclass
from time import monotonic

import numpy as np

from stowage.actions.expander import Expander
from stowage.actions.space import Discharge, Place
from stowage.feasibility import DeadEnd, IllegalActionError
from llm.lookahead.bounds import BoundResult, LookaheadConfig, OptimisticCapacity


_RULE = 'lookahead.necessary_future_capacity'


@dataclass(frozen=True)
class _Legal:
    flat: np.ndarray
    actions: tuple
    members: frozenset
    rejected: int
    reason: str | None
    certificate: dict


@dataclass
class _StateCache:
    """Results for one copied state; mutation detaches the owner first.

    Legal sets and bound results are read-only after construction. Sharing the
    holder also covers siblings copied before the first state query.
    """

    legal: _Legal | None = None
    state_result: BoundResult | None = None


def _native_contract(engine):
    """Recognize only reviewed wrappers over the restricted production executor."""
    from stowage.feasibility._engine import IncrementalEngine
    from llm.instructions.mask_filter import InstructionEngine
    from llm.securing.mask_filter import SecuringEngine
    current, seen = engine, set()
    while type(current) in (InstructionEngine, SecuringEngine, LookaheadEngine):
        if id(current) in seen:
            return False
        seen.add(id(current))
        current = current.inner
    return type(current) is IncrementalEngine and not current._freeze


class LookaheadEngine:
    """Consistent cached capacity filter for every public mask interface.

    ``capacity`` is an injectable necessary-bound policy for isolated tests.
    Real execution constructs SupportCapacity (or the explicit height-only
    ablation) and verifies the executor.
    Only this wrapper should mutate its inner; external mutations are detected
    on the next query by replaying the public action history.
    """

    def __init__(self, inner, *, config=None, capacity=None, expander=None):
        self.inner, self.instance = inner, inner.instance
        self.config = config or LookaheadConfig()
        if capacity is not None:
            self.capacity = capacity
        elif self.config.support_conditioned:
            from llm.lookahead.support_bounds import SupportCapacity
            self.capacity = SupportCapacity(self.instance,self.config,long_matching=False)
        else:
            self.capacity = OptimisticCapacity(self.instance,self.config)
        if getattr(self.capacity,'instance',self.instance) is not self.instance:
            raise ValueError('capacity policy and engine must share the immutable instance')
        self.native_contract = _native_contract(inner)
        self._actions = tuple(inner.actions())
        self.expander = expander if expander is not None else self._replay(self._actions)
        self.index_boxes = tuple(inner.index_boxes) if hasattr(inner,'index_boxes') else tuple(sorted(self.capacity.boxes))
        self.index_slots = tuple(inner.index_slots) if hasattr(inner,'index_slots') else tuple(self.capacity.slots)
        self.n_slots = len(self.index_slots)
        self._box_index = {cid:i for i,cid in enumerate(self.index_boxes)}
        self._slot_index = {slot:i for i,slot in enumerate(self.index_slots)}
        self._state_cache = _StateCache()
        self.metrics = dict(states_evaluated=0, candidate_probes=0, rejected_actions=0,
                            state_rejections=0, unknown_states=0, bound_seconds=0.,
                            deferred_reasons={})

    def __getattr__(self, name):
        return getattr(self.inner,name)

    def _replay(self, actions):
        ex = Expander(self.instance)
        for action in actions:
            ex.step(action)
        return ex

    @property
    def _cache(self):
        return self._state_cache.legal

    @_cache.setter
    def _cache(self, value):
        self._state_cache.legal = value

    @property
    def _state_result(self):
        return self._state_cache.state_result

    @_state_result.setter
    def _state_result(self, value):
        self._state_cache.state_result = value

    def _invalidate(self):
        self._state_cache = _StateCache()

    def _sync(self):
        actions = tuple(self.inner.actions())
        if actions != self._actions:
            self.expander = self._replay(actions)
            self._actions = actions
            self._invalidate()

    def _check(self, ex):
        before = monotonic()
        try:
            return self.capacity.check(ex,native_contract=self.native_contract)
        except Exception as error:
            return BoundResult('unknown',f'calculation:{type(error).__name__}')
        finally:
            self.metrics['bound_seconds'] += monotonic()-before

    def _state(self):
        self._sync()
        if self._state_result is None:
            self.metrics['states_evaluated'] += 1
            self._state_result = self._check(self.expander)
            if self._state_result.status == 'reject':
                self.metrics['state_rejections'] += 1
            elif self._state_result.status == 'unknown':
                self.metrics['unknown_states'] += 1
                reasons = self.metrics['deferred_reasons']
                reason = self._state_result.reason or 'unknown'
                reasons[reason] = reasons.get(reason,0)+1
        return self._state_result

    def _legal(self):
        self._sync()
        if self._cache is not None:
            return self._cache
        state = self._state()
        accepted, rejected, reason, certificate = [], 0, None, {}
        if state.status == 'reject':
            reason, certificate = state.reason, state.certificate
        elif not self.inner.is_complete():
            indexed = hasattr(self.inner,'legal_index') and hasattr(self.inner,'decode_index')
            if indexed:
                source = (self.inner.decode_index(int(flat)) for flat in self.inner.legal_index())
                source = [*source,*self.inner.legal_discharges()]
            else:
                source = self.inner.legal_actions()
            # Deterministic count caps preserve reproducible masks. Above the
            # tail cap, no action trials start; all existing filters still run.
            probe = state.status != 'unknown'
            probed = 0
            for action in source:
                if not self.inner.is_legal(action):
                    continue  # preserve instruction/securing intersections
                if (probe and isinstance(action,(Place,Discharge))
                        and probed < self.config.max_action_probes_per_state):
                    trial = self.expander.copy()
                    try:
                        trial.step(action)
                        result = self._check(trial)
                    except (ValueError, KeyError, TypeError, IndexError, ArithmeticError):
                        result = BoundResult('unknown','trial_expansion_unavailable')
                    probed += 1
                    self.metrics['candidate_probes'] += 1
                    if result.status == 'reject':
                        rejected += 1
                        reason, certificate = result.reason, result.certificate
                        continue
                accepted.append(action)
        actions = tuple(sorted(set(accepted),key=self.inner.space.sort_key))
        flat = np.asarray(sorted(self.index_of(action) for action in actions if isinstance(action,Place)),dtype=np.int64)
        flat.flags.writeable = False
        self.metrics['rejected_actions'] += rejected
        self._cache = _Legal(flat,actions,frozenset(actions),rejected,reason,certificate)
        return self._cache

    def legal_index(self):
        return self._legal().flat

    def legal_pairs(self):
        flat = self.legal_index()
        return flat//self.n_slots, flat%self.n_slots

    def decode_index(self, flat):
        return Place(self.index_boxes[int(flat)//self.n_slots],self.index_slots[int(flat)%self.n_slots])

    def index_of(self, action):
        if not isinstance(action,Place):
            return None
        b,s = self._box_index.get(action.box),self._slot_index.get(action.slot)
        return None if b is None or s is None else b*self.n_slots+s

    def legal_actions(self):
        return list(self._legal().actions)

    def legal_discharges(self):
        return tuple(action for action in self._legal().actions if not isinstance(action,Place))

    def legal_slots_for_box(self, box):
        return tuple(action.slot for action in self._legal().actions if isinstance(action,Place) and action.box == box)

    def legal_box_ids(self):
        return tuple(sorted({action.box for action in self._legal().actions if isinstance(action,Place)}))

    def is_legal(self, action):
        return action in self._legal().members

    def legal_candidates(self, candidates):
        return (action for action in candidates if action in self._legal().members)

    def iter_legal_actions(self, deadline=float('inf')):
        for action in self._legal().actions:
            if monotonic() >= deadline:
                break
            yield action

    def is_complete(self):
        self._sync()
        return self.inner.is_complete() and self._state().status != 'reject'

    def completion_check(self):
        method = getattr(self.inner,'completion_check',None)
        result = dict(method()) if method is not None else {}
        result['lookahead_necessary_capacity'] = (self._state().status != 'reject'
            and (self.inner.is_complete() or bool(self._legal().actions)))
        return result

    def dead_end(self):
        state = self._state()
        if state.status == 'reject':
            return DeadEnd(self.depth,'Necessary future capacity: '+(state.reason or 'deficit'),(_RULE,))
        dead = self.inner.dead_end()
        if dead is not None:
            return dead
        if not self.inner.is_complete() and not self._legal().actions:
            return DeadEnd(self.depth,'Necessary lookahead leaves no legal action'+
                           (': '+self._legal().reason if self._legal().reason else ''),
                           (_RULE,) if self._legal().rejected else ())
        return None

    def push(self, action):
        if not self.is_legal(action):
            raise IllegalActionError(f'Necessary lookahead rejects action: {action!r}')
        trial = self.expander.copy()
        trial.step(action)
        self.inner.push(action)
        self.expander, self._actions = trial,tuple(self.inner.actions())
        self._invalidate()

    def pop(self):
        action = self.inner.pop()
        self._actions = tuple(self.inner.actions())
        self.expander = self._replay(self._actions)
        self._invalidate()
        return action

    def copy(self):
        self._sync()
        # Geometry, index tables and cached results belong to this immutable
        # prefix. Branches own their inner engine/expander and detach results
        # on every mutation; no constructor/index reconstruction is needed.
        copied = object.__new__(LookaheadEngine)
        copied.__dict__.update(self.__dict__)
        copied.inner = self.inner.copy()
        copied.expander = self.expander.copy()
        copied.metrics = {**self.metrics,'deferred_reasons':dict(self.metrics['deferred_reasons'])}
        return copied

    def to_plan(self):
        if not self.is_complete():
            raise ValueError('to_plan requires completion under necessary lookahead')
        return self.inner.to_plan()

    def screen_diagnostics(self):
        self._sync()
        method = getattr(self.inner,'screen_diagnostics',None)
        result = dict(method()) if method is not None else {}
        current = self._cache
        result['lookahead'] = dict(self.metrics,policy='optimistic-future-cell-height-capacity',
            capacity_policy=type(self.capacity).__name__,
            config={name:getattr(self.config,name) for name in self.config.__dataclass_fields__},
            long_matching=getattr(self.capacity,'long_matching',False),
            native_executor_contract=self.native_contract,
            evaluated_current_state=current is not None,
            rejected_current_state=current.rejected if current is not None else 0,
            last_reason=current.reason if current is not None else None,
            last_certificate=current.certificate if current is not None else {})
        return result


def wrap_engine(engine, *, config=None):
    return LookaheadEngine(engine,config=config)


def lookahead_decoder_factory(*, engine_wrapper=None, securing=True, config=None,
                              securing_screen=None):
    """Opt-in production -> instructions -> lookahead -> securing decoders.

    Cheap capacity filters run before the outer securing policy's terminal IRS
    calls. Bounded action probes can cover different actions than in the old
    order, so both orders are necessary-only but need not expose equal sets.
    ``securing_screen`` optionally shares the existing instance-scoped cache;
    it changes neither its policy nor the independent engine state per sample.
    """
    from llm.stowlm.mask import MaskedDecoder
    from llm.securing import wrap_engine as securing_wrap

    def factory(instance, engine, codec, mask_config, n):
        if engine.instance is not instance:
            raise ValueError('decoder and engine must share the immutable instance')
        if n < 1:
            raise ValueError('n must be positive')
        wrapped = engine_wrapper(engine) if engine_wrapper is not None else engine
        wrapped = wrap_engine(wrapped,config=config)
        if securing:
            wrapped = securing_wrap(wrapped,screen=securing_screen)
        return [MaskedDecoder(wrapped if index == 0 else wrapped.copy(),codec,mask_config)
                for index in range(n)]
    factory.name = 'necessary-future-capacity-before-securing'
    return factory
