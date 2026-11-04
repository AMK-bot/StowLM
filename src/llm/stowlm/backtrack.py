"""Backtracking masked decoding (v4 pass-rate work, leader 29 Oct 18:40).

The 5.1 mask guarantees every emitted action is legal *now*, but a legal prefix can still reach a state with no legal
action (a dead end: e.g. "no box fits any open slot"). Plain decoding stops there. Here, on a dead end at action depth
``t`` the decoder rewinds to depth ``t - k`` (``k = 1, 2, 4, ...`` within one dead-end episode), **forbids exactly the
action that was taken at that depth** and decodes again from there: greedy then takes the next-best legal choice,
sampling resamples. Total rewinds per plan are capped (``max_backtracks``); every rewind is reported.

This is depth-first search with chronological backjumping over the model's own ranking (cf. Russell & Norvig, AIMA,
ch. 6 "Backtracking search for CSPs"). It changes nothing about legality: every action still passes the full mask.

Forbidding one action exactly: actions are token paths in the mask's trie. At the current partial action prefix P, a
next token ``t`` is excluded when every action under ``P + t`` is forbidden at this state (trie counts are action
counts), so no other action is lost.

KV cache: after a rewind the cache is trimmed to the kept tokens when mlx-lm allows it (``can_trim_prompt_cache``;
Glimmer's sliding-window layers allow it only before the window wraps), else the prompt plus the kept plan tokens are
re-prefilled. Batch 1, one sample at a time.
"""
from __future__ import annotations

import math
import time
from typing import Any, Sequence

from llm.stowlm.mask_mlx import SequenceResult, prefill


class ForbiddingDecoder:
    """Wraps a mask decoder (``MaskedDecoder`` or a transparent wrapper of one) with per-state forbidden actions."""

    def __init__(self, inner: Any) -> None:
        self.inner = inner
        self.forbidden: dict[tuple, set] = {}  # state key (committed actions) -> forbidden next actions
        self._encoded: dict[Any, tuple[int, ...]] = {}

    def __getattr__(self, name: str):  # status, dead_end, tokens, actions, engine, steps, ...
        return getattr(self.inner, name)

    def _base(self):
        d = self.inner
        while "inner" in vars(d):  # unwrap transparent wrappers (they forward reads, so hasattr would lie)
            d = d.inner
        return d

    def _ids(self, action) -> tuple[int, ...]:
        ids = self._encoded.get(action)
        if ids is None:
            ids = self._encoded[action] = tuple(self._base()._cache.encode(action)[0])
        return ids

    def state_key(self) -> tuple:
        return tuple(self.inner.actions)

    def allowed_token_ids(self) -> tuple[int, ...]:
        allowed = self.inner.allowed_token_ids()
        banned = self.forbidden.get(self.state_key()) if allowed else None
        if not banned:
            return allowed
        base = self._base()
        prefix = list(base._prefix)
        node = base._node
        out = []
        for t in allowed:
            path = prefix + [t]
            hits = sum(1 for a in banned if self._ids(a)[:len(path)] == tuple(path))
            if hits == 0:
                out.append(t)
                continue
            child = node.children.get(t) if node is not None else None
            if child is None:
                continue
            if not hasattr(child, "group"):  # not a mask trie node (test doubles): action counts by contract
                if (child.alive if child.screened else child.count) > hits:
                    out.append(t)
            elif self._has_unbanned(child, banned):  # other actions remain under this token
                out.append(t)
        return tuple(out)

    def _has_unbanned(self, node, banned) -> bool:
        """Whether any live action other than a banned one lies below ``node``, by walking the trie (rev-v4-h P2).
        Trie counts can't be used: the lazy box-head trie counts nonempty heads, not actions, so a head holding a
        banned placement and other legal slots would look exhausted. A head node's tail (that box's slots) is
        resolved on demand, as ``advance`` would; any other head returns at once (lazy heads are nonempty)."""
        base = self._base()
        stack = [node]
        while stack:
            n = stack.pop()
            if n.screened and n.alive == 0:
                continue  # pruned by the mask's own lookahead
            if n.group is not None:
                stack.append(base._tail(n.group))
                continue
            if n.action is not None and not n.children:
                if n.action not in banned:
                    return True
                continue
            if not n.children and n.count > 0:
                return True  # a terminal (e.g. end-of-plan) leaf that carries no action can't be banned
            stack.extend(n.children.values())
        return False

    def advance(self, token_id: int):
        return self.inner.advance(token_id)

    def prefetch(self) -> bool:
        return False

    def rewind(self, k: int) -> int:
        """Pop ``k`` committed actions (fewer at the root) plus the partial action; forbid the first popped action
        at the state it was taken from. Returns how many actions were popped (0 = nothing left to undo)."""
        base = self._base()
        popped = []
        for _ in range(k):
            if len(base.actions) == 0:
                break
            popped.append(base.pop_action())
        if not popped and base._prefix:  # dead end inside the first action: just drop the partial tokens
            del base.tokens[len(base.tokens) - len(base._prefix):]
            base._prefix, base._node, base._allowed = [], None, None
        if popped:
            self.forbidden.setdefault(self.state_key(), set()).add(popped[-1])
        base.status, base.dead_end = "running", None
        steps = getattr(self.inner, "steps", None)  # RecordingDecoder: keep its per-token log aligned
        if steps is not None:
            del steps[len(base.tokens):]
            self.inner._pending = None
        return len(popped)


def _restore_cache(model, cache, prompt: Sequence[int], tokens: Sequence[int], in_cache: int):
    """The cache holds prompt[:-1] + S[:in_cache] with S = [prompt[-1]] + plan tokens. Make it hold
    prompt[:-1] + S[:len(tokens)], so the next token to feed is S[len(tokens)] = tokens[-1] (or prompt[-1]).
    Returns (cache, pending token, new in_cache, "trim" | "prefill")."""
    from mlx_lm.models.cache import can_trim_prompt_cache, trim_prompt_cache

    pending = tokens[-1] if tokens else prompt[-1]
    drop = in_cache - len(tokens)
    if drop >= 0 and can_trim_prompt_cache(cache):
        if drop:
            trim_prompt_cache(cache, drop)
        return cache, pending, len(tokens), "trim"
    return prefill(model, list(prompt) + list(tokens)), pending, len(tokens), "prefill"


def generate_backtracking(model, prompt: Sequence[int], decoder: ForbiddingDecoder, *, temperature: float,
                          seed: int = 0, max_tokens: int = 100_000, max_backtracks: int = 32,
                          max_rewind: int = 8, max_rewound_actions: int = 64, check_deadline=None) -> tuple[SequenceResult, dict[str, Any]]:
    """Decode one plan under the mask with bounded backtracking (batch 1). Returns the result and its stats."""
    import mlx.core as mx

    t0 = time.perf_counter()
    cache = prefill(model, prompt)
    stats = {"prefill_s": round(time.perf_counter() - t0, 3), "backtracks": 0, "rewound_actions": 0,
             "cache_trims": 0, "cache_refills": 0, "steps": 0, "override_steps": 0, "argmax_illegal": 0,
             "boundary_steps": 0, "boundary_argmax_illegal": 0, "max_depth": 0}
    key = mx.random.key(seed)
    pending, in_cache = prompt[-1], 0  # cache: prompt[:-1] + S[:in_cache], S = [prompt[-1]] + plan; feed ``pending``
    logps: list[float] = []
    episode_depth, episode_k = -1, 0
    t_loop = time.perf_counter()
    status = "max_tokens"
    while stats["steps"] < max_tokens:
        if check_deadline is not None:
            check_deadline()
        allowed = decoder.allowed_token_ids()
        if decoder.status == "complete":
            status = "complete"
            break
        if not allowed:
            depth = len(decoder.actions)
            # bounded: rewinds per plan, depth of one rewind (k <= max_rewind) and total rewound actions; measured on a
            # small map, unbounded doubling rewound 322 actions in 21 backtracks (each decode step costs ~0.1-0.25 s
            # of mask work there), so deep rewinds mostly re-decode the same plan
            if stats["backtracks"] >= max_backtracks or stats["rewound_actions"] >= max_rewound_actions:
                if decoder.status == "running":  # forbidden-exhausted state: report it as a dead end
                    base = decoder._base()
                    from llm.stowlm.mask import DeadEnd
                    base.status, base.dead_end = "dead_end", DeadEnd(depth, "backtracking budget exhausted", ())
                status = "dead_end"
                break
            # one dead-end episode = rewinds until the decoder gets deeper than where it first failed
            episode_k = 1 if depth > episode_depth else min(episode_k * 2, max_rewind)
            episode_depth = max(episode_depth, depth)
            popped = decoder.rewind(episode_k)
            stats["backtracks"] += 1
            stats["rewound_actions"] += popped
            if popped == 0 and not decoder.actions and not decoder.allowed_token_ids():
                status = "dead_end"  # nothing left to undo at the root
                break
            tokens = list(decoder.tokens)
            del logps[len(tokens):]
            cache, pending, in_cache, how = _restore_cache(model, cache, prompt, tokens, in_cache)
            stats["cache_trims" if how == "trim" else "cache_refills"] += 1
            continue
        logits = model(mx.array([[pending]]), cache=cache)[:, -1, :].astype(mx.float32)
        idx = mx.array(list(allowed))
        sel = logits[0, idx]
        argmax = int(mx.argmax(logits[0]).item())
        boundary = not decoder._base()._prefix
        if temperature <= 0:
            choice = int(mx.argmax(sel).item())
            lp = 0.0
        else:
            key, sub = mx.random.split(key)
            scaled = sel / temperature
            choice = int(mx.random.categorical(scaled, key=sub).item())
            lp = float((scaled - mx.logsumexp(scaled))[choice].item())
        token = allowed[choice]
        stats["steps"] += 1
        stats["argmax_illegal"] += argmax not in allowed
        stats["boundary_steps"] += boundary
        stats["boundary_argmax_illegal"] += boundary and argmax not in allowed
        in_cache += 1  # ``pending`` is now in the cache
        decoder.advance(token)
        logps.append(lp)
        pending = token
        stats["max_depth"] = max(stats["max_depth"], len(decoder.actions))
        if decoder.status not in ("running", "complete"):
            continue  # dead end detected on advance: handled at the top of the loop
    if decoder.status == "complete":
        status = "complete"
    elif decoder.status == "dead_end":
        status = "dead_end"
    stats["in_cache"] = in_cache
    stats["wall_s"] = round(time.perf_counter() - t_loop, 3)
    stats["tokens"] = len(decoder.tokens)
    stats["tok_s"] = round(stats["steps"] / stats["wall_s"], 2) if stats["wall_s"] else 0.0
    return SequenceResult(list(decoder.tokens), status, logps, decoder.inner), stats
