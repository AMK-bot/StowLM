"""Feasibility mask (plan 5.1): (partial token sequence, engine state) -> allowed next token IDs.

**What a feasibility mask is.** A language model picks the next token from a probability distribution over its whole
vocabulary (~200k tokens). *Constrained decoding* removes, before sampling, every token that could not continue a
valid output: the disallowed logits are set to -inf, so their probability is exactly zero and the model renormalises
over what is left. Here the next action must pass the feasibility engine's (4.4) local move/static rules and
necessary completion screens. These screens do not prove that a completion exists (D23/D49): later dead ends and
full-checker failures remain possible. The ballast finisher and full checker (4.1), including both D50 voyage
conditions, judge the finished plan.
Sources: Willard & Louf, "Efficient Guided Generation for LLMs" (2023, https://arxiv.org/abs/2307.09702) for
token masks from a grammar/automaton; Hugging Face ``LogitsProcessor`` docs for the logits-processor idea.

**From actions to tokens.** The engine speaks in actions (``Place(box, slot)``); the model speaks in tokens. One
action is 7 tokens (``P`` ``000`` `` `` ``001`` ``01`` ``82`` ``\\n``, format 4.5). The set of allowed next tokens
is read off a *prefix trie* (a tree of shared token prefixes) over the encoded legal actions: walk the tokens of the
current action written so far, the children of that node are the allowed tokens. Materialising the trie over every
legal action costs seconds on a full feeder (34k actions in early states, ``experiments/format``), so it is built
**per field, lazily**:

    level 1 (eager, per state): a trie over action *heads* = opcode + box index + first separator, one entry per
            distinct (op, box) — at most a few hundred;
    level 2 (lazy, per head): a trie over the remaining fields (bay, row, tier, newline) restricted to that box's
            legal slots, built only when the model has actually written that head.

The state is read through the engine's index arrays (engine v2.3: ``legal_index()`` = sorted flat
``box * n_slots + slot`` integers, plus ``legal_discharges()``), never through ``legal_actions()``, which would build
~56k action objects in an early full-ship state: the heads come from ``np.unique`` over the box part, a tail is the
slice of that box's slot indices, and an action object is made (``decode_index``) only for the slots of the box the
model has written. Every field's token IDs come from a string → IDs cache filled with the shared 4.5 builder, so a cached action's
tokens equal ``ActionCodec.encode_action`` (asserted in tests). The per-state structures live on a stack that
follows the engine's push/pop, so revisiting a state (or reading the mask twice) costs nothing.

With rule-freeze-v1 active, Stage A and lookahead off, the first level instead
uses ``legal_box_ids()`` and a chosen head uses ``legal_slots_for_box()``. Both
are projections of the same engine legal set: no E6, weight-class or crossing
policy is duplicated here. This avoids screening unchosen box/slot pairs.
Stage B, reference mode and lookahead retain the complete index/action path.
An explicit ``n_legal`` query still evaluates the exact complete action count;
trace writers requesting that count should include its cost in their timing.

**Dead ends and the lookahead.** The engine is sound but not complete with respect to *completion* (C1/C2 are
necessary conditions), so some legal actions lead to a state with no legal action at all: a dead end
(``experiments/feasibility``). Once a token is fed to the model it can't be cheaply taken back in batched
generation, so the mask can look one action ahead where that is affordable (``MaskConfig.lookahead``, off by
default — measured to buy nothing under random sampling): whenever at most ``lookahead`` candidate
actions remain under the current prefix (typically after the box and bay are written), each candidate is pushed on
an engine copy and dropped if its successor state is a dead end. The successor engines are kept, so the chosen
action costs nothing extra. This rule depends only on (state, prefix, lookahead), so the mask is a deterministic
function — GRPO can recompute the same allowed sets (D9). If no allowed token remains, the decoder reports a dead
end (``status == "dead_end"``, with the engine's ``DeadEnd``) and the prefix goes to repair (5.7): the partial plan
is ``decoder.actions`` (complete actions only; tokens of a half-written action are not part of it).

No physics is approximated here: the mask is exactly the engine's legal set minus actions *proved* (by the
engine's own model) to lead to an immediate dead end. Lookahead is one action deep and only applied below
``lookahead`` candidates; deeper dead ends are reported, not avoided.

Behavior log-probabilities under the mask (D9): for temperature T > 0, log-softmax over the allowed scaled logits,
i.e. ``logit[t]/T - logsumexp(logit[allowed]/T)``. At T <= 0, greedy sampling gives its deterministic chosen token
log-probability 0 and every other token -inf. ``mask_mlx.masked_logprobs`` replays this distribution from
``allowed_sets`` when given the same temperature.
"""

from __future__ import annotations

from collections.abc import Sequence
from copy import deepcopy
from dataclasses import dataclass
from typing import Any
from weakref import WeakValueDictionary

import numpy as np

from llm.stowlm.format import ActionCodec, build_sequence
from stowage.actions.space import Action, Place
from stowage.feasibility import DeadEnd, FeasibilityEngine
from stowage.rules.catalogue import rule_freeze_v1_enabled

MASK_VERSION = "stowlm-mask@1.2"  # propose 1.3 with the leader's coordinated activation/image-pin bump
_DEAD_STATE_RULE_LABEL = "rules active at the dead state"


@dataclass(frozen=True)
class MaskConfig:
    """``lookahead``: screen candidates for immediate dead ends once at most this many actions remain under the
    current prefix (0 = never; the mask is then exactly the engine's legal set). Default 0: under random sampling a
    lookahead of 8 cost 3–4× the mask CPU time and did not change the dead-end rate or depth (dead ends come from
    earlier choices; ``experiments/mask/README.md``). ``index_arrays``: read the state through the engine's
    ``legal_index()`` / ``legal_discharges()`` (no action objects; engines without them fall back automatically);
    False builds from ``legal_actions()`` — same allowed sets, kept as the reference path for tests.
    On activation, the default Stage A path uses the engine's lazy box/slot
    domains when lookahead is off; exact ``n_legal`` metadata remains eager."""

    lookahead: int = 0
    index_arrays: bool = True  # build from the engine's index arrays (v2.3); False = from legal_actions() (tests)


class MaskError(ValueError):
    """A token outside the allowed set was fed to ``MaskedDecoder.advance``."""


class _Node:
    """Trie node. ``count`` = candidate actions below before lookahead pruning, ``alive`` = after. A head-trie leaf
    carries ``group`` (one head and its actions); a tail-trie leaf carries ``action``."""

    __slots__ = ("children", "count", "alive", "group", "action", "screened")

    def __init__(self) -> None:
        self.children: dict[int, _Node] = {}
        self.count = 0
        self.alive = 0
        self.group: _Group | None = None
        self.action: Action | None = None
        self.screened = False


class _Group:
    """All legal actions sharing one head (op, box, first separator); ``tail`` (the trie over the remaining fields)
    is built on first visit only. Either explicit ``actions`` or, from the index arrays, ``flats`` (flat
    ``box * n_slots + slot`` indices of one box's legal places) decoded only when the tail is built."""

    __slots__ = ("actions", "flats", "tail", "box")

    def __init__(self, first: Action | None = None, flats: Any = None, box: str | None = None) -> None:
        self.actions: list[Action] = [first] if first is not None else []
        self.flats = flats
        self.tail: _Node | None = None
        self.box = box  # activated lazy domain: resolve slots only after this head is written

    def __len__(self) -> int:
        return len(self.flats) if self.flats is not None else len(self.actions)


def _insert(root: _Node, ids: Sequence[int]) -> _Node:
    """Insert one path, adding 1 to every count on it; returns the leaf."""
    node = root
    node.count += 1
    for t in ids:
        nxt = node.children.get(t)
        if nxt is None:
            nxt = node.children[t] = _Node()
        node = nxt
        node.count += 1
    return node


def _finish_counts(node: _Node) -> None:
    for child in node.children.values():
        _finish_counts(child)
    node.alive = node.count


class _TokenCache:
    """Field string → token IDs (the 4.5 builder applied per field: no cross-field merges), and action → IDs."""

    def __init__(self, codec: ActionCodec) -> None:
        self.codec = codec
        self.fields: dict[str, tuple[int, ...]] = {}
        self.actions: dict[Action, tuple[tuple[int, ...], int]] = {}
        self._tables: dict[tuple, tuple[list, list]] = {}
        self._last_tables: tuple[tuple, tuple[list, list]] | None = None

    def index_tables(self, engine: Any) -> tuple[list, list]:
        """For an engine with index arrays: (head IDs per engine box index, tail IDs per engine slot index), from
        the same ``action_fragments`` as ``encode`` (head = fragments[:3], tail = fragments[3:] of a Place)."""
        key = (engine.index_boxes, engine.index_slots)
        # Wrapper copies retain these immutable index tuples. Avoid hashing
        # every box and slot again when building each chosen box's tail.
        last = self._last_tables
        if last is not None and key[0] is last[0][0] and key[1] is last[0][1]:
            return last[1]
        hit = self._tables.get(key)
        if hit is None:
            boxes, slots = key
            known = self.codec.box_index.by_id
            any_slot, any_box = slots[0], next(b for b in boxes if b in known)
            heads = [tuple(t for f in self.codec.action_fragments(Place(b, any_slot))[:3] for t in self.field_ids(f))
                     if b in known else None for b in boxes]
            tails = [tuple(t for f in self.codec.action_fragments(Place(any_box, sl))[3:] for t in self.field_ids(f))
                     for sl in slots]
            hit = self._tables[key] = (heads, tails)
        self._last_tables = (key, hit)
        return hit

    def field_ids(self, text: str) -> tuple[int, ...]:
        ids = self.fields.get(text)
        if ids is None:
            ids = build_sequence(self.codec.tokenizer, [text])
            self.fields[text] = ids
        return ids

    def encode(self, action: Action) -> tuple[tuple[int, ...], int]:
        """(all token IDs of the action, length of its head in tokens). Head = opcode, index, first separator."""
        hit = self.actions.get(action)
        if hit is None:
            parts = [self.field_ids(f) for f in self.codec.action_fragments(action)]
            hit = (tuple(t for p in parts for t in p), sum(len(p) for p in parts[:3]))
            self.actions[action] = hit
        return hit


# Only token encodings are shared: tries, legal sets, engine states and prefix
# pruning remain per decoder. A live cache retains its codec, so its identity
# cannot be reused. Weak values release all memoized encodings after the last
# sibling decoder/cache is released, even if a caller retains the codec.
_TOKEN_CACHES: WeakValueDictionary[int, _TokenCache] = WeakValueDictionary()


def _token_cache(codec: ActionCodec) -> _TokenCache:
    cache = _TOKEN_CACHES.get(id(codec))
    if cache is None:
        cache = _TokenCache(codec)
        _TOKEN_CACHES[id(codec)] = cache
    return cache


class _StateView:
    """The mask structures of one engine state: the head trie, lazily built tails, and the successor engines that
    the lookahead (or the chosen action) produced."""

    __slots__ = ("engine", "root", "complete", "dead_end", "succ", "next_views", "n_legal", "built", "lazy")

    def __init__(self, engine: FeasibilityEngine) -> None:
        self.engine = engine
        self.built = False  # built on first query, so a decoding loop can do it while the GPU runs
        self.succ: dict[Action, FeasibilityEngine] = {}
        self.next_views: dict[Action, _StateView] = {}  # successor views built ahead by ``prefetch``

    def build(self, cache: _TokenCache, use_index: bool = True, lazy: bool = False) -> _StateView:
        if self.built:
            return self
        self.built = True
        engine = self.engine
        self.complete = engine.is_complete()
        self.dead_end: DeadEnd | None = None
        self.root = _Node()
        self.n_legal = 0
        self.lazy = False
        if self.complete:
            leaf = _insert(self.root, cache.codec.end_plan_tokens)
            leaf.screened = True
            _finish_counts(self.root)
            return self
        if (lazy and use_index and rule_freeze_v1_enabled()
                and hasattr(engine, "legal_box_ids") and hasattr(engine, "legal_slots_for_box")
                and hasattr(engine, "index_slots")
                and getattr(getattr(engine, "instance", None), "stage", None) == "A"
                and not engine.instance.discharge_list):
            self._build_from_boxes(cache)
            return self
        if use_index and hasattr(engine, "legal_index"):
            self._build_from_index(cache)
            return self
        legal = engine.legal_actions()
        self.n_legal = len(legal)
        if not legal:
            self.dead_end = engine.dead_end() or DeadEnd(engine.depth, "no legal action and the plan is not complete")
            return self
        # Legal actions are sorted by (op, box, target) so one head is one contiguous run: encode only the first
        # action of each run here; the rest of the run is encoded when (if) the model writes that head.
        groups: list[tuple[tuple[int, ...], _Group]] = []
        prev = None
        for a in legal:
            key = (a.__class__, a.box, (a.slot if a.__class__ is Place else getattr(a, "to", None)) is None)
            if key != prev:
                ids, head_len = cache.encode(a)
                groups.append((ids[:head_len], _Group(a)))
                prev = key
            else:
                groups[-1][1].actions.append(a)
        for head, group in groups:
            node = self.root
            node.count += len(group.actions)
            for t in head:
                nxt = node.children.get(t)
                if nxt is None:
                    nxt = node.children[t] = _Node()
                node = nxt
                node.count += len(group.actions)
            if node.group is not None:  # defensive: the same head in two runs
                node.group.actions.extend(group.actions)
            else:
                node.group = group
        _finish_counts(self.root)
        return self

    def _build_from_boxes(self, cache: _TokenCache) -> None:
        """The engine owns both projections, including all C1/C2/move screens.

        Counts here measure nonempty heads, not actions. Exact action counts are
        evaluated only on explicit ``n_legal`` requests. Lookahead uses the eager
        path; prefetch resolves a head before deciding whether it is determined.
        Stage B retains the index path because legal_discharges evaluates all pairs.
        """
        engine = self.engine
        boxes = engine.legal_box_ids()
        self.lazy = True
        self.n_legal = None
        if not boxes:
            self.n_legal = 0
            self.dead_end = engine.dead_end() or DeadEnd(engine.depth, "no legal action and the plan is not complete")
            return
        for box in boxes:
            ids, head_len = cache.encode(Place(box, engine.index_slots[0]))
            _insert(self.root, ids[:head_len]).group = _Group(box=box)
        _finish_counts(self.root)

    def _build_from_index(self, cache: _TokenCache) -> None:
        """Head trie from the index arrays: one head per distinct legal box (``np.unique`` over the sorted flat
        array, so each box is one contiguous run); slots stay as an index slice until that head is written."""
        engine = self.engine
        flat = engine.legal_index()
        discharges = engine.legal_discharges()
        self.n_legal = len(flat) + len(discharges)
        if not self.n_legal:
            self.dead_end = engine.dead_end() or DeadEnd(engine.depth, "no legal action and the plan is not complete")
            return
        groups: list[tuple[tuple[int, ...], _Group]] = []
        if len(flat):
            heads, _ = cache.index_tables(engine)
            boxes = flat // engine.n_slots
            uniq, start = np.unique(boxes, return_index=True)
            ends = list(start[1:]) + [len(flat)]
            for b, lo, hi in zip(uniq.tolist(), start.tolist(), ends):
                groups.append((heads[b], _Group(flats=flat[lo:hi])))
        prev = None
        for a in discharges:  # few (stage B): through the action path
            key = (a.__class__, a.box, getattr(a, "to", None) is None)
            if key != prev:
                ids, head_len = cache.encode(a)
                groups.append((ids[:head_len], _Group(a)))
                prev = key
            else:
                groups[-1][1].actions.append(a)
        for head, group in groups:
            n = len(group)
            node = self.root
            node.count += n
            for t in head:
                nxt = node.children.get(t)
                if nxt is None:
                    nxt = node.children[t] = _Node()
                node = nxt
                node.count += n
            node.group = group  # heads are distinct: one per box (places) or per discharge run
        _finish_counts(self.root)


class MaskedDecoder:
    """Framework-neutral, per-sequence mask state machine (one generated plan).

    ``allowed_token_ids()`` → sorted allowed next IDs (empty iff finished or dead end); ``advance(t)`` feeds one
    sampled token and returns the action it completed (or None). Completing an action pushes it on the engine;
    completing the end-of-plan sequence sets ``status = "complete"``. ``status = "dead_end"`` once nothing is
    allowed and the plan is not complete (``dead_end`` says why). Deterministic: same state + same tokens → same
    allowed sets, no RNG. The decoder owns the engine passed in (pass ``engine.copy()`` to keep yours).
    """

    def __init__(self, engine: FeasibilityEngine, codec: ActionCodec, config: MaskConfig | None = None, *,
                 token_cache: _TokenCache | None = None) -> None:
        self.codec = codec
        self.config = config or MaskConfig()
        self._cache = token_cache if token_cache is not None else _token_cache(codec)
        self._stack: list[_StateView] = [_StateView(engine)]
        self._prefix: list[int] = []  # tokens of the current, unfinished action
        self._node: _Node | None = None  # None = at the root of a state not built yet
        self._allowed: tuple[int, ...] | None = None
        self.tokens: list[int] = []  # every plan token fed so far
        self.status = "running"
        self.dead_end: DeadEnd | None = None
        self.lookahead_evals = 0  # successor states built by the lookahead

    @property
    def engine(self) -> FeasibilityEngine:
        return self._stack[-1].engine

    @property
    def actions(self) -> list[Action]:
        return self.engine.actions()

    @property
    def n_legal(self) -> int:
        """Size of the engine's legal set at the current state."""
        view = self._view()
        if view.n_legal is None:
            view.n_legal = len(view.engine.legal_index()) + len(view.engine.legal_discharges())
        return view.n_legal

    def diagnostics(self) -> dict[str, Any]:
        """JSON-ready current-prefix screen counts and terminal rule attribution.

        Reading diagnostics does not enumerate untouched box/slot domains. Pair
        counts may therefore be partial; they are not counts of rejected actions.
        """
        dead = self.dead_end
        screens = getattr(self.engine, "screen_diagnostics", lambda: {})()
        terminal = dict(depth=dead.depth, reason=dead.reason, rule_ids=list(dead.rule_ids)) if dead else None
        if rule_freeze_v1_enabled():
            if terminal is not None:
                terminal["rule_ids_label"] = screens.get("dead_end_rule_ids_label", _DEAD_STATE_RULE_LABEL)
        return deepcopy(dict(status=self.status, depth=self.engine.depth,
            dead_end=terminal,
            dead_ends_by_rule={rule: 1 for rule in sorted(set(dead.rule_ids))} if dead else {},
            screens=screens))

    def _view(self) -> _StateView:
        view = self._stack[-1].build(self._cache, self.config.index_arrays, self.config.lookahead <= 0)
        if self._node is None:
            self._node = view.root
        return view

    def allowed_token_ids(self) -> tuple[int, ...]:
        if self.status != "running":
            return ()
        if self._allowed is None:
            view = self._view()
            if view.dead_end is not None:
                self._fail(view.dead_end)
                return ()
            node = self._node
            screened = self._screen(node)
            allowed = tuple(sorted(t for t, c in node.children.items() if (c.alive if screened else c.count) > 0))
            if not allowed:
                self._fail(self._lookahead_dead_end())
            self._allowed = allowed
        return self._allowed

    def advance(self, token_id: int) -> Action | None:
        """Feed one token; returns the action it completed, if any. Raises MaskError if it isn't allowed."""
        if token_id not in self.allowed_token_ids():
            raise MaskError(f"token {token_id} not allowed after {self._prefix} (status {self.status})")
        self.tokens.append(token_id)
        self._prefix.append(token_id)
        self._allowed = None
        node = self._node.children[token_id]
        if self._view().complete:
            self._node = node
            if not node.children:
                self.status = "complete"
            return None
        if node.group is not None:  # head written: continue in this box's tail trie
            node = self._tail(node.group)
        self._node = node
        if node.action is not None:
            return self._commit(node.action)
        return None

    def _tail(self, group: _Group) -> _Node:
        if group.tail is None:
            tail = _Node()
            if group.box is not None:
                engine = self._stack[-1].engine
                group.actions = [Place(group.box, slot) for slot in engine.legal_slots_for_box(group.box)]
            if group.flats is not None:
                engine = self._stack[-1].engine
                _, tails = self._cache.index_tables(engine)
                n_slots = engine.n_slots
                for f in group.flats.tolist():
                    _insert(tail, tails[f % n_slots]).action = engine.decode_index(f)
            for a in group.actions:
                ids, head_len = self._cache.encode(a)
                _insert(tail, ids[head_len:]).action = a
            _finish_counts(tail)
            group.tail = tail
        return group.tail

    def _commit(self, action: Action) -> Action:
        view = self._stack[-1]
        ahead = view.next_views.pop(action, None)
        if ahead is not None:
            view.next_views.clear()
            self._stack.append(ahead)
            self._prefix = []
            self._node = None
            return action
        nxt = view.succ.get(action)
        if nxt is None:
            nxt = view.engine.copy()
            nxt.push(action)
        self._stack.append(_StateView(nxt))
        self._prefix = []
        self._node = None
        return action

    def prefetch(self) -> bool:
        """If the current action is already determined (one candidate left under the written prefix, e.g. only
        the tier and newline remain), push it on an engine copy and build that successor state's mask now.

        A pure speed-up for lock-step batches: every sequence reaches an action boundary on the same step, so
        without it all the engine evaluations land in one GPU step; with it they spread over the steps of the
        forced tokens. It never changes an allowed set. Call after ``allowed_token_ids()``; returns True if it did
        work."""
        if self.status != "running" or self._node is None or self._node.count != 1:
            return False
        view = self._stack[-1]
        if not view.built or view.complete:
            return False
        leaf = self._node
        while not (leaf.action is not None and not leaf.children):
            if leaf.count != 1:
                return False
            if leaf.group is not None:
                leaf = self._tail(leaf.group)
                continue
            (leaf,) = leaf.children.values()
        a = leaf.action
        if a in view.next_views:
            return False
        e = view.succ.get(a)
        if e is None:
            e = view.engine.copy()
            e.push(a)
            view.succ[a] = e
        view.next_views[a] = _StateView(e).build(self._cache, self.config.index_arrays, self.config.lookahead <= 0)
        return True

    def pop_action(self) -> Action:
        """Undo the last committed action (CPU search and tests; batched LM decoding can't un-feed tokens). Drops
        the tokens of the current partial action and of the popped action."""
        if len(self._stack) < 2:
            raise IndexError("no action to pop")
        view = self._stack.pop()
        action = view.engine.actions()[-1]
        ids, _ = self._cache.encode(action)
        del self.tokens[len(self.tokens) - len(self._prefix) - len(ids):]
        self._prefix = []
        self._node = None
        self._allowed = None
        self.status = "running"
        self.dead_end = None
        return action

    # ------------------------------------------------------------------ lookahead
    def _screen(self, node: _Node) -> bool:
        """True if the one-action lookahead applies at ``node`` (≤ ``lookahead`` raw candidates below it); then
        every candidate's successor is built once and dead ones get ``alive = 0``."""
        k = self.config.lookahead
        view = self._view()
        if k <= 0 or node.count > k or view.complete:
            return False
        if not node.screened:

            def rec(n: _Node) -> int:
                n.screened = True
                if n.group is not None:
                    n.alive = rec(self._tail(n.group))
                    return n.alive
                if n.action is not None:
                    a = n.action
                    e = view.succ.get(a)
                    if e is None:
                        e = view.engine.copy()
                        e.push(a)
                        view.succ[a] = e
                        self.lookahead_evals += 1
                    n.alive = 1 if (e.is_complete() or _has_legal(e)) else 0
                    return n.alive
                n.alive = sum(rec(c) for c in n.children.values())
                return n.alive

            rec(node)
        return True

    def _lookahead_dead_end(self) -> DeadEnd:
        view = self._stack[-1]
        rules: set[str] = set()
        for e in view.succ.values():
            d = e.dead_end()
            if d is not None:
                rules.update(d.rule_ids)
        return DeadEnd(view.engine.depth, f"every candidate under the written prefix ({len(view.succ)} screened) "
                       "leads to a dead end (one-action lookahead)", tuple(sorted(rules)))

    def _fail(self, dead: DeadEnd) -> None:
        if rule_freeze_v1_enabled():
            # Local screens can empty the domain without the provider copying
            # their causes into DeadEnd (whose rule_ids are optional). Attribute
            # the terminal state using its public screen evidence as well. This
            # is a union of active rules, not a minimal cause or an action count.
            screens = getattr(self.engine, "screen_diagnostics", lambda: {})()
            active = {rule for rule, count in screens.get("rejected", {}).items() if count > 0}
            dead = DeadEnd(dead.depth, dead.reason, tuple(sorted(set(dead.rule_ids) | active)))
        self.status = "dead_end"
        self.dead_end = dead


def allowed_sets(engine: FeasibilityEngine, codec: ActionCodec, plan_tokens: Sequence[int],
                 config: MaskConfig | None = None) -> list[tuple[int, ...]]:
    """Replay ``plan_tokens`` under the mask from ``engine`` (not modified): the allowed set before each token.
    Raises MaskError if a token is not allowed. Used for masked log-probs (eval, GRPO; D9)."""
    dec = MaskedDecoder(engine.copy(), codec, config)
    sets = []
    for t in plan_tokens:
        sets.append(dec.allowed_token_ids())
        dec.advance(t)
    return sets


def allowed_token_ids(engine: FeasibilityEngine, codec: ActionCodec, partial_action: Sequence[int] = (),
                      config: MaskConfig | None = None) -> tuple[int, ...]:
    """One-shot form of the mask: allowed next IDs at ``engine``'s state after the tokens ``partial_action`` of the
    current, unfinished action. Builds fresh structures each call; decoding loops should keep a MaskedDecoder."""
    dec = MaskedDecoder(engine.copy(), codec, config)
    for t in partial_action:
        if dec.advance(t) is not None:
            raise MaskError("partial_action completes an action; push it on the engine instead")
    return dec.allowed_token_ids()


def random_masked_plan(engine: FeasibilityEngine, codec: ActionCodec, rng: Any, config: MaskConfig | None = None,
                       *, max_tokens: int = 1_000_000) -> MaskedDecoder:
    """Uniform random sampling over allowed tokens at every step (no model). ``rng`` is a ``random.Random``.
    A ``complete`` decoder has emitted all actions and the end marker; it is not checker approval. Finalize its
    plan with the ballast finisher and run the full checker before treating it as feasible. A token cap can also
    leave the decoder ``running``; it must not be counted as a pass or a dead end."""
    dec = MaskedDecoder(engine.copy(), codec, config)
    for _ in range(max_tokens):
        allowed = dec.allowed_token_ids()
        if not allowed:
            break
        dec.advance(allowed[rng.randrange(len(allowed))])
    return dec


def _has_legal(engine: FeasibilityEngine) -> bool:
    if hasattr(engine, "legal_index"):
        return len(engine.legal_index()) > 0 or len(engine.legal_discharges()) > 0
    return bool(engine.legal_actions())
