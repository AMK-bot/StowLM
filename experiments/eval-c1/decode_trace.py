"""Decode traces for the run-folder contract (experiments/fleet/orders/w2-stowlm-run-contract.md).

Three parts, no src/ change:

1. ``RecordingFactory`` plugs into ``StowLMPlanner(decoder_factory=...)`` and wraps every sample's decoder (in-thread
   ``MaskedDecoder`` or a pool proxy). Per token it records the allowed set the mask produced, the host time spent on
   it, and, when the token starts an action, the engine's legal-action count and the physically possible count.
2. ``build_trace`` takes the *selected* sample (``metrics["chosen_sample"]``) and replays prompt + its tokens through
   the model once (teacher forcing, batch 1, chunked like ``mask_mlx.masked_logprobs``). That gives, at each action's
   first token, the model's top-5 tokens **before masking** with a ``legal`` flag, and the chosen action's masked
   (behaviour, D9) and unmasked log-probabilities. It is the *selected* sample, *replayed*; caption it so.
3. ``override_stats`` covers **all** k samples: at each decoded position, was the model's unmasked top-1 token
   outside the mask's allowed set (the mask overrode the model)? The argmax comes from the actual batched sampling
   forward passes (``ArgmaxLog``), not from a replay.

Denominators. ``n_legal`` is the engine's legal actions (every rule and screen applied). ``n_actions`` is what is
**physically possible** at that state: Place(box, slot) for every liftable remaining box and every open slot of the
box's length class (a 20 ft box in a 20 ft slot, a long box in a 40/45 ft slot), before any rule, stability or DG
check — so "n_legal of n_actions" shows what the rules remove, not what geometry trivially excludes. The raw
remaining-boxes × all-slots product is kept as ``n_place_pairs_raw`` for reference only.
"""

from __future__ import annotations

import json
import math
import time
from pathlib import Path
from typing import Any, Callable, Sequence

import numpy as np


def physically_possible(engine) -> int | None:
    """Liftable remaining boxes × open slots of matching length class at the engine's current state (Stage A
    Place actions only). Reads engine internals (``_ensure``, ``_open_slots``, ``_liftable``, ``_attr``, ``T``);
    returns None if they're unavailable, rather than guessing."""
    try:
        st = engine._ensure()
        open_slots = engine._open_slots(st)
        units = engine.T.slot_n[open_slots]  # 1 = 20 ft slot, 2 = long slot
        n20, nlong = int(np.sum(units == 1)), int(np.sum(units != 1))
        boxes = np.flatnonzero(engine._liftable(st))
        box_units = [engine._attr[engine.T.box_ids[b]][1] for b in boxes]
        return sum(n20 if u == 1 else nlong for u in box_units)
    except Exception:
        return None


def _at_boundary(inner) -> bool:
    """Is the decoder at an action start? Cheap (no legal-action count: logic-mask High 2 moved exact counts out of
    the batched decode; they're computed only when replaying the selected sample, ``exact_counts``)."""
    if hasattr(inner, "at_boundary"):  # pool proxy: reported by the worker with each allowed set
        return bool(inner.at_boundary)
    return getattr(inner, "_prefix", None) == [] and inner.status == "running"


DECISION_FIELDS = ("box", "bay", "row", "tier")


def token_fields(texts: Sequence[str]) -> list[str]:
    """Field of each token of one action, from its text (``ActionCodec.action_fragments``: opcode, box index, " ",
    then for Place bay(3)/row(2)/tier(2) digits, then newline). Decisions are box/bay/row/tier; the opcode, separators
    and newline are format. Other opcodes' targets are labelled "target"; the end-of-plan sequence "eop"."""
    fields, stage, chars = [], "opcode", 0
    op = None
    for i, text in enumerate(texts):
        if i == 0:
            op = text.strip()[:1] or None
            fields.append("opcode" if op in ("P", "O", "D", "R") else "eop")
            if fields[-1] == "eop":
                return fields + ["eop"] * (len(texts) - 1)
            stage = "box"
            if text.strip()[1:].isdigit():  # opcode and index merged into one token
                fields[-1] = "box"
            continue
        if "\n" in text:
            fields.append("newline")
            continue
        if stage == "box":
            if text.startswith(" "):
                stage, chars = "slot", 0
                digits = text.strip()
                if not digits:
                    fields.append("sep")
                    continue
            else:
                fields.append("box")
                continue
        if op != "P":
            fields.append("target")
            continue
        start = chars
        chars += len(text.strip())
        fields.append("bay" if start < 3 else "row" if start < 5 else "tier")
    return fields


def _screen_rejections(inner) -> dict | None:
    """The engine's per-screen candidate rejection counts at this state (``screen_diagnostics``), in-thread only."""
    try:
        return {str(k): int(v) for k, v in inner.engine.screen_diagnostics()["rejected"].items()}
    except Exception:
        return None


class RecordingDecoder:
    """Transparent wrapper: forwards everything to ``inner`` and records the per-token mask trace."""

    def __init__(self, inner, clock: Callable[[], float] = time.perf_counter) -> None:
        self.inner, self._clock = inner, clock
        self.steps: list[dict] = []
        self._pending: dict | None = None
        self.t0 = clock()

    def allowed_token_ids(self):
        t = self._clock()
        allowed = self.inner.allowed_token_ids()
        ms = (self._clock() - t) * 1e3
        if self._pending is None:
            boundary = _at_boundary(self.inner)
            self._pending = {"allowed": tuple(allowed), "mask_ms": ms, "boundary": boundary,
                             "screens": _screen_rejections(self.inner) if boundary else None}
        else:
            self._pending["mask_ms"] += ms
        return allowed

    def advance(self, token_id: int):
        rec = self._pending or {"allowed": None, "mask_ms": 0.0, "boundary": False}
        rec.update(token=int(token_id), t=self._clock())
        self.steps.append(rec)
        self._pending = None
        return self.inner.advance(token_id)

    @property
    def queried(self) -> int:
        """Decode positions at which this sample was in the batch (it was asked for an allowed set)."""
        return len(self.steps) + (1 if self._pending is not None else 0)

    def allowed_at(self, position: int):
        rec = self.steps[position] if position < len(self.steps) else self._pending
        return None if rec is None else rec["allowed"]

    def prefetch(self) -> bool:
        return self.inner.prefetch()

    def __getattr__(self, name: str):  # status, dead_end, tokens, actions, engine, ...
        return getattr(self.inner, name)


class RecordingFactory:
    """``decoder_factory`` that wraps another factory's decoders (``base=None``: in-thread ``MaskedDecoder``)."""

    def __init__(self, base=None) -> None:
        self.base = base
        self.name = getattr(base, "name", "in-thread")
        self.last: list[RecordingDecoder] = []
        self.n_slots: int | None = None

    def __call__(self, instance, engine, codec, mask_config, n):
        if self.base is None:
            from llm.stowlm.mask import MaskedDecoder

            inner = [MaskedDecoder(engine if i == 0 else engine.copy(), codec, mask_config) for i in range(n)]
        else:
            inner = self.base(instance, engine, codec, mask_config, n)
        self.n_slots = getattr(engine, "n_slots", None)
        self.last = [RecordingDecoder(d) for d in inner]
        return self.last


class ArgmaxLog:
    """Model wrapper for ``generate_masked``: after each one-token decode forward, records each batch row's
    unmasked argmax token (reduced on the GPU, async). Everything else is delegated to the model."""

    def __init__(self, model) -> None:
        self.model, self.steps = model, []

    def __call__(self, x, cache=None):
        out = self.model(x, cache=cache)
        if x.shape[1] == 1:  # decode step (prefill feeds chunks); see override_stats for the 1-token-chunk edge
            import mlx.core as mx

            top = mx.argmax(out[:, -1, :], axis=-1)
            mx.async_eval(top)
            self.steps.append(top)
        return out

    def __getattr__(self, name: str):
        return getattr(self.model, name)

    def rows(self) -> list[list[int]]:
        return [[int(v) for v in np.array(s).reshape(-1)] for s in self.steps]


def segment_fields(steps: Sequence[dict], token_text) -> list[str | None]:
    """Field of every recorded token of one sample: actions are segmented at recorded boundaries and classified by
    ``token_fields``. Without ``token_text`` every field is None (decision positions then unavailable)."""
    if token_text is None:
        return [None] * len(steps)
    starts = [i for i, s in enumerate(steps) if s.get("boundary")] or ([0] if steps else [])
    out: list[str | None] = [None] * len(steps)
    for k, start in enumerate(starts):
        end = starts[k + 1] if k + 1 < len(starts) else len(steps)
        for offset, field in enumerate(token_fields([token_text(steps[i]["token"]) for i in range(start, end)])):
            out[start + offset] = field
    return out


def override_stats(recordings: Sequence[RecordingDecoder], argmax_rows: list[list[int]],
                   token_text=None) -> dict[str, Any]:
    """Over all samples: where was the model's unmasked top-1 token illegal (the mask overrode it)?

    Rates are reported at **decision tokens** (the first token of the box index and of the bay/row/tier fields;
    logic-mask High 1, run contract amended 29 Oct), per field, and separately at the opcode (``format_override``):
    the opcode position measures format compliance, not a planning choice. Decode step t's batch holds, in sample
    order, exactly the samples asked for an allowed set at position t (``generate_masked`` keeps rows in order and
    drops finished ones); extra leading one-token forwards (a final prefill chunk) are dropped."""
    n_steps = max((r.queried for r in recordings), default=0)
    rows = argmax_rows[len(argmax_rows) - n_steps:] if len(argmax_rows) > n_steps else argmax_rows
    fields = [segment_fields(r.steps, token_text) for r in recordings]
    by_field: dict[str, list[int]] = {f: [0, 0] for f in (*DECISION_FIELDS, "opcode")}
    total = total_over = 0
    screens_over: dict[str, int] = {}
    screens_all: dict[str, int] = {}
    for t, row in enumerate(rows):
        active = [i for i, r in enumerate(recordings) if t < r.queried]
        if len(active) != len(row):
            return {"override_error": f"batch size mismatch at step {t}: {len(row)} rows, {len(active)} samples"}
        for k, i in enumerate(active):
            allowed = recordings[i].allowed_at(t)
            if not allowed:  # finished or dead end: no choice was made here
                continue
            over = row[k] not in allowed
            total += 1
            total_over += over
            field = fields[i][t] if t < len(fields[i]) else None
            prev = fields[i][t - 1] if 0 < t <= len(fields[i]) else None
            if field in by_field and (field == "opcode" or prev != field):  # first token of the field only
                by_field[field][0] += 1
                by_field[field][1] += over
                rec = recordings[i].steps[t - _first_of(fields[i], t)]
                if field == "box":
                    for rule, n in (rec.get("screens") or {}).items():
                        screens_all[rule] = screens_all.get(rule, 0) + n
                        if over:
                            screens_over[rule] = screens_over.get(rule, 0) + n
    decision_n = sum(by_field[f][0] for f in DECISION_FIELDS)
    decision_over = sum(by_field[f][1] for f in DECISION_FIELDS)
    opcode_n, opcode_over = by_field["opcode"]
    return {"mask_override_rate_decision": decision_over / decision_n if decision_n else None,
            "mask_override_decision_positions": decision_n,
            "mask_override_rate_by_field": {f: (by_field[f][1] / by_field[f][0] if by_field[f][0] else None)
                                            for f in DECISION_FIELDS},
            "mask_override_positions_by_field": {f: by_field[f][0] for f in DECISION_FIELDS},
            "format_override_rate": opcode_over / opcode_n if opcode_n else None,
            "format_override_positions": opcode_n,
            "mask_override_rate_all_tokens": total_over / total if total else None,
            "mask_override_token_positions": total,
            "mask_override_scope": ("all samples, batched sampling pass (unmasked argmax vs allowed set); decision = "
                                    "first token of box/bay/row/tier; opcode overrides are format_override"),
            # Screens that rejected candidates at the action states where the model's box choice was overridden,
            # vs at all box decisions (in-thread decoders, or the pool's boundary screens). A state-level rule mix.
            "override_state_screen_rejections": screens_over, "decision_state_screen_rejections": screens_all}


def _first_of(fields: list, t: int) -> int:
    """Offset from position t back to its action's start (the boundary record carries the state's screens)."""
    back = 0
    while t - back > 0 and fields[t - back] not in ("opcode", None):
        back += 1
    return back


def exact_counts(engine, actions: Sequence[Any]) -> list[dict]:
    """Exact legal-action counts for the selected sample only (never in the batched decode; logic-mask High 2):
    replay ``actions`` on ``engine`` (a fresh root-state engine) and, before each Stage A Place, count the legal
    actions, the legal slots for the chosen box, then narrowed by the chosen bay and row, and the physically possible
    placements. Other action kinds get the state count only."""
    out = []
    for action in actions:
        row: dict[str, Any] = {"n_possible": physically_possible(engine)}
        try:
            boxes, slots = engine.legal_pairs()
            row["n_legal"] = int(len(boxes))
            slot_id = getattr(action, "slot", None)
            if slot_id is not None:
                b = list(engine.T.box_ids).index(action.box)
                ids = [engine.T.slot_ids[sl] for bx, sl in zip(boxes.tolist(), slots.tolist()) if bx == b]
                # options left at each decision: all legal actions -> slots for the box -> in its bay -> in bay+row
                row.update(box=row["n_legal"], bay=len(ids), row=sum(i[:3] == slot_id[:3] for i in ids),
                           tier=sum(i[:5] == slot_id[:5] for i in ids))
        except Exception as exc:
            row["error"] = f"{type(exc).__name__}: {exc}"
        out.append(row)
        engine.push(action)
    return out


# ---------------------------------------------------------------------------------------------------- scoring


def score_positions(xp, logits, tokens: Sequence[int], sets: Sequence[Sequence[int] | None], temperature: float,
                    k: int = 5) -> dict[str, np.ndarray]:
    """For each row of ``logits`` (rows = positions, predicting ``tokens``): top-k token ids and log-probs over the
    whole vocabulary, the chosen token's unmasked log-prob, and its masked behaviour log-prob over ``sets`` (NaN
    where the set is unknown). ``xp`` is ``numpy`` or ``mlx.core`` (same arithmetic; the MLX path reduces on GPU).

    T > 0: log-softmax of logits/T (the sampling distribution). T <= 0 (greedy) follows generation's deterministic
    rule (``mask_mlx._behavior_logprobs``): the first maximum in allowed order scores 0, every other token -inf; the
    unmasked top-k display then uses T = 1 (``display_temperature``)."""
    greedy = temperature <= 0
    T = 1.0 if greedy else temperature
    scaled = logits.astype(xp.float32) / T
    m = xp.max(scaled, axis=-1, keepdims=True)
    lse = m + xp.log(xp.sum(xp.exp(scaled - m), axis=-1, keepdims=True))
    logp = scaled - lse
    part = xp.argpartition(-scaled, kth=k - 1, axis=-1)[:, :k]
    top_lp = xp.take_along_axis(logp, part, axis=-1)
    tok = xp.array(list(tokens))[:, None]
    chosen = xp.take_along_axis(logp, tok, axis=-1)[:, 0]
    width = max(1, max((len(s) for s in sets if s), default=1))
    idx = np.zeros((len(sets), width), dtype=np.int32)
    valid = np.zeros((len(sets), width), dtype=bool)
    for r, s in enumerate(sets):
        if s:
            idx[r, :len(s)] = s
            idx[r, len(s):] = s[0]
            valid[r, :len(s)] = True
    sel = xp.where(xp.array(valid), xp.take_along_axis(scaled, xp.array(idx), axis=-1), -math.inf)
    if greedy:
        first = xp.argmax(sel, axis=-1)[:, None]  # first maximum in allowed order
        pick = xp.take_along_axis(xp.array(idx), first, axis=-1)
        masked = xp.where(pick == tok, 0.0, -math.inf)
    else:
        ms = xp.max(sel, axis=-1, keepdims=True)
        lse_m = ms + xp.log(xp.sum(xp.exp(sel - ms), axis=-1, keepdims=True))
        masked = xp.take_along_axis(scaled, tok, axis=-1) - lse_m
    out = {"top_ids": part, "top_lp": top_lp, "chosen_lp": chosen, "masked_lp": masked[:, 0]}
    out = {key: np.array(v) for key, v in out.items()}
    out["masked_lp"] = np.where([bool(s) for s in sets], out["masked_lp"], np.nan)
    out["display_temperature"] = np.array(T)
    return out


def mlx_replay_scores(model, prompt: Sequence[int], tokens: Sequence[int], sets, temperature: float,
                      chunk: int = 2048) -> dict[str, np.ndarray]:
    """Teacher-forced replay through the MLX model (prefill as in ``mask_mlx``), reduced per chunk on the GPU."""
    import mlx.core as mx

    from llm.stowlm.mask_mlx import prefill

    cache = prefill(model, prompt)
    seq = [prompt[-1], *tokens[:-1]]
    parts = []
    for s in range(0, len(seq), chunk):
        logits = model(mx.array(seq[s:s + chunk])[None], cache=cache)[0]
        parts.append(score_positions(mx, logits, tokens[s:s + chunk], sets[s:s + chunk], temperature))
    out = {key: np.concatenate([p[key] for p in parts]) for key in parts[0] if key != "display_temperature"}
    out["display_temperature"] = parts[0]["display_temperature"]
    return out


def build_trace(instance, recording: RecordingDecoder, scores: dict[str, np.ndarray], *, decode_token,
                encode_action, n_slots: int | None, token_text=None, exact: Sequence[dict] | None = None) -> list[dict]:
    """One trace row per decoded action of the selected sample, then either the real end-of-plan sequence (decoder
    status ``complete``) or a ``partial`` row for an unfinished action (timeout, max tokens, dead end).

    Each action row has ``decisions``: for the first token of the box, bay, row and tier fields, its position in the
    action, the allowed-set size, the exact legal count consistent with the choices so far (``exact``, from the
    replay; None without it), the model's top-k before masking and whether the mask overrode its top-1. The row's
    ``top_k`` is the box token's; an overridden opcode is ``format_override`` (run contract, amended 29 Oct)."""
    steps = recording.steps
    starts = [i for i, s in enumerate(steps) if s.get("boundary")] or ([0] if steps else [])
    actions = list(recording.actions) if steps else []
    status = recording.status
    n_boxes = len(instance.load_list)
    display_t = float(scores.get("display_temperature", np.array(1.0)))

    def top_k(pos: int) -> list[dict]:
        allowed = set(steps[pos]["allowed"] or ())
        order = np.argsort(-scores["top_lp"][pos])
        return [{"text": decode_token([int(scores["top_ids"][pos][j])]), "logprob": float(scores["top_lp"][pos][j]),
                 "legal": int(scores["top_ids"][pos][j]) in allowed} for j in order]

    def overridden(pos: int) -> bool:
        best = int(scores["top_ids"][pos][int(np.argmax(scores["top_lp"][pos]))])
        return best not in set(steps[pos]["allowed"] or ())

    rows, t_prev = [], recording.t0
    for k, start in enumerate(starts):
        end = starts[k + 1] if k + 1 < len(starts) else len(steps)
        toks = [steps[i]["token"] for i in range(start, end)]
        fields = token_fields([token_text(t) for t in toks]) if token_text else [None] * len(toks)
        is_action = k < len(actions)
        is_eos = not is_action and k == len(starts) - 1 and status == "complete"
        masked = scores["masked_lp"][start:end]
        if is_action:
            chosen = {"action_id": encode_action(actions[k]), "text": decode_token(toks)}
        elif is_eos:
            chosen = {"action_id": None, "text": f"<end of plan> {decode_token(toks)}", "end_of_plan": True}
        else:
            chosen = {"action_id": None, "text": f"<partial action: {status}> {decode_token(toks)}", "partial": True}
        counts = exact[k] if exact is not None and k < len(exact) and is_action else {}
        decisions = [{"position": off, "field": f, "n_allowed": len(steps[start + off]["allowed"] or ()),
                      "n_legal": counts.get(f), "top_k": top_k(start + off), "overridden": overridden(start + off)}
                     for off, f in enumerate(fields)
                     if f in DECISION_FIELDS and (off == 0 or fields[off - 1] != f)]
        box = next((d for d in decisions if d["field"] == "box"), None)
        opcode = [off for off, f in enumerate(fields) if f == "opcode"]
        rows.append({
            "step": k,
            "n_legal": counts.get("n_legal"),
            "n_actions": counts.get("n_possible"),
            "n_actions_scope": "physically possible Place(box, slot): liftable box x open slot of its length class",
            "n_place_pairs_raw": (max(0, n_boxes - k) * n_slots) if (n_slots and is_action) else None,
            "chosen": chosen,
            "chosen_logprob": None if np.isnan(masked).any() else float(masked.sum()),
            "chosen_logprob_unmasked": float(scores["chosen_lp"][start:end].sum()),
            "decisions": decisions,
            "top_k": box["top_k"] if box else top_k(start),
            "top_k_field": "box" if box else (fields[0] if fields else None),
            "format_override": overridden(start + opcode[0]) if opcode else None,
            "top_k_temperature": display_t,
            "mask_ms": round(sum(steps[i]["mask_ms"] for i in range(start, end)), 3),
            "decode_ms": round((steps[end - 1]["t"] - t_prev) * 1e3, 3),
        })
        t_prev = steps[end - 1]["t"]
    return rows


def merge_mask_trace(rows: list[dict], mask_rows: list[dict]) -> list[dict]:
    """Merge the 5.1 mask's per-sample ``decode_trace`` rows (``gen_decode_trace``, filtered to one sample) into
    trace rows: the k-th ``action`` event's screens go to the k-th decoded action row (its source state), and each
    ``dead_end`` event becomes a terminal row with its rule attribution, also when no token was decoded (a root
    dead end). Rows without a mask event keep ``screens: None`` (diagnostics are collected only with rule freeze v1
    on)."""
    actions = [r for r in mask_rows if r.get("event") == "action"]
    out, k = [], 0
    for row in rows:
        row = dict(row)
        is_action = row["chosen"].get("action_id") is not None
        row["event"] = ("action" if is_action else "end_of_plan" if row["chosen"].get("end_of_plan") else "partial")
        if is_action:
            row["screens"] = actions[k].get("screens") if k < len(actions) else None
            k += 1
        out.append(row)
    for dead in (r for r in mask_rows if r.get("event") == "dead_end"):
        out.append({"step": len(out), "event": "dead_end", "depth": dead.get("depth"), "n_legal": 0,
                    "dead_end": dead.get("dead_end"), "dead_ends_by_rule": dead.get("dead_ends_by_rule") or {},
                    "screens": dead.get("screens")})
    return out


def write_trace(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text("".join(json.dumps(r, default=str) + "\n" for r in rows))
    tmp.replace(path)
