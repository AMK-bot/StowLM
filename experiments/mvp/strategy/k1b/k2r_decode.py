"""rev-k1c P2: K1r on the ladder under the model's deadline boundary. The 3,600 s clock starts when the map starts, setup included, is checked inside
sampling at every action, and an interrupted sample isn't stored, as in run_c1. Elapsed time is saved per map, so a resume can't renew the allowance."""
from __future__ import annotations

import json
from pathlib import Path
import random
import time

import k1r


class Expired(Exception):
    pass


class DeadlineRandom(random.Random):
    """k1r.sample draws every action through choice(), which makes it the per-action deadline check."""

    def __init__(self, seed, deadline):
        super().__init__(seed)
        self.deadline = deadline

    def choice(self, seq):
        if time.monotonic() >= self.deadline:
            raise Expired
        return super().choice(seq)


def load_clock(path):
    return json.loads(Path(path).read_text())['elapsed_s'] if Path(path).exists() else 0.


def save_clock(path, elapsed):
    Path(path).write_text(json.dumps(dict(elapsed_s=elapsed)))


def decode_map(job, out, limit_s, samples=k1r.SAMPLES):
    from llm.lookahead import lookahead_decoder_factory
    from llm.stowlm.backtrack import ForbiddingDecoder
    from llm.stowlm.format import load_tokenizer
    from llm.stowlm.mask import MaskConfig
    from llm.stowlm.sft_data import codec_for
    from stowage.actions import get_action_space
    from stowage.feasibility import FeasibilityConfig, make_feasibility_engine
    from stowage.io import load_instance
    folder, clock = Path(out)/'candidates'/job['instance_id'], Path(out)/'clocks'/f"{job['instance_id']}.json"
    folder.mkdir(parents=True, exist_ok=True)
    clock.parent.mkdir(parents=True, exist_ok=True)
    spent, started = load_clock(clock), time.monotonic()
    deadline = started+limit_s-spent
    todo = [i for i in range(samples) if not (folder/f'sample-{i:02d}.json').exists()]
    if not todo or time.monotonic() >= deadline:
        return dict(instance_id=job['instance_id'], stopped='done' if not todo else 'deadline')
    instance = load_instance(k1r.ROOT/job['input'])
    engine = make_feasibility_engine(instance, get_action_space(), config=FeasibilityConfig(extra={'global_screen': 0}))
    codec = codec_for(instance, load_tokenizer())
    decoders = list(lookahead_decoder_factory(securing=True)(instance, engine, codec, MaskConfig(), samples))
    stopped = 'done'
    for i in todo:
        decoder = ForbiddingDecoder(decoders[i])
        began = time.perf_counter()
        try:
            status, rewinds = k1r.sample(decoder, codec, DeadlineRandom(i, deadline))
        except Expired:
            stopped = 'deadline'
            break
        plan = decoder.inner.engine.to_plan().model_dump(mode='json') if status == 'complete' else None
        path = folder/f'sample-{i:02d}.json'
        temporary = path.with_suffix('.tmp')
        temporary.write_text(json.dumps(dict(instance_id=job['instance_id'], sample=i, status=status, temperature='uniform', seed=i, backtracks=rewinds,
                                             wall_s=round(time.perf_counter()-began, 2), plan=plan)))
        temporary.replace(path)
        save_clock(clock, spent+time.monotonic()-started)
    save_clock(clock, spent+time.monotonic()-started)
    print(json.dumps(dict(instance_id=job['instance_id'], stopped=stopped)), flush=True)
    return dict(instance_id=job['instance_id'], stopped=stopped)
