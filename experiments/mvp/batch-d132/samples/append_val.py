"""Sample the sole new VAL mask action, without claiming D92 constructor success."""
from __future__ import annotations

import argparse
import os
from pathlib import Path
import shutil
import time

from runtime import (FENCE, LIVE_ROOT, configure_runtime, verify_runtime, assert_runtime_imports,
                     install_write_guard, save, read, sha)
from worker import caches, prefix_record, exact_prefix


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--runtime', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    args.output = args.output.resolve()
    assert args.output.is_relative_to(FENCE)
    args.output.mkdir(parents=True, exist_ok=False)
    runtime = configure_runtime(args.runtime)
    os.environ['STOWAGE_VESSEL_SINGLEFLIGHT'] = '1'
    caches(args.output)
    prefix = prefix_record('val-c')
    comparison = next(row for row in read(FENCE.parent / 'heat/val-comparison.json')['results']
                      if row['id'] == prefix['id'])
    assert len(comparison['added']) == 1
    source = LIVE_ROOT / prefix['map']
    assert '/test/' not in str(source) and sha(source) == prefix['input_sha256']
    shutil.copy2(source, args.output / 'instance.json')
    save(args.output / 'prefix.json', prefix)
    from stowage.actions import get_action_space
    from stowage.feasibility import FeasibilityConfig, make_feasibility_engine
    from stowage.io import load_instance
    from stowage.model import PlannerResult
    from llm.stowlm.format import ActionCodec, load_tokenizer
    from llm.stowlm.mask import MaskConfig
    from llm.securing import securing_decoder_factory, securing_decode_metrics
    instance = load_instance(source)
    space = get_action_space()
    install_write_guard(protected_roots=[args.runtime])
    started = time.perf_counter()
    engine = make_feasibility_engine(instance, space, config=FeasibilityConfig(extra={'global_screen': 0}))
    codec = ActionCodec(load_tokenizer(LIVE_ROOT / 'models/glimmer-30b-4bit/tokenizer.json'),
                        [box.id for box in instance.containers])
    decoder = securing_decoder_factory()(instance, engine, codec, MaskConfig(), 1)[0]
    actions = [space.decode(row) for row in prefix['actions'] + comparison['added']]
    replayed = 0
    error = None
    try:
        for action in actions:
            for token in codec.encode_action(action):
                decoder.advance(token)
            replayed += 1
        for token in codec.end_plan_tokens:
            decoder.advance(token)
    except Exception as exc:
        error = f'{type(exc).__name__}: {exc}'
    counters = securing_decode_metrics([decoder])
    record = {'sample': 'val-c', 'phase': 'construct', 'source': 'exact-mask-continuation',
        'runtime_epoch': runtime['source_epoch_sha256'], 'input_sha256': prefix['input_sha256'],
        'committed_actions': len(prefix['actions']), 'sampled_added_action': comparison['added'][0],
        'actions_replayed': replayed, 'decoder_status': decoder.status, 'error': error,
        'securing_counters': counters, 'd92_constructor_success_claimed': False,
        'd92_design_gm_rejection_ratio': 1.0985028223317657,
        'full_plan_cure': False, 'passed': False, 'wall_s': time.perf_counter() - started}
    if error is None and decoder.status == 'complete':
        plan = space.expand(instance, actions)
        result = PlannerResult(status='ok', plan=plan, planner='mask.sample.fixed-prefix',
            planner_version='D132', seed=132, wall_time_s=record['wall_s'],
            metrics={'retained_actions': len(prefix['actions']), 'discarded_actions': 0,
                     'prefix_relaxations': 0, 'sampled_mask_actions': len(actions),
                     'not_d92_constructor': True})
        save(args.output / 'raw-result.json', result.model_dump(mode='json'))
        save(args.output / 'raw-plan.json', plan.model_dump(mode='json'))
        record['prefix'] = exact_prefix(instance, plan, prefix['actions'])
        record['passed'] = (record['prefix']['preserved'] and counters.get('securing_active') is True
                            and counters.get('fail_open') == 0 and counters.get('unavailable') == 0)
        record['status'] = 'ok' if record['passed'] else 'unaccepted'
    assert sha(source) == prefix['input_sha256']
    assert_runtime_imports(args.runtime)
    assert verify_runtime(args.runtime)['source_epoch_sha256'] == runtime['source_epoch_sha256']
    save(args.output / 'construct.json', record)
    print(record, flush=True)


if __name__ == '__main__':
    main()
