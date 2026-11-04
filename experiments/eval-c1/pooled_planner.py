"""StowLMPlanner whose per-sample mask decoders run in a ``pool.MaskWorkerPool`` (experiments only).

The planner's logic (prompt, ``generate_masked``, best-of-k with ballast + full checker, dead-end/timeout handling)
is unchanged: the pool plugs in through ``StowLMPlanner``'s ``decoder_factory`` hook (8.1, 28 Oct). The pool starts
lazily on the first plan, so constructing the planner (e.g. for its evaluation identity) spawns nothing.
Workers produce exactly the allowed sets the in-thread decoder would (tests/test_pool.py checks this), so sampled
plans are unchanged for a given seed; only where the host work runs differs.
"""

from __future__ import annotations

import time
from typing import Any

from llm.stowlm.planner import StowLMPlanner

from pool import MaskWorkerPool, PooledDecoderFactory
from decode_trace import (ArgmaxLog, RecordingFactory, build_trace, exact_counts, merge_mask_trace,
                          mlx_replay_scores, override_stats, write_trace)


class PooledStowLMPlanner(StowLMPlanner):
    def __init__(self, *, mask_workers: int, c2: bool, trace_dir: str | None = None, trace_exact: bool = True,
                 base_factory=None, engine_wrapper=None, **kw: Any) -> None:
        super().__init__(**kw)
        # base_factory: an extra in-thread mask layer (e.g. llm.securing's securing_decoder_factory, D95);
        # engine_wrapper: the same layer applied to the trace's exact-count replay engine.
        self.engine_wrapper = engine_wrapper
        if base_factory is not None and mask_workers:
            raise ValueError("an extra mask layer (base_factory) runs in-thread only; use mask_workers=0")
        self.trace_exact = trace_exact  # exact legal counts in the selected sample's trace (replay, not the decode)
        self.trace_dir = trace_dir  # run folder: writes instances/<instance_id>/trace.jsonl for every plan
        self.mask_workers = int(mask_workers)
        self.c2 = bool(c2)
        self._pool: MaskWorkerPool | None = None
        if self.mask_workers:  # fixed at construction so the evaluation identity never changes after a plan
            planner = self

            def factory(instance, engine, codec, mask_config, n):
                return PooledDecoderFactory(planner.pool(), planner.engine_config)(instance, engine, codec,
                                                                                  mask_config, n)
            factory.name = f"process-pool-{self.mask_workers}"
        else:
            factory = base_factory
        self.mask_layer = getattr(base_factory, "name", None)
        # Recording is transparent (same allowed sets, same tokens); it only adds the per-token trace.
        self.decoder_factory = self._recorder = RecordingFactory(factory)

    @property
    def num_workers(self) -> int:  # the 5.2 harness counts planner-internal CPU workers against its pool cap
        return max(1, self.mask_workers)

    @property
    def evaluation_config(self) -> dict[str, Any]:
        config = dict(super().evaluation_config)
        config["mask_backend"] = f"process-pool-{self.mask_workers}" if self.mask_workers else "in-thread"
        if self.mask_layer:
            config["mask_layer"] = self.mask_layer
        return config

    def _load(self):
        """The loaded model, wrapped so every batched decode step logs each row's unmasked argmax (for the
        mask-override rate over all samples). ``self._model`` stays the bare model (trace replay uses it)."""
        self._argmax_log = ArgmaxLog(super()._load())
        return self._argmax_log

    def pool(self) -> MaskWorkerPool:
        if self._pool is None:
            from stowage.rules.catalogue import rule_freeze_v1_enabled

            # Workers follow the parent's effective switch (a rule_freeze_v1 context doesn't cross spawn).
            self._pool = MaskWorkerPool(self.mask_workers, c2=self.c2, rule_freeze=rule_freeze_v1_enabled())
        return self._pool

    def plan(self, instance, *, seed: int = 0, time_limit_s: float | None = None):
        pool = self.pool() if self.mask_workers else None
        wait0 = pool.wait_s if pool else 0.0
        t = time.perf_counter()
        result = super().plan(instance, seed=seed, time_limit_s=time_limit_s)
        metrics = dict(result.metrics)
        metrics["pool_plan_wall_s"] = round(time.perf_counter() - t, 3)
        if pool is not None:
            try:
                stats = pool.worker_stats()
            except Exception:  # a crashed worker already surfaced as an error result
                stats = []
            metrics.update(pool_workers=self.mask_workers, pool_parent_wait_s=round(pool.wait_s - wait0, 3),
                           pool_worker_busy_s=[round(s["busy_s"], 3) for s in stats],
                           pool_worker_prefetch_s=[round(s["prefetch_s"], 3) for s in stats])
        log = getattr(self, "_argmax_log", None)
        if metrics.get("gen_decode_mode") == "sequential-backtracking":
            # rewinds break the batched argmax-row alignment; the decode loop reports its own override rates
            metrics["mask_override_scope"] = "backtracking decode: gen_override_rate_all_steps / _action_start"
            if log is not None:
                log.steps = []
        elif log is not None and self._recorder.last:
            try:
                metrics.update(override_stats(self._recorder.last, log.rows(), token_text=self._token_text()))
            except Exception as exc:  # diagnostics never change the result
                metrics["override_error"] = f"{type(exc).__name__}: {exc}"
            log.steps = []
        if self.trace_dir is not None:
            metrics.update(self._write_trace(instance, metrics))
        # The mask's per-action decode trace is in trace.jsonl now; result.json keeps the per-rule counts only.
        metrics.pop("gen_decode_trace", None)
        return result.model_copy(update={"metrics": metrics})

    def _token_text(self):
        from llm.stowlm.format import load_tokenizer

        tokenizer = load_tokenizer()
        cache: dict[int, str] = {}

        def text(token: int) -> str:
            if token not in cache:
                cache[token] = tokenizer.decode([int(token)])
            return cache[token]
        return text

    def _write_trace(self, instance, metrics: dict) -> dict:
        """Trace of the selected sample (contract: experiments/fleet/orders/w2-stowlm-run-contract.md). Never
        changes the result; failures are reported in metrics and leave an empty trace."""
        from pathlib import Path

        path = Path(self.trace_dir) / "instances" / instance.id / "trace.jsonl"
        chosen = metrics.get("chosen_sample")
        decoders = self._recorder.last
        mask_rows = [r for r in metrics.get("gen_decode_trace") or [] if r.get("sample") == chosen]
        if chosen is None or chosen >= len(decoders) or self._model is None or not decoders[chosen].steps:
            rows = merge_mask_trace([], mask_rows)  # a root dead end still gets its rule-attributed row
            write_trace(path, rows)
            return {"trace_rows": len(rows), "trace_note": "no decoded tokens in the selected sample"}
        try:
            from llm.stowlm.format import load_tokenizer
            from llm.stowlm.format_prompt import encode_prompt
            from stowage.actions import get_action_space

            t = time.perf_counter()
            rec = decoders[chosen]
            tokenizer, space = load_tokenizer(), get_action_space()
            prompt = encode_prompt(instance, tokenizer)
            tokens = [s["token"] for s in rec.steps]
            scores = mlx_replay_scores(self._model, prompt, tokens, [s["allowed"] for s in rec.steps],
                                       self.temperature)
            exact, t_exact = None, time.perf_counter()
            if self.trace_exact:
                from stowage.feasibility import make_feasibility_engine

                engine = make_feasibility_engine(instance, space, config=self.engine_config)
                exact = exact_counts(self.engine_wrapper(engine) if self.engine_wrapper else engine,
                                     list(rec.actions))
            t_exact = time.perf_counter() - t_exact
            rows = build_trace(instance, rec, scores, decode_token=lambda ids: tokenizer.decode(list(ids)),
                               encode_action=space.encode, n_slots=self._recorder.n_slots,
                               token_text=self._token_text(), exact=exact)
            rows = merge_mask_trace(rows, mask_rows)
            write_trace(path, rows)
            return {"trace_rows": len(rows), "trace_replay_s": round(time.perf_counter() - t, 3),
                    "trace_exact_counts_s": round(t_exact, 3) if self.trace_exact else None}
        except Exception as exc:
            write_trace(path, [])
            return {"trace_rows": 0, "trace_error": f"{type(exc).__name__}: {exc}"}

    def close(self) -> None:
        if self._pool is not None:
            self._pool.close()
            self._pool = None
