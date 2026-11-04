"""D193 K1r, no LLM and no GPU: at each step pick uniformly among mask-legal actions, under the same mask stack, rewinds and 16 samples as
K1b, then the same compaction and judge. A sample's seed is its index, as in K1b; every sample is random (there's no greedy). Rewinds follow
generate_backtracking: k = 1, 2, 4... up to 8 within a dead-end episode, at most 32 rewinds and 64 rewound actions per plan; a rewind
forbids the action taken at the state it returns to. Resumable per sample. Detached: k1r.py [workers=6]
"""
from __future__ import annotations

from concurrent.futures import ProcessPoolExecutor
from datetime import datetime
import json
import os
from pathlib import Path
import random
import sys
import time

HERE = Path(__file__).resolve().parent
OUT = HERE/'k1r'
os.environ.update(K1B_OUT=str(OUT), COMPACT_FINISHER_S='600')  # before compact and judge are imported; spawned workers inherit it
for name in ('OMP_NUM_THREADS', 'OPENBLAS_NUM_THREADS', 'MKL_NUM_THREADS', 'VECLIB_MAXIMUM_THREADS', 'NUMEXPR_NUM_THREADS'):
    os.environ[name] = '1'
os.environ.pop('STOWAGE_BALLAST_V4', None)
sys.path.insert(0, str(HERE.parent))
import judge
import kcurve
import k1r_report

ROOT = HERE.parents[3]
K1B = json.loads((HERE/'manifest.json').read_text())
SAMPLES, MAX_BACKTRACKS, MAX_REWIND, MAX_REWOUND = 16, 32, 8, 64


def next_k(k, depth, episode_depth):
    """generate_backtracking's rule: restart at 1 once deeper than the deepest failure, else double up to the limit."""
    return 1 if depth > episode_depth else min(k*2, MAX_REWIND)


def choose(actions, banned, rng):
    """One action uniformly among the legal ones not forbidden here; None if there are none."""
    open_actions = [a for a in actions if a not in banned]
    return rng.choice(open_actions) if open_actions else None


def sample(decoder, codec, rng):
    """(status, rewinds). decoder is a ForbiddingDecoder over the K1b mask stack."""
    rewinds, rewound, episode_k, episode_depth = 0, 0, 0, -1
    while True:
        allowed = decoder.allowed_token_ids()
        if decoder.status == 'complete':
            return 'complete', rewinds
        engine = decoder.inner.engine
        if allowed and engine.is_complete():
            for token in codec.end_plan_tokens:
                decoder.advance(token)
            continue
        action = choose(engine.legal_actions(), decoder.forbidden.get(decoder.state_key(), set()), rng) if allowed else None
        if action is not None:
            for token in codec.encode_action(action):
                decoder.advance(token)
            continue
        if rewinds >= MAX_BACKTRACKS or rewound >= MAX_REWOUND:
            return 'dead_end', rewinds
        depth = len(decoder.actions)
        episode_k = next_k(episode_k, depth, episode_depth)
        episode_depth = max(episode_depth, depth)
        popped = decoder.rewind(episode_k)
        rewinds += 1
        rewound += popped
        if popped == 0 and not decoder.actions and not decoder.allowed_token_ids():
            return 'dead_end', rewinds


def decode_map(job):
    from llm.lookahead import lookahead_decoder_factory
    from llm.stowlm.backtrack import ForbiddingDecoder
    from llm.stowlm.format import load_tokenizer
    from llm.stowlm.mask import MaskConfig
    from llm.stowlm.sft_data import codec_for
    from stowage.actions import get_action_space
    from stowage.feasibility import FeasibilityConfig, make_feasibility_engine
    from stowage.io import load_instance
    iid, folder = job['instance_id'], OUT/'candidates'/job['instance_id']
    folder.mkdir(parents=True, exist_ok=True)
    todo = [i for i in range(SAMPLES) if not (folder/f'sample-{i:02d}.json').exists()]
    if not todo:
        return iid
    instance = load_instance(ROOT/job['input'])
    space = get_action_space()
    engine = make_feasibility_engine(instance, space, config=FeasibilityConfig(extra={'global_screen': 0}))
    codec = codec_for(instance, load_tokenizer())
    if job.get('port_rule'):  # D196: K1c's mask, the D185 rule as an instruction layer
        import port_mask
        factory = lookahead_decoder_factory(securing=True, engine_wrapper=port_mask.wrap_engine)
    else:
        factory = lookahead_decoder_factory(securing=True)
    decoders = list(factory(instance, engine, codec, MaskConfig(), SAMPLES))
    deadline = time.monotonic()+job['time_limit_s'] if job.get('time_limit_s') else None
    for i in todo:
        if deadline is not None and time.monotonic() >= deadline:
            break  # the ladder's per-map limit; samples already stored stay
        decoder = ForbiddingDecoder(decoders[i])
        started = time.perf_counter()
        status, rewinds = sample(decoder, codec, random.Random(i))
        plan = decoder.inner.engine.to_plan().model_dump(mode='json') if status == 'complete' else None
        path = folder/f'sample-{i:02d}.json'
        temporary = path.with_suffix('.tmp')
        temporary.write_text(json.dumps(dict(instance_id=iid, sample=i, status=status, temperature='uniform', seed=i, backtracks=rewinds,
                                             wall_s=round(time.perf_counter()-started, 2), plan=plan)))
        temporary.replace(path)
    print(json.dumps(dict(instance_id=iid, samples=len(todo))), flush=True)
    return iid


def main():
    workers = int(sys.argv[1]) if len(sys.argv) > 1 else 6
    files = sorted(K1B['inputs'])
    OUT.mkdir(exist_ok=True)
    jobs = judge.jobs(files)
    if not (OUT/'decode.DONE').exists():
        with ProcessPoolExecutor(workers) as pool:
            list(pool.map(decode_map, jobs))
        (OUT/'decode.DONE').write_text(datetime.now().astimezone().isoformat()+'\n')
    print('decoded', flush=True)
    with ProcessPoolExecutor(workers) as pool:
        list(pool.map(judge.judge_model, jobs))
    print('judged', flush=True)
    with ProcessPoolExecutor(workers) as pool:
        list(pool.map(kcurve.curve, kcurve.jobs(jobs)))
    print('curves done', flush=True)
    deadline = time.monotonic()+3*3600  # K1c's row goes in if it lands in time; the reports are rerun otherwise
    while not (HERE/'k1c/K1C.DONE').exists() and time.monotonic() < deadline:
        time.sleep(30)
    k1r_report.write_all()
    ledger = ROOT/'experiments/fleet/ledger.md'
    ledger.write_text(''.join(line for line in ledger.read_text().splitlines(keepends=True) if not line.startswith('| k1r/')))
    with open(ROOT/'experiments/fleet/history.md', 'a') as history:
        history.write(f'\n- k1r/run (fix-t8-sft, 31 Oct): D193 K1r and k-curve, CPU only; finished {datetime.now().astimezone().isoformat(timespec="minutes")}.\n')
    (OUT/'K1R.DONE').write_text(datetime.now().astimezone().isoformat()+'\n')


if __name__ == '__main__':
    main()
