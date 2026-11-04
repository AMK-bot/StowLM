"""Process-pool mask backend for C1/6.2 evaluation (experiments only; see SRC_PATCH.md for the src/ hook).

Why: ``generate_masked`` computes every row's allowed set in ONE host thread per GPU step. At an action boundary each
row pays one feasibility-engine step (reference medians with C2 on: ~2 ms tiny, 77 ms small, 200 ms medium, 1.4 s
full; experiments/feasibility/perf-1028/isolation/report.md), so at batch B the step's host work is ~B × that,
serialised behind the GIL — far longer than the ~0.1–0.3 s GPU forward pass from small up.

How: each sampled sequence's ``MaskedDecoder`` lives in a worker *process* (spawned: MLX/Metal in the parent must not
be forked). ``RemoteDecoder`` is a drop-in proxy with the attributes ``generate_masked`` and ``StowLMPlanner.plan``
use. The protocol is push-based so the parent never serialises on round trips:

    parent: advance(row, tok)  ->  worker: decoder.advance(tok); s = decoder.allowed_token_ids(); push (row, s, status)
    parent: allowed_token_ids() = wait for that row's pushed reply (already computed in parallel by W workers)

Workers compute while the parent samples, feeds the other rows and runs the next forward pass; while idle they call
``decoder.prefetch()`` (builds successor states of already-determined actions; never changes an allowed set).

One known cost: the proxy learns that an ``advance`` completed the plan only from the worker's next reply, so a
finished row is dropped one GPU step later than with the in-thread decoder (``generate_masked`` sees an empty
allowed set and records the decoder's final status). That is one extra forward row per finished sequence, no change
to sampled tokens or log-probs.
"""

from __future__ import annotations

import multiprocessing as mp
import time
from dataclasses import dataclass
from typing import Any

STUB_REASON = ("C1 dry run: 3.7 linear policy epoch may be stale during requalification, so the engine's C2 global "
               "screen is off (extra global_screen=0); L1/L2 local checks and C1 stay on; the full checker judges "
               "every plan")


def c2_off_engine(instance, space, *, config=None):
    from dataclasses import replace

    from stowage.feasibility import FeasibilityConfig
    from stowage.feasibility._engine import IncrementalEngine

    c = config or FeasibilityConfig()
    return IncrementalEngine(instance, space, replace(c, extra={**c.extra, "global_screen": 0}))


# ------------------------------------------------------------------------------------------------------- worker


def completing_tokens(d, allowed) -> tuple[int, ...]:
    """Allowed tokens that would finish the plan, by ``MaskedDecoder.advance``'s own rule: every box is placed (the
    state view is complete) and the token reaches a leaf of the end-of-plan trie. The proxy uses it to report
    ``complete`` synchronously in ``advance``, as the in-thread decoder does (code review w2-rev-c1, High)."""
    if d.status != "running" or not allowed or d._node is None or not d._view().complete:
        return ()
    return tuple(t for t in allowed if not d._node.children[t].children)


def action_tokens(d, allowed) -> tuple[int, ...]:
    """Allowed tokens that complete an action, by ``MaskedDecoder.advance``'s rule: the child carries an action and
    no box group (a group head continues into that box's tail trie). The proxy's ``advance`` then returns a marker,
    so ``generate_masked`` records the per-action screen row exactly as for an in-thread decoder."""
    if d.status != "running" or not allowed or d._node is None or d._view().complete:
        return ()
    return tuple(t for t in allowed if d._node.children[t].group is None and d._node.children[t].action is not None)


def reply_payload(d, allowed, *, physically_possible) -> dict:
    """Everything the parent proxy needs after one allowed-set query: the set itself, status and terminal dead end
    (with rule ids: 5.1 per-rule attribution), trace counts and screens at an action boundary, and which tokens
    complete an action or the plan."""
    boundary = d._prefix == [] and d.status == "running"
    dead = d.dead_end
    screens = getattr(d.engine, "screen_diagnostics", None) if boundary else None
    return {"allowed": tuple(allowed), "status": d.status, "depth": d.engine.depth,
            "dead_end": None if dead is None else {"depth": dead.depth, "reason": dead.reason,
                                                   "rule_ids": sorted(set(dead.rule_ids))},
            # no exact legal count here (logic-mask High 2): it's computed only when replaying the selected sample
            "boundary": boundary,
            "screens": screens() if callable(screens) else None,
            "completing": completing_tokens(d, allowed), "action_tokens": action_tokens(d, allowed)}



def _worker(conn, c2: bool, rule_freeze: bool | None = None) -> None:
    import stowage.interfaces  # noqa: F401  (registers frozen implementations)
    from decode_trace import physically_possible
    from llm.stowlm.format import load_tokenizer
    from llm.stowlm.mask import MaskedDecoder
    from llm.stowlm.sft_data import codec_for
    from stowage.actions import get_action_space
    from stowage.feasibility import make_feasibility_engine
    from stowage.frozen import stub_override
    from stowage.model import Instance

    override = None
    if not c2:  # same override as the parent, so engines and plan provenance carry the same taint
        override = stub_override("feasibility.make_engine", c2_off_engine, reason=STUB_REASON)
        override.__enter__()  # held for the worker's lifetime (an unreferenced context would be collected and exit)
    freeze = None
    if rule_freeze is not None:  # the parent's effective activation switch, even when set by a context override
        from stowage.rules.catalogue import rule_freeze_v1, rule_freeze_v1_enabled

        if rule_freeze_v1_enabled() != rule_freeze:
            freeze = rule_freeze_v1(enabled=rule_freeze)
            freeze.__enter__()  # held for the worker's lifetime, like the C2 override
    tokenizer, space = load_tokenizer(), get_action_space()
    decoders: dict[int, Any] = {}
    busy = 0.0  # seconds spent in decoder work (advance + allowed sets + prefetch)
    prefetch_s = 0.0

    def reply(row: int) -> None:
        d = decoders[row]
        allowed = d.allowed_token_ids()
        conn.send(("allowed", row, reply_payload(d, allowed, physically_possible=physically_possible)))

    while True:
        if not conn.poll(0):
            # idle: prefetch determined successors, one row at a time, until a message arrives
            worked = False
            for d in list(decoders.values()):
                if conn.poll(0):
                    break
                t = time.perf_counter()
                if d.prefetch():
                    worked = True
                dt = time.perf_counter() - t
                busy += dt
                prefetch_s += dt
            if not worked:
                conn.poll(None)
            continue
        msg = conn.recv()
        kind = msg[0]
        t = time.perf_counter()
        try:
            if kind == "init":
                _, instance_json, rows, engine_config, mask_config = msg
                instance = Instance.model_validate_json(instance_json)
                engine = make_feasibility_engine(instance, space, config=engine_config)
                codec = codec_for(instance, tokenizer)
                decoders = {r: MaskedDecoder(engine if k == 0 else engine.copy(), codec, mask_config)
                            for k, r in enumerate(rows)}
                busy, prefetch_s = 0.0, 0.0
                for r in rows:
                    reply(r)
            elif kind == "advance":
                _, row, tok = msg
                decoders[row].advance(tok)
                reply(row)
            elif kind == "final":
                _, row = msg
                d = decoders[row]
                plan = d.engine.to_plan() if d.status == "complete" else None
                conn.send(("final", row, {"actions": d.actions, "plan": plan, "status": d.status}))
            elif kind == "stats":
                conn.send(("stats", None, {"busy_s": busy, "prefetch_s": prefetch_s}))
            elif kind == "release":
                decoders = {}
            elif kind == "stop":
                for ctx in (freeze, override):
                    if ctx is not None:
                        ctx.__exit__(None, None, None)
                return
        except Exception as exc:  # surface in the parent as an error result, never a hang
            conn.send(("error", msg[1] if len(msg) > 1 and isinstance(msg[1], int) else None,
                       f"{type(exc).__name__}: {exc}"))
        if kind in ("init", "advance"):
            busy += time.perf_counter() - t


# ------------------------------------------------------------------------------------------------------- parent


@dataclass
class _DeadEnd:
    reason: str
    depth: int | None = None
    rule_ids: tuple[str, ...] = ()


class _ActionMarker:
    """Truthy stand-in for the Action a completing token produced (the Action itself lives in the worker)."""


class _RemoteEngine:
    def __init__(self, decoder: RemoteDecoder) -> None:
        self._d = decoder

    def to_plan(self):
        plan = self._d._final()["plan"]
        if plan is None:
            raise ValueError("plan is not complete")
        return plan

    def actions(self):
        return self._d._final()["actions"]

    @property
    def depth(self) -> int | None:
        return self._d._depth

    def screen_diagnostics(self) -> dict | None:
        """Screens of the state the proxy last queried (an action's source state until the next reply)."""
        return self._d.boundary_screens


class RemoteDecoder:
    """Proxy for ``MaskedDecoder`` whose state lives in a pool worker (see module docstring)."""

    def __init__(self, pool: MaskWorkerPool, worker: int, row: int) -> None:
        self._pool, self._w, self._row = pool, worker, row
        self.tokens: list[int] = []
        self.status = "running"
        self.dead_end: _DeadEnd | None = None
        self.at_boundary = False  # the current position starts an action
        self.boundary_screens: dict | None = None  # engine screen diagnostics at that boundary (5.1 attribution)
        self._depth: int | None = None
        self._allowed: tuple[int, ...] | None = None
        self._completing: tuple[int, ...] = ()
        self._action_tokens: tuple[int, ...] = ()
        self._final_cache: dict | None = None

    def allowed_token_ids(self) -> tuple[int, ...]:
        if self._allowed is None:
            p = self._pool._await(self._w, self._row)
            self._allowed, self.status, self._depth = tuple(p["allowed"]), p["status"], p.get("depth")
            self.at_boundary = bool(p.get("boundary"))
            if self.at_boundary:
                self.boundary_screens = p.get("screens")
            self._completing, self._action_tokens = tuple(p.get("completing", ())), tuple(p.get("action_tokens", ()))
            dead = p.get("dead_end")
            self.dead_end = None if dead is None else _DeadEnd(dead["reason"], dead.get("depth"),
                                                                tuple(dead.get("rule_ids", ())))
        return self._allowed

    def advance(self, token_id: int):
        if self.status != "running":
            raise ValueError(f"advance on a {self.status} decoder")
        self.tokens.append(int(token_id))
        # generate_masked reads status right after advance (a sample can finish on its last allowed token);
        # MaskedDecoder sets "complete" inside advance, so the proxy must too, from the worker's completing set.
        terminal = self._allowed is not None and int(token_id) in self._completing
        completes_action = self._allowed is not None and int(token_id) in self._action_tokens
        self._allowed = None
        self._final_cache = None
        self._pool._send(self._w, ("advance", self._row, int(token_id)))
        if terminal:
            self.status = "complete"
        if completes_action:  # the Action itself stays in the worker; generate_masked only needs "an action"
            self._depth = (self._depth or 0) + 1
            return _ActionMarker()
        return None

    def diagnostics(self) -> dict:
        """``MaskedDecoder.diagnostics`` from the worker's last reply, without a round trip: status, depth, the
        terminal dead end with its rule ids, and the screens of the last queried boundary state."""
        dead = self.dead_end
        rules = sorted(set(dead.rule_ids)) if dead is not None else []
        return {"status": self.status, "depth": self._depth,
                "dead_end": None if dead is None else {"depth": dead.depth, "reason": dead.reason, "rule_ids": rules},
                "dead_ends_by_rule": {rule: 1 for rule in rules}, "screens": self.boundary_screens}

    def prefetch(self) -> bool:
        return False  # workers prefetch on their own while idle

    def _final(self) -> dict:
        if self._final_cache is None:
            self.allowed_token_ids()  # drain the pending reply first (keeps the pipe in order)
            self._pool._send(self._w, ("final", self._row))
            self._final_cache = self._pool._await(self._w, self._row, kind="final")
        return self._final_cache

    @property
    def engine(self) -> _RemoteEngine:
        return _RemoteEngine(self)

    @property
    def actions(self) -> list:
        return self._final()["actions"]


class MaskWorkerPool:
    """``n_workers`` spawned processes; ``decoders(instance, n)`` returns n proxies, row i on worker i % W."""

    def __init__(self, n_workers: int, *, c2: bool, rule_freeze: bool | None = None) -> None:
        if n_workers < 1:
            raise ValueError("n_workers must be >= 1")
        ctx = mp.get_context("spawn")
        self.c2 = c2
        self._conns, self._procs = [], []
        for _ in range(n_workers):
            parent, child = ctx.Pipe()
            p = ctx.Process(target=_worker, args=(child, c2, rule_freeze), daemon=True)
            p.start()
            self._conns.append(parent)
            self._procs.append(p)
        self._inbox: list[dict] = [{} for _ in range(n_workers)]
        self.wait_s = 0.0  # parent time blocked waiting for worker replies

    @property
    def n_workers(self) -> int:
        return len(self._conns)

    def _send(self, w: int, msg) -> None:
        self._conns[w].send(msg)

    def _await(self, w: int, row: int, kind: str = "allowed"):
        key = (kind, row)
        t = time.perf_counter()
        while key not in self._inbox[w]:
            k, r, *payload = self._conns[w].recv()
            if k == "error":
                self.wait_s += time.perf_counter() - t
                raise RuntimeError(f"mask worker {w}: {payload[0]}")
            self._inbox[w][(k, r)] = payload
        self.wait_s += time.perf_counter() - t
        payload = self._inbox[w].pop(key)
        return payload[0]

    def decoders(self, instance, n: int, *, engine_config=None, mask_config=None) -> list[RemoteDecoder]:
        for inbox in self._inbox:
            inbox.clear()
        rows_by_worker = [[r for r in range(n) if r % self.n_workers == w] for w in range(self.n_workers)]
        blob = instance.model_dump_json()
        for w, rows in enumerate(rows_by_worker):
            self._send(w, ("init", blob, rows, engine_config, mask_config) if rows else ("release",))
        return [RemoteDecoder(self, r % self.n_workers, r) for r in range(n)]

    def worker_stats(self) -> list[dict]:
        out = []
        for w in range(self.n_workers):
            self._send(w, ("stats",))
            out.append(self._await(w, None, kind="stats"))
        return out

    def close(self) -> None:
        for c in self._conns:
            try:
                c.send(("stop",))
            except (OSError, BrokenPipeError):
                pass
        for p in self._procs:
            p.join(timeout=10)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()


class PooledDecoderFactory:
    """``StowLMPlanner(decoder_factory=...)`` backend: n remote decoders for one instance on the pool."""

    def __init__(self, pool: MaskWorkerPool, engine_config=None) -> None:
        self.pool, self.engine_config = pool, engine_config
        self.name = f"process-pool-{pool.n_workers}"

    def __call__(self, instance, engine, codec, mask_config, n):
        return self.pool.decoders(instance, n, engine_config=self.engine_config, mask_config=mask_config)
