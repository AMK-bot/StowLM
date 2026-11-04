"""One-core warm mask CPU probe, runnable directly; no GPU or timing assertions.

Usage: OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 VECLIB_MAXIMUM_THREADS=1 \
    uv run python tests/llm/test_mask_cost.py --rung all --repeats 3

C2 is off until requalification. Tiny/small use L4's seeded native feeder500
fixtures (no hull); medium/full use L4's mixed 20/40, bottom-up mid-plan feeder500
workloads retaining hull, bridge and fixed background. These are timing inputs,
not a full-checker qualification. Each switch sees the exact same instance.
Construction and one first-action warm-up are excluded. Each measured sample
has a fresh engine, with CPU tokenizer/field caches warm. Domain time includes
all allowed-token queries and token advances up to the final newline; commit
measures the final advance's engine copy/push. Exact-count overhead is measured
separately after domain selection, before commit; it forces all action pairs.
"""
from __future__ import annotations

import argparse
from functools import lru_cache
import importlib.util
import json
from pathlib import Path
import resource
import signal
from time import perf_counter

from llm.stowlm.format import ActionCodec, load_tokenizer
from llm.stowlm.mask import MaskedDecoder
from stowage.actions import get_action_space
from stowage.feasibility import FeasibilityConfig, make_feasibility_engine
from stowage.rules.catalogue import rule_freeze_v1

ROOT = Path(__file__).resolve().parents[2]


@lru_cache(maxsize=2)
def _module(name, path):
    spec = importlib.util.spec_from_file_location(name, ROOT / path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class ProbeTimeout(BaseException):
    """Stop cap survives checker exception-to-verdict handling."""


def _expired(*args):
    raise ProbeTimeout('20s stop cap')


def probe(rung: str, repeats: int = 3) -> dict:
    with rule_freeze_v1(enabled=False):
        if rung in ('tiny', 'small'):
            fixture = _module('mask_cost_native', 'tests/feasibility/test_w2_agreement.py')
            instance = fixture._case('feeder_500', rung, 8, 23)[0]
        else:
            fixture = _module('mask_cost_large', 'tests/feasibility/w2_performance_probe.py')
            instance = fixture.make_performance_instance(rung)
    row = dict(rung=rung, cells=len(instance.vessel.cells), pending=len(instance.load_list),
               onboard=len(instance.onboard.placements), c2=False, crane_h=8)
    codec = ActionCodec(load_tokenizer(), [c.id for c in instance.containers])
    token_cache = None
    for active in (False, True):
        modes = {}
        for exact_count in (False, True):
            samples = []
            for repeat in range(repeats + 1):
                with rule_freeze_v1(enabled=active):
                    signal.alarm(20)
                    try:
                        engine = make_feasibility_engine(instance, get_action_space('boxslot'),
                            config=FeasibilityConfig(extra={'global_screen': 0}))
                        decoder = MaskedDecoder(engine, codec, token_cache=token_cache)
                        token_cache = decoder._cache
                        start = perf_counter()
                        tokens = 0
                        while decoder.status == 'running':
                            allowed = decoder.allowed_token_ids()
                            if not allowed:
                                raise AssertionError((decoder.status, decoder.dead_end))
                            token = allowed[0]
                            # At an action leaf, the next advance performs copy/push.
                            if decoder._node.children[token].action is not None:
                                break
                            decoder.advance(token)
                            tokens += 1
                        domain_ms = 1000 * (perf_counter() - start)
                        start = perf_counter()
                        n_legal = decoder.n_legal if exact_count else None
                        count_ms = 1000 * (perf_counter() - start) if exact_count else 0.
                        start = perf_counter()
                        action = decoder.advance(token)
                        commit_ms = 1000 * (perf_counter() - start)
                        assert action is not None
                        if repeat:
                            samples.append(dict(domain_ms=domain_ms, count_ms=count_ms,
                                commit_ms=commit_ms, total_ms=domain_ms+count_ms+commit_ms))
                    finally:
                        signal.alarm(0)
            means = {key: sum(sample[key] for sample in samples)/len(samples) for key in samples[0]}
            modes['with_count' if exact_count else 'decode'] = dict(
                samples=samples, mean=means, tokens=tokens+1, chosen=repr(action), n_legal=n_legal)
        row['on' if active else 'off'] = modes
    row['rss_mb'] = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 2**20
    return row


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--rung', choices=['tiny', 'small', 'medium', 'full', 'all'], default='all')
    parser.add_argument('--repeats', type=int, default=3)
    args = parser.parse_args()
    signal.signal(signal.SIGALRM, _expired)
    for rung in (['tiny', 'small', 'medium', 'full'] if args.rung == 'all' else [args.rung]):
        print(json.dumps(probe(rung, args.repeats)), flush=True)


if __name__ == '__main__':
    main()
