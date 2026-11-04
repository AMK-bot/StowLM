"""Frozen first-fit conditional-logit arm; no training or validation-time tuning."""
from __future__ import annotations

import hashlib
import ast
import importlib.util
import json
import os
from pathlib import Path
import random
import sys
import time

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[3]
for name in ('OMP_NUM_THREADS', 'OPENBLAS_NUM_THREADS', 'MKL_NUM_THREADS',
             'VECLIB_MAXIMUM_THREADS', 'NUMEXPR_NUM_THREADS', 'BLIS_NUM_THREADS',
             'RAYON_NUM_THREADS', 'POLARS_MAX_THREADS'):
    os.environ[name] = '1'
os.environ.update(TOKENIZERS_PARALLELISM='false', CUDA_VISIBLE_DEVICES='',
                  COMPACT_FINISHER_S='600', UV_CACHE_DIR=str(ROOT/'.uv-cache'))
os.environ.pop('STOWAGE_BALLAST_V4', None)
sys.path[:0] = [str(ROOT/'src'), str(ROOT), str(HERE.parent), str(HERE.parent/'k1b')]
sys.dont_write_bytecode = True

import numpy as np

WEIGHTS_SHA256 = 'da15edecf1061c2e2014317ccaf53190f6c2997b5f225f8ca39bd5174a6559d6'
FIT_PATH = HERE/'frozen-source/fitted.json'
if hashlib.sha256(FIT_PATH.read_bytes()).hexdigest() != WEIGHTS_SHA256:
    raise RuntimeError('frozen weights changed')
FIT = json.loads(FIT_PATH.read_text())
W = np.array(FIT['w'])
spec = importlib.util.spec_from_file_location('frozen_rule_features', HERE/'frozen-source/rf.py')
features = importlib.util.module_from_spec(spec)
spec.loader.exec_module(features)
if FIT['names'] != features.NAMES:
    raise RuntimeError('feature order differs from frozen weights')

_TOKENIZER = None


def cpu_forbidding_decoder():
    """Load the production class verbatim without importing MLX's unrelated model helpers."""
    source = ROOT/'src/llm/stowlm/backtrack.py'
    tree = ast.parse(source.read_text(), filename=str(source))
    cls = next(node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == 'ForbiddingDecoder')
    module = ast.Module(body=[ast.ImportFrom(module='__future__', names=[ast.alias(name='annotations')], level=0), cls], type_ignores=[])
    namespace = {}
    exec(compile(ast.fix_missing_locations(module), str(source), 'exec'), namespace)
    return namespace['ForbiddingDecoder']


def decode_instance(inst, seed=0, greedy=True):
    """Exact scratch fitgreedy/fitT1 chooser and rewind caps (32, 8, 64)."""
    global _TOKENIZER
    from llm.lookahead import lookahead_decoder_factory
    ForbiddingDecoder = cpu_forbidding_decoder()
    from llm.stowlm.format import load_tokenizer
    from llm.stowlm.mask import MaskConfig
    from llm.stowlm.sft_data import codec_for
    from stowage.actions import get_action_space
    from stowage.feasibility import FeasibilityConfig, make_feasibility_engine

    started = time.perf_counter()
    if _TOKENIZER is None:
        _TOKENIZER = load_tokenizer()
    ctx, rng = features.Ctx(inst), random.Random(seed)
    eng = make_feasibility_engine(inst, get_action_space(), config=FeasibilityConfig(extra={'global_screen': 0}))
    codec = codec_for(inst, _TOKENIZER)
    dec = ForbiddingDecoder(next(iter(lookahead_decoder_factory(securing=True)(inst, eng, codec, MaskConfig(), 1))))
    rew, rewound, ek, ed = 0, 0, 0, -1
    status = 'dead_end'
    while True:
        allowed = dec.allowed_token_ids()
        if dec.status == 'complete':
            status = 'complete'
            break
        engine = dec.inner.engine
        if allowed and engine.is_complete():
            for token in codec.end_plan_tokens:
                dec.advance(token)
            continue
        choices = ([a for a in engine.legal_actions() if a not in dec.forbidden.get(dec.state_key(), set())]
                   if allowed else [])
        if choices:
            state = ctx.state(dec.actions)
            z = np.array([ctx.row(a, state) for a in choices]) @ W
            if greedy:
                action = choices[int(np.argmax(z))]
            else:
                z -= z.max()
                action = rng.choices(choices, weights=np.exp(z).tolist())[0]
            for token in codec.encode_action(action):
                dec.advance(token)
            continue
        if rew >= 32 or rewound >= 64:
            break
        depth = len(dec.actions)
        ek = 1 if depth > ed else min(ek*2, 8)
        ed = max(ed, depth)
        popped = dec.rewind(ek)
        rew += 1
        rewound += popped
        if popped == 0 and not dec.actions and not dec.allowed_token_ids():
            break
    return (dec.inner.engine.to_plan() if status == 'complete' else None,
            dict(status=status, rewinds=rew, rewound=rewound, seed=seed,
                 greedy=greedy, temperature=0 if greedy else 1,
                 decode_seconds=round(time.perf_counter()-started, 6), weights_sha256=WEIGHTS_SHA256))
