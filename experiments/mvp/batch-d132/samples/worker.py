"""Fixed-prefix completion samples; no prefix relaxation can count as a cure."""
from __future__ import annotations

import argparse
from dataclasses import asdict
import json
import os
from pathlib import Path
import shutil
import sys
import time

from runtime import (FENCE, LIVE_ROOT, configure_runtime, verify_runtime, assert_runtime_imports,
                     install_write_guard, save, sha, read)


def caches(output):
    own = output / 'cache'
    stable, vessels = own / 'stability', own / 'vessels'
    stable.mkdir(parents=True, exist_ok=True)
    vessels.mkdir(exist_ok=True)
    for folder, target in [(LIVE_ROOT / 'experiments/mvp/fixwindow/d126-20261030/cache/stability', stable),
                           (FENCE.parent / 'checker/candidate/cache/vessels', vessels)]:
        for source in folder.iterdir():
            if not source.is_file():
                continue
            destination = target / source.name
            if not destination.exists() and not destination.is_symlink():
                destination.symlink_to(source.resolve())
    os.environ['STOWAGE_VESSEL_CACHE_DIR'] = str(vessels)
    import stowage.linear.cache as linear
    import stowage.checker.stability_tables as tables
    import stowage.checker.stability_liquid_tables as liquid
    import stowage.checker.stability_liquid as direct
    import stowage.checker.stability_sounding as sounding
    # Existing qualified numerical payloads may be read. The write audit below
    # rejects cache misses published outside this sample's fence.
    linear._DEFAULT_ROOT = LIVE_ROOT / 'experiments/mvp/fixwindow/d126-20261030/cache/linear'
    for module in (liquid, direct, sounding):
        module.CACHE_DIRECTORY = stable
    tables.CACHE_DIR = stable
    for name in ('load_table_geometry', 'cached_geometry', 'migrate_legacy'):
        function = getattr(tables, name)
        if function.__kwdefaults__ and 'cache_dir' in function.__kwdefaults__:
            function.__kwdefaults__['cache_dir'] = stable


def prefix_record(label):
    if label == 'train':
        return read(FENCE.parent / 'heat/train-escape-prefix.json')
    if label == 'showcase':
        path = LIVE_ROOT / 'experiments/mvp/fixwindow/d130/original-private-heat/instance.json'
        return {'id': 'd130-showcase', 'map': str(path), 'input_sha256': sha(path), 'actions': []}
    rows = read(FENCE.parent / 'heat/val-prefixes.json')['prefixes']
    comparison = read(FENCE.parent / 'heat/val-comparison.json')['results']
    arm = label.removeprefix('val-')
    escaped = {r['id'] for r in comparison if r['arm'] == arm and r['immediate_engine_escape']}
    selected = [r for r in rows if r['id'] in escaped]
    assert len(selected) == 1
    return selected[0]


def exact_prefix(instance, plan, prefix):
    from stowage.actions import get_action_space
    space = get_action_space()
    actions = [space.encode(action) for action in space.from_plan(instance, plan)]
    return {'preserved': actions[:len(prefix)] == prefix,
            'committed_actions': len(prefix), 'returned_actions': len(actions),
            'returned_prefix': actions[:len(prefix)]}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--runtime', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--sample', choices=['train', 'train-fresh', 'val-c', 'val-d', 'showcase'], required=True)
    parser.add_argument('--phase', choices=['construct', 'finalize', 'check', 'replay'], required=True)
    parser.add_argument('--constructor-s', type=float, default=120)
    parser.add_argument('--seed', type=int, default=132)
    parser.add_argument('--v4', action='store_true')
    parser.add_argument('--strategy', type=int, default=0)
    parser.add_argument('--replay-label', default='')
    parser.add_argument('--lookahead', action='store_true')
    args = parser.parse_args()
    args.output = args.output.resolve()
    assert args.output.is_relative_to(FENCE)
    args.output.mkdir(parents=True, exist_ok=True)
    runtime = configure_runtime(args.runtime)
    sys.path.insert(1, str(args.runtime / 'src'))
    os.environ['STOWAGE_VESSEL_SINGLEFLIGHT'] = '1'
    os.environ['STOWAGE_BALLAST_V4'] = '1'
    caches(args.output)
    prefix = (read(args.output / 'prefix.json') if args.phase != 'construct'
              and (args.output / 'prefix.json').exists() else prefix_record(args.sample))
    path = LIVE_ROOT / prefix['map']
    assert '/test/' not in str(path).lower()
    assert sha(path) == prefix['input_sha256']
    from stowage.io import load_instance, load_plan
    from stowage.rules.catalogue import rule_freeze_v1_enabled
    assert not rule_freeze_v1_enabled()
    instance = load_instance(path)
    input_before = instance.model_dump_json()
    copied = args.output / 'instance.json'
    if not copied.exists():
        shutil.copy2(path, copied)
    assert sha(copied) == prefix['input_sha256']
    save(args.output / 'prefix.json', prefix)
    install_write_guard(protected_roots=[args.runtime])
    begin = time.perf_counter()
    record = {'sample': args.sample, 'phase': args.phase,
              'instance_id': instance.id, 'input_sha256': prefix['input_sha256'],
              'runtime_epoch': runtime['source_epoch_sha256'],
              'passed': False, 'full_plan_cure': False}
    if args.phase == 'construct':
        from stowage.feasibility import FeasibilityConfig
        from stowage.solver.scalable import ScalableConfig, complete
        cfg = ScalableConfig(default_time_limit_s=args.constructor_s, max_neighborhoods=0,
            feasibility_config=FeasibilityConfig(extra={'global_screen': 0}),
            securing_screen=True, heavy_first_ordering=True,
            backtrack_limit=0, max_restarts=0, constructor_v4=args.v4, initial_strategy=args.strategy)
        result = complete(instance, prefix['actions'], seed=args.seed,
                          time_limit_s=args.constructor_s, config=cfg)
        save(args.output / 'raw-result.json', result.model_dump(mode='json'))
        record.update(status=result.status, message=result.message, metrics=result.metrics,
                      constructor_v4=args.v4)
        if result.plan is not None:
            save(args.output / 'raw-plan.json', result.plan.model_dump(mode='json'))
            record['prefix'] = exact_prefix(instance, result.plan, prefix['actions'])
            record['passed'] = (result.status == 'ok' and record['prefix']['preserved']
                                and result.metrics.get('discarded_actions') == 0
                                and result.metrics.get('prefix_relaxations') == 0)
    elif args.phase == 'finalize':
        from stowage.model import PlannerResult
        from stowage.planner import finalize
        prior = read(args.output / 'construct.json')
        assert prior['passed'], 'Cannot finish an incomplete or prefix-relaxed construction'
        result = PlannerResult.model_validate(read(args.output / 'raw-result.json'))
        result = finalize(instance, result, time_limit_s=120)
        save(args.output / 'final-result.json', result.model_dump(mode='json'))
        record.update(status=result.status, message=result.message, metrics=result.metrics)
        if result.plan is not None:
            save(args.output / 'plan.json', result.plan.model_dump(mode='json'))
            record['prefix'] = exact_prefix(instance, result.plan, prefix['actions'])
            record['passed'] = result.status == 'ok' and record['prefix']['preserved'] and result.plan.ballast is not None
    elif args.phase == 'check':
        from stowage.checker import check, report_matches_inputs
        if args.sample == 'showcase':
            source = LIVE_ROOT / 'experiments/mvp/fixwindow/d130/original-private-heat/plan.json'
            shutil.copy2(source, args.output / 'plan.json')
        plan = load_plan(args.output / 'plan.json')
        report = check(instance, plan)
        save(args.output / 'report.json', report.model_dump(mode='json'))
        record.update(passed=report.passed and report_matches_inputs(report, instance, plan),
                      plan_sha256=sha(args.output / 'plan.json'), report_sha256=sha(args.output / 'report.json'),
                      hard_rules=sorted({v.rule_id for v in report.violations if v.severity == 'hard'}),
                      prefix=exact_prefix(instance, plan, prefix['actions']))
        record['full_plan_cure'] = record['passed'] and record['prefix']['preserved'] and args.sample in ('train', 'val-c', 'val-d')
    else:
        from stowage.actions import get_action_space
        from stowage.feasibility import FeasibilityConfig, make_feasibility_engine
        from llm.stowlm.format import ActionCodec, load_tokenizer
        from llm.stowlm.mask import MaskConfig
        from llm.securing import securing_decoder_factory, securing_decode_metrics
        plan = load_plan(args.output / 'plan.json')
        assert read(args.output / 'check.json')['passed']
        engine = make_feasibility_engine(instance, get_action_space(), config=FeasibilityConfig(extra={'global_screen': 0}))
        codec = ActionCodec(load_tokenizer(LIVE_ROOT / 'models/glimmer-30b-4bit/tokenizer.json'),
                            [box.id for box in instance.containers])
        if args.lookahead:
            from llm.lookahead.mask_filter import LookaheadEngine
            from llm.securing.mask_filter import SecuringEngine
            from llm.stowlm.mask import MaskedDecoder
            # Exact order used by the historical VAL c/d ablation.
            decoder = MaskedDecoder(LookaheadEngine(SecuringEngine(engine)), codec, MaskConfig())
        else:
            decoder = securing_decoder_factory()(instance, engine, codec, MaskConfig(), 1)[0]
        actions = get_action_space().from_plan(instance, plan)
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
        record.update(counters=counters, decoder_status=decoder.status, actions=len(actions),
            actions_replayed=replayed, error=error,
            lookahead=args.lookahead,
            blocked_action=(get_action_space().encode(actions[replayed]) if replayed < len(actions) else None),
            passed=decoder.status == 'complete' and counters.get('securing_active') is True
                and counters.get('securing_calls', 0) > 0 and counters.get('unavailable') == 0
                and counters.get('fail_open') == 0)
        record['full_plan_cure'] = record['passed'] and args.sample in ('train', 'val-c', 'val-d')
    assert instance.model_dump_json() == input_before
    assert sha(path) == prefix['input_sha256']
    assert_runtime_imports(args.runtime)
    assert verify_runtime(args.runtime)['source_epoch_sha256'] == runtime['source_epoch_sha256']
    record['wall_s'] = time.perf_counter() - begin
    suffix = '-' + args.replay_label if args.phase == 'replay' and args.replay_label else ''
    save(args.output / (args.phase + suffix + '.json'), record)
    print(json.dumps(record), flush=True)


if __name__ == '__main__':
    main()
