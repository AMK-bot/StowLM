"""MLX side of the feasibility mask (plan 5.1): a logits processor, batched masked sampling, masked log-probs.

**How masking enters decoding.** At every step the model returns one logit per vocabulary token. A *logits
processor* rewrites them before sampling; here it keeps only the allowed tokens of ``mask.MaskedDecoder`` (the rest
get -inf, i.e. probability 0). Instead of building a 200k-wide -inf mask per sequence, the batched loop *gathers*
the allowed logits into a small (batch × max-allowed) matrix and samples there: same distribution, far less work.

**Batched, lock-step generation.** All sequences in one call share one prompt (one instance; GRPO groups and eval
samples are exactly that), so the prompt is prefilled once at batch 1 and its KV cache is copied B times. Then
every sequence advances one token per step. ``mx.async_eval`` blocks for most of the forward pass (measured
~270 of ~290 ms at b64) but releases the GIL, so the host mask work (allowed sets, then ``prefetch`` of determined
actions) runs in a worker thread *during* the forward pass; ``GenerationStats.host_wait_s`` is what remains exposed. Sequences that finish (end of plan) or reach a dead end are removed from the batch (their
KV rows are dropped) so the remaining ones run faster. Source for the batching idea: mlx-lm's ``BatchGenerator``;
this loop is simpler (no padding: one prompt) and uses the bare model step as ``docs/compute.md`` recommends.

**Behavior log-probs (D9).** For temperature T > 0, ``log p(t) = logit[t]/T -
logsumexp(logit[allowed]/T)``. ``generate_masked`` records the actual sampling distribution;
``masked_logprobs`` replays it for a given (prompt, plan) at the same temperature (default 1).
For T <= 0 decoding is deterministic: the first allowed maximum gets log-probability 0,
all other tokens -inf, including tied maxima. These are behavior scores for eval/GRPO,
not temperature-independent raw-policy scores.
"""

from __future__ import annotations

import math
import threading
import time
from collections.abc import Callable, Sequence
from contextvars import copy_context
from copy import deepcopy
from dataclasses import dataclass, field
from typing import Any

import mlx.core as mx
import numpy as np

from llm.stowlm.mask import MaskedDecoder, _DEAD_STATE_RULE_LABEL
from stowage.rules.catalogue import rule_freeze_v1_enabled

PREFILL_CHUNK = 2048


@dataclass
class SequenceResult:
    """One sequence; ``masked_logprobs`` describes its sampling temperature.

    ``status``: complete | dead_end | max_tokens | unmasked.
    """

    tokens: list[int]
    status: str
    masked_logprobs: list[float]
    decoder: MaskedDecoder | None = None
    diagnostics: dict[str, Any] = field(default_factory=dict)
    screen_trace: list[dict[str, Any]] = field(default_factory=list)

    @property
    def logprob(self) -> float:
        return float(sum(self.masked_logprobs))


@dataclass
class GenerationStats:
    """Timings of one ``generate_masked`` call (seconds unless noted)."""

    prefill_s: float = 0.0
    steps: int = 0
    tokens: int = 0  # sampled tokens summed over sequences
    wall_s: float = 0.0  # decode loop only
    host_mask_s: float = 0.0  # allowed sets + index matrix (overlaps the GPU forward)
    wait_s: float = 0.0  # waiting for logits + sampling after the host work
    advance_s: float = 0.0  # feeding sampled tokens to the decoders
    prefetched: int = 0  # successor states built ahead during GPU time (MaskedDecoder.prefetch)
    host_wait_s: float = 0.0  # main thread waiting for the host worker after the forward pass returned (the stall)
    step_ms: list[float] = field(default_factory=list)
    batch_sizes: list[int] = field(default_factory=list)
    dead_ends_by_rule: dict[str, int] = field(default_factory=dict)
    dead_ends_unattributed: int = 0
    # Screen supplement to the run-folder action trace, not a replacement for
    # its chosen-action text, behavior scores, or pre-mask model top-5.
    decode_trace: list[dict[str, Any]] = field(default_factory=list)
    screen_diagnostics_enabled: bool = False

    def summary(self) -> dict[str, Any]:
        s = sorted(self.step_ms) or [0.0]
        result = dict(prefill_s=round(self.prefill_s, 3), steps=self.steps, tokens=self.tokens,
                    wall_s=round(self.wall_s, 3), tok_s=round(self.tokens / self.wall_s, 2) if self.wall_s else 0.0,
                    host_mask_s=round(self.host_mask_s, 3), wait_s=round(self.wait_s, 3),
                    advance_s=round(self.advance_s, 3), prefetched=self.prefetched,
                    host_wait_s=round(self.host_wait_s, 3),
                    step_ms_p50=round(s[len(s) // 2], 2), step_ms_p95=round(s[min(len(s) - 1, int(0.95 * len(s)))], 2))
        if self.screen_diagnostics_enabled:
            result.update(dead_ends_by_rule=dict(self.dead_ends_by_rule),
                          dead_ends_unattributed=self.dead_ends_unattributed,
                          decode_trace=deepcopy(self.decode_trace))
        return result


def _screen_diagnostics(engine: Any) -> dict[str, Any] | None:
    query = getattr(engine, "screen_diagnostics", None)
    return deepcopy(query()) if callable(query) else None


def _decoder_diagnostics(decoder: Any) -> dict[str, Any]:
    """Snapshot diagnostics, keeping legacy/custom decoders without the additive API usable.

    Pool proxies must transport rule ids and screen snapshots from their worker
    to provide attribution; an unavailable cause is explicitly unattributed.
    """
    query = getattr(decoder, "diagnostics", None)
    if callable(query):
        result = deepcopy(query())
    else:
        engine = getattr(decoder, "engine", None)
        dead = getattr(decoder, "dead_end", None)
        rules = sorted(set(getattr(dead, "rule_ids", ())))
        result = dict(status=decoder.status, depth=getattr(engine, "depth", None),
                dead_end=None if dead is None else dict(depth=getattr(dead, "depth", None),
                                                       reason=dead.reason, rule_ids=rules),
                dead_ends_by_rule={rule: 1 for rule in rules}, screens=_screen_diagnostics(engine))
    if rule_freeze_v1_enabled() and result.get("dead_end") is not None:
        result["dead_end"]["rule_ids_label"] = (result.get("screens") or {}).get(
            "dead_end_rule_ids_label", _DEAD_STATE_RULE_LABEL)
    return result


def prefill(model: Any, prompt: Sequence[int], *, chunk: int = PREFILL_CHUNK) -> list[Any]:
    """Run the prompt minus its last token through a fresh batch-1 cache (the last token is fed by the loop)."""
    cache = model.make_cache()
    ids = mx.array(list(prompt[:-1]))[None]
    for s in range(0, ids.shape[1], chunk):
        model(ids[:, s:s + chunk], cache=cache)
        mx.eval([c.state for c in cache])
    return cache


def _tile(cache: list[Any], n: int) -> None:
    for c in cache:
        if c.keys is not None:
            c.keys = mx.repeat(c.keys, n, axis=0)
            c.values = mx.repeat(c.values, n, axis=0)


def _keep(cache: list[Any], rows: mx.array) -> None:
    for c in cache:
        if c.keys is not None:
            c.keys = c.keys[rows]
            c.values = c.values[rows]


def _index_matrix(sets: Sequence[Sequence[int]]) -> tuple[mx.array, mx.array]:
    """(B, A) token-id matrix padded with each row's first id, and the (B, A) validity mask."""
    width = max(len(s) for s in sets)
    idx = np.empty((len(sets), width), dtype=np.int32)
    valid = np.zeros((len(sets), width), dtype=bool)
    for r, s in enumerate(sets):
        idx[r, :len(s)] = s
        idx[r, len(s):] = s[0]
        valid[r, :len(s)] = True
    return mx.array(idx), mx.array(valid)


def _behavior_logprobs(logits: mx.array, temperature: float) -> mx.array:
    """Row distributions; nonpositive temperatures select the first maximum deterministically."""
    if temperature <= 0:
        choice = mx.argmax(logits, axis=-1)
        return mx.where(mx.arange(logits.shape[-1])[None, :] == choice[:, None], 0.0, -math.inf)
    scaled = logits / temperature
    return scaled - mx.logsumexp(scaled, axis=-1, keepdims=True)


def masked_sample(logits: mx.array, idx: mx.array, valid: mx.array, temperature: float,
                  key: mx.array | None = None) -> tuple[mx.array, mx.array]:
    """Return sampled IDs and behavior log-probs; T <= 0 picks the first allowed maximum with score 0."""
    sel = mx.take_along_axis(logits.astype(mx.float32), idx, axis=-1)
    sel = mx.where(valid, sel, -math.inf)
    logp = _behavior_logprobs(sel, temperature)
    if temperature <= 0:
        choice = mx.argmax(sel, axis=-1)
    else:
        choice = mx.random.categorical(sel / temperature, key=key)
    tok = mx.take_along_axis(idx, choice[:, None], axis=-1)[:, 0]
    lp = mx.take_along_axis(logp, choice[:, None], axis=-1)[:, 0]
    return tok, lp


def masked_sample_rows(logits: mx.array, idx: mx.array, valid: mx.array, temps: mx.array,
                       key: mx.array | None = None) -> tuple[mx.array, mx.array]:
    """``masked_sample`` with one temperature per row (rows with T <= 0 are greedy, score 0), so a greedy sample
    and k-1 stochastic samples share ONE prefill and one lock-step batch."""
    sel = mx.take_along_axis(logits.astype(mx.float32), idx, axis=-1)
    sel = mx.where(valid, sel, -math.inf)
    greedy = temps <= 0
    scaled = sel / mx.where(greedy, 1.0, temps)[:, None]
    logp = scaled - mx.logsumexp(scaled, axis=-1, keepdims=True)
    choice = mx.where(greedy, mx.argmax(sel, axis=-1), mx.random.categorical(scaled, key=key))
    tok = mx.take_along_axis(idx, choice[:, None], axis=-1)[:, 0]
    lp = mx.where(greedy, 0.0, mx.take_along_axis(logp, choice[:, None], axis=-1)[:, 0])
    return tok, lp


def generate_masked(model: Any, prompt: Sequence[int], decoders: Sequence[MaskedDecoder] | None, *,
                    batch: int | None = None, temperature: float | Sequence[float] = 1.0, max_tokens: int = 100_000, seed: int = 0,
                    on_step: Callable[[int, int], None] | None = None, prefetch: bool = True) -> tuple[list[SequenceResult], GenerationStats]:
    """Sample ``len(decoders)`` plans for one prompt under the mask, lock-step.

    ``decoders=None`` runs the same loop **unmasked** for ``batch`` sequences and exactly ``max_tokens`` steps
    (the overhead baseline; outputs are meaningless). Each decoder must start at the instance's root engine state
    (pass copies). Stops a sequence at end of plan, dead end, or ``max_tokens``. ``prefetch``: use the rest of each
    step's GPU time to build successor states of already-determined actions (``MaskedDecoder.prefetch``); it never
    changes the sampled distribution.

    ``SequenceResult.screen_trace`` and ``GenerationStats.summary()['decode_trace']``
    carry action screen snapshots and terminal dead-end attribution. They
    supplement the run-folder trace; the writer must merge these fields into
    its action rows. Action snapshots require a decoder that returns completed
    actions from ``advance`` (the in-thread decoder does). No logits or GPU work
    are added for diagnostics. Collection and new summary keys are gated by the
    catalogue activation switch; inactive decoding follows the legacy path.
    """
    masked = decoders is not None
    per_row = not isinstance(temperature, (int, float))
    if per_row and (not masked or len(temperature) != len(decoders)):
        raise ValueError("per-row temperatures need masked decoding and one temperature per decoder")
    collect_diagnostics = masked and rule_freeze_v1_enabled()
    n = len(decoders) if masked else int(batch or 1)
    stats = GenerationStats(screen_diagnostics_enabled=collect_diagnostics)
    key = mx.random.key(seed)
    t0 = time.perf_counter()
    cache = prefill(model, prompt)
    _tile(cache, n)
    stats.prefill_s = time.perf_counter() - t0
    results = [SequenceResult([], "running", [], decoders[i] if masked else None) for i in range(n)]
    active = list(range(n))
    y = mx.array([[prompt[-1]]] * n)
    t_loop = time.perf_counter()
    for step in range(max_tokens):
        ts = time.perf_counter()
        worker = None
        if masked:
            # mx.async_eval blocks for most of the forward pass but releases the GIL (measured: a Python thread runs
            # at ~84% speed meanwhile), so the host mask work runs in a worker thread during the forward pass:
            # first the allowed sets every row needs, then prefetches of determined actions until the GPU is done.
            box: dict[str, Any] = {}
            gpu_done = threading.Event()
            rows_now = list(active)

            def host_work() -> None:
                t0 = time.perf_counter()
                box["sets"] = [decoders[i].allowed_token_ids() for i in rows_now]
                box["sets_s"] = time.perf_counter() - t0
                if prefetch:
                    for i in rows_now:
                        if gpu_done.is_set():
                            break
                        if decoders[i].prefetch():
                            stats.prefetched += 1

            # Catalogue activation is contextual. Threads do not inherit those
            # overrides, so both allowed sets and prefetch must run in the
            # caller's snapshot (a fresh Context for this step's worker).
            host_context = copy_context()
            worker = threading.Thread(target=host_context.run, args=(host_work,), daemon=True)
            worker.start()
        logits = model(y, cache=cache)[:, -1, :]
        mx.async_eval(logits)
        th = time.perf_counter()
        key, sub = mx.random.split(key)
        if masked:
            gpu_done.set()
            worker.join()
            stats.host_mask_s += box["sets_s"]
            stats.host_wait_s += time.perf_counter() - th
            th = time.perf_counter()
            sets = box["sets"]
            live = [k for k, s in enumerate(sets) if s]
            if len(live) < len(active):  # dead ends (or completions) found by the host: drop those rows
                for k, i in enumerate(active):
                    if not sets[k]:
                        results[i].status = decoders[i].status
                if not live:
                    active = []
                    stats.host_mask_s += time.perf_counter() - th
                    break
                rows = mx.array(live)
                logits = logits[rows]
                _keep(cache, rows)
                active = [active[k] for k in live]
                sets = [sets[k] for k in live]
            idx, valid = _index_matrix(sets)
            stats.host_mask_s += time.perf_counter() - th
            tw = time.perf_counter()
            if per_row:
                tok, lp = masked_sample_rows(logits, idx, valid,
                                             mx.array([float(temperature[i]) for i in active]), sub)
            else:
                tok, lp = masked_sample(logits, idx, valid, temperature, sub)
        else:
            tw = time.perf_counter()
            scaled = logits.astype(mx.float32)
            logp = _behavior_logprobs(scaled, temperature)
            tok = (mx.argmax(scaled, axis=-1) if temperature <= 0
                   else mx.random.categorical(scaled / temperature, key=sub))
            lp = mx.take_along_axis(logp, tok[:, None], axis=-1)[:, 0]
        mx.eval(tok, lp)
        toks, lps = tok.tolist(), lp.tolist()
        stats.wait_s += time.perf_counter() - tw
        ta = time.perf_counter()
        still = []
        for k, i in enumerate(active):
            r = results[i]
            r.tokens.append(toks[k])
            r.masked_logprobs.append(lps[k])
            if masked:
                if collect_diagnostics:
                    # Keep the queried source engine: advance moves the decoder
                    # to a successor whose candidate screens have not run yet.
                    source_engine = getattr(decoders[i], "engine", None)
                    action = decoders[i].advance(toks[k])
                    if action is not None:
                        diagnostics = _decoder_diagnostics(decoders[i])
                        r.screen_trace.append(dict(step=len(r.screen_trace), event="action",
                                                   depth=diagnostics.get("depth"),
                                                   screens=_screen_diagnostics(source_engine)))
                else:
                    decoders[i].advance(toks[k])
                if decoders[i].status == "running":
                    still.append(k)
                else:
                    r.status = decoders[i].status
            else:
                still.append(k)
        stats.advance_s += time.perf_counter() - ta
        stats.steps += 1
        stats.tokens += len(active)
        stats.batch_sizes.append(len(active))
        y = mx.array([[toks[k]] for k in still]) if still else None
        if len(still) < len(active):
            if still:
                _keep(cache, mx.array(still))
            active = [active[k] for k in still]
        stats.step_ms.append((time.perf_counter() - ts) * 1e3)
        if on_step is not None:
            on_step(step, len(active))
        if not active:
            break
    stats.wall_s = time.perf_counter() - t_loop
    for i in active:
        results[i].status = "max_tokens" if masked else "unmasked"
    if collect_diagnostics:
        for i, result in enumerate(results):
            result.diagnostics = diagnostics = _decoder_diagnostics(decoders[i])
            if result.status == "dead_end":
                dead = diagnostics.get("dead_end")
                rules = sorted(set(dead.get("rule_ids", ()))) if dead else []
                for rule in rules:
                    stats.dead_ends_by_rule[rule] = stats.dead_ends_by_rule.get(rule, 0) + 1
                if not rules:
                    stats.dead_ends_unattributed += 1
                result.screen_trace.append(dict(step=len(result.screen_trace), event="dead_end",
                                                depth=diagnostics.get("depth"), dead_end=dead,
                                                rule_ids_label=dead.get("rule_ids_label", _DEAD_STATE_RULE_LABEL) if dead else _DEAD_STATE_RULE_LABEL,
                                                dead_ends_by_rule={rule: 1 for rule in rules},
                                                screens=diagnostics.get("screens")))
            stats.decode_trace.extend(dict(sample=i, **row) for row in result.screen_trace)
    return results, stats


def masked_logprobs(model: Any, prompt: Sequence[int], plan_tokens: Sequence[int],
                    sets: Sequence[Sequence[int]], *, temperature: float = 1.0) -> np.ndarray:
    """Replay per-token behavior log-probs under the mask at the generation temperature (D9).

    ``sets[i]`` = ordered allowed IDs before token i (``mask.allowed_sets``). T <= 0 is deterministic:
    only the first allowed maximum receives 0; another allowed token receives -inf, even on a tie.
    Returns float32 array (len(plan_tokens),), including an empty array without model work for an empty plan.
    """
    if len(sets) != len(plan_tokens):
        raise ValueError("one allowed set per plan token")
    if any(t not in s for t, s in zip(plan_tokens, sets)):
        raise ValueError("a plan token is outside its allowed set")
    if not len(plan_tokens):
        return np.empty((0,), dtype=np.float32)
    cache = prefill(model, prompt)
    seq = mx.array([prompt[-1], *plan_tokens[:-1]])[None]
    out = []
    for s in range(0, seq.shape[1], PREFILL_CHUNK):
        logits = model(seq[:, s:s + PREFILL_CHUNK], cache=cache)[0]
        part = sets[s:s + PREFILL_CHUNK]
        idx, valid = _index_matrix(part)
        sel = mx.where(valid, mx.take_along_axis(logits.astype(mx.float32), idx, axis=-1), -math.inf)
        tgt = mx.array(list(plan_tokens[s:s + PREFILL_CHUNK]))[:, None]
        column = mx.argmax(valid & (idx == tgt), axis=-1)
        logp = _behavior_logprobs(sel, temperature)
        out.append(np.array(mx.take_along_axis(logp, column[:, None], axis=-1)[:, 0]))
    return np.concatenate(out).astype(np.float32)


class MaskLogitsProcessor:
    """mlx-lm ``logits_processors`` entry for batch-1 ``generate_step``: ``(tokens, logits) -> logits``.

    Feeds each newly generated token to the decoder, then sets every disallowed logit to -inf. ``prompt_len`` =
    number of prompt tokens (mlx-lm passes prompt + generated tokens). When no token is allowed (plan complete or
    dead end) only the end-of-turn token is left, so generation stops; ``decoder.status`` says which.
    """

    def __init__(self, decoder: MaskedDecoder, prompt_len: int, stop_token: int) -> None:
        self.decoder = decoder
        self.prompt_len = prompt_len
        self.stop_token = stop_token
        self._seen = 0

    def __call__(self, tokens: mx.array, logits: mx.array) -> mx.array:
        new = tokens.shape[-1] - self.prompt_len
        if new > self._seen:
            for t in np.array(tokens).reshape(-1)[self.prompt_len + self._seen:self.prompt_len + new].tolist():
                if self.decoder.status == "running":
                    self.decoder.advance(int(t))
            self._seen = new
        allowed = list(self.decoder.allowed_token_ids()) or [self.stop_token]
        bias = np.full(logits.shape[-1], -np.inf, dtype=np.float32)
        bias[allowed] = 0.0
        return logits + mx.array(bias).astype(logits.dtype)
