"""Exact demand-driven mask replay with memoized immutable action prefixes.

Only the existing engine's evaluation domain changes. All placement/completion
rules still execute. Outside the guarded legacy pure-load domain use _mask_ok.
"""
from __future__ import annotations

from collections import OrderedDict
from dataclasses import replace

import numpy as np

from alns import _mask_ok
from stowage.actions.space import Place
from stowage.feasibility._engine import IncrementalEngine
from stowage.frozen import active_stubs
from stowage.rules.catalogue import rule_freeze_v1_enabled


class PrefixMask:
    """Callable exact mask oracle for one unmodified Problem, preserving its state.

    Snapshots share the engine's immutable state arrays; cached nodes are keyed
    by the entire ordered prefix, including box identity and slot identity.
    """

    def __init__(self, problem, *, cache_size=4096):
        self.problem = problem
        self.original_engine = problem.engine
        self.root = getattr(problem.engine, '_cur', None)
        self.config = repr(getattr(problem.engine, 'config', None))
        self.cache_size = max(0, int(cache_size))
        self.cache = OrderedDict()
        self.rejected = OrderedDict()
        self.stats = dict(calls=0, fallback=0, evaluated_pairs=0,
                          prefix_hits=0, reused_actions=0, rejected_hits=0)
        self.engine = problem.engine.copy() if type(problem.engine) is IncrementalEngine else None

    def supported(self, actions):
        p, e = self.problem, self.original_engine
        return (self.engine is not None and p.engine is e and e.depth == 0 and e._cur is self.root
                and not e._freeze and not rule_freeze_v1_enabled() and not active_stubs()
                and e._global is None and e._global_error is None
                and repr(e.config) == self.config and not p.instance.discharge_list
                and len(actions) == len(p.instance.load_list)
                and all(type(a) is Place for a in actions)
                and {a.box for a in actions} == set(p.instance.load_list)
                and len({a.box for a in actions}) == len(actions))

    def _remember(self, cache, key, value):
        if self.cache_size:
            cache[key] = value
            cache.move_to_end(key)
            while len(cache) > self.cache_size:
                cache.popitem(last=False)

    def check(self, actions):
        actions = tuple(actions)
        self.stats['calls'] += 1
        if not self.supported(actions):
            self.stats['fallback'] += 1
            return _mask_ok(self.problem, actions)
        # The work engine owns caches/counters but never changes the caller's
        # engine cursor or history. Rebuild history from the exact cached path.
        e = self.engine
        keys = [actions[:i] for i in range(len(actions)+1)]
        for key in keys[1:]:
            if key in self.rejected:
                self.rejected.move_to_end(key)
                self.stats['rejected_hits'] += 1
                return False
        longest = next((i for i in range(len(actions), 0, -1) if keys[i] in self.cache), 0)
        if longest:
            self.stats['prefix_hits'] += 1
            self.stats['reused_actions'] += longest
            st = self.cache[keys[longest]]
            self.cache.move_to_end(keys[longest])
            history = []
            cursor = st
            while cursor is not self.root:
                history.append(cursor.parent)
                cursor = cursor.parent
            e._history, e._cur = list(reversed(history)), st
        else:
            e._history, e._cur = [], self.root
        try:
            for i in range(longest, len(actions)):
                a, st = actions[i], e._cur
                b = e.T.box_index.get(a.box)
                s = e.T.slot_index.get(a.slot)
                if b is None or s is None:
                    self._remember(self.rejected, keys[i+1], True)
                    return False
                # Owner _lazy_pair uses this same restricted probe in freeze
                # mode. E9's all_boxes refers to boxes already aboard; the
                # candidate axis is independent in both E9 branches.
                if st.evaluated:
                    legal = e.is_legal(a)
                else:
                    probe = replace(st, legal=None, mask=None, dead=None, evaluated=False)
                    e._evaluate(probe, allowed_boxes=np.array([b]), allowed_slots=np.array([s]),
                                include_discharges=False)
                    legal = bool(len(probe.mask))
                    # These are immutable-prefix caches, independent of the
                    # requested candidate domain. Preserve incremental C1 flow
                    # repair on the child's dirty stacks.
                    for name in ('_capacity', '_c1g_answers', '_dgfree', '_e6_info',
                                 '_env', '_killed_cells'):
                        if hasattr(probe, name):
                            setattr(st, name, getattr(probe, name))
                    st.dgF = probe.dgF
                    st.completion = probe.completion
                    self.stats['evaluated_pairs'] += 1
                if not legal:
                    self._remember(self.rejected, keys[i+1], True)
                    return False
                # push() repeats is_legal; _child is exactly its state update.
                child = e._child(st, a)
                e._history.append(st)
                e._cur = child
                self._remember(self.cache, keys[i+1], child)
            return e.is_complete()
        finally:
            e._history, e._cur = [], self.root

    def __call__(self, *args):
        """Accept either oracle(actions) or legacy mask(problem, actions)."""
        if len(args) == 2:
            problem, actions = args
            if problem is not self.problem:
                return _mask_ok(problem, actions)
        else:
            actions, = args
        return self.check(actions)
