"""A fresh TRAIN two-step sample, explicitly not a terminal-prefix cure."""
import argparse
import os
from pathlib import Path
import shutil
import time

from runtime import FENCE, LIVE_ROOT, configure_runtime, assert_runtime_imports, install_write_guard, save, sha
from worker import caches, prefix_record


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--runtime', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    output = args.output.resolve()
    assert output.is_relative_to(FENCE)
    output.mkdir(parents=True, exist_ok=False)
    runtime = configure_runtime(args.runtime)
    os.environ['STOWAGE_VESSEL_SINGLEFLIGHT'] = '1'
    caches(output)
    terminal = prefix_record('train')
    prefix = {**terminal, 'actions': [], 'is_terminal_prefix_cure': False,
              'original_terminal_prefix_length': len(terminal['actions']),
              'sampling_method': 'fresh two-step from root; original failed prefix not reused'}
    source = LIVE_ROOT / prefix['map']
    assert sha(source) == prefix['input_sha256'] and '/test/' not in str(source)
    shutil.copy2(source, output / 'instance.json')
    save(output / 'prefix.json', prefix)
    from stowage.io import load_instance
    from stowage.feasibility import FeasibilityConfig
    from stowage.solver.twostep import TwoStepPlanner
    from stowage.solver.gold_warmstart import _mask_replay
    from stowage.solver.securing_screen import DeckSecuringScreen
    instance = load_instance(source)
    install_write_guard(protected_roots=[args.runtime])
    started = time.perf_counter()
    result = TwoStepPlanner(feasibility_config=FeasibilityConfig(extra={'global_screen': 0})).plan(
        instance, seed=132, time_limit_s=120)
    record = {'sample': 'train-fresh', 'phase': 'construct',
              'runtime_epoch': runtime['source_epoch_sha256'],
              'input_sha256': prefix['input_sha256'],
              'status': result.status, 'message': result.message,
              'full_plan_cure': False, 'terminal_prefix_cure': False,
              'source': 'fresh two-step sample', 'passed': False,
              'wall_s': time.perf_counter() - started, 'metrics': result.metrics}
    if result.status == 'ok' and result.plan is not None:
        record['d92_postconstruction'] = _mask_replay(instance, result.plan, screen=DeckSecuringScreen(instance))
        record['passed'] = record['d92_postconstruction']['passed']
        save(output / 'raw-plan.json', result.plan.model_dump(mode='json'))
    save(output / 'raw-result.json', result.model_dump(mode='json'))
    save(output / 'construct.json', record)
    assert_runtime_imports(args.runtime)
    assert sha(source) == prefix['input_sha256']
    print(record, flush=True)


if __name__ == '__main__':
    main()
