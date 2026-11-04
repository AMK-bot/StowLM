"""Exercise development packets and report plumbing with explicit no-plan stubs.

This is a machinery fixture, never an engine, baseline or reader measurement.
The actual runner assembles packets, checks the envelope and authenticates the
fixture reader output. Planning returns no plan, so no checker pass is invented.
"""
from __future__ import annotations

import argparse
from contextlib import ExitStack
from pathlib import Path
import sys
from unittest.mock import patch

from . import driver, worker
from .common import HERE, ROOT, atomic_json
from .budget import DEFAULT_WORKERS


STUBS = ['engine returns no plan', 'two-step returns no plan',
         'deterministic fixture reader', 'in-process reader deadline',
         'unused numerical cache setup', 'fresh audit unavailable for surrogate',
         'development source inventory covers imported runtime and harness; no sealed freeze claim']


def _development_sources():
    """Pin this surrogate's imports and data, excluding unrelated live probes."""
    from experiments.mvp.reader import public_brief  # actual packet reader API
    from experiments.mvp.pipeline.runner.profiles import get_profile
    get_profile('standard')  # import Config without invoking any planner
    loaded = {Path(module.__file__).resolve() for module in tuple(sys.modules.values())
              if getattr(module, '__file__', None)}
    return [path for path in driver.source_inventory()
            if path in loaded or path.parent == HERE
            or path.is_relative_to(ROOT / 'src') or path.is_relative_to(ROOT / 'params')
            or path.name in {Path(name).name for name in driver.ENGINE_RESOURCES}]


def _no_engine(*args, **kwargs):
    return None, dict(status='no_checked_plan', passed=False,
                      engine_version='fixture-surrogate',
                      reason='Explicit dry-run no-plan engine stub; no search ran.')


def _no_twostep(*args, **kwargs):
    return dict(status='no_checked_plan', plan=None,
                evidence=dict(fixture_surrogate=True, label=worker.TWOSTEP_LABEL,
                              reason='Explicit dry-run no-plan two-step stub.'))


def _direct(function, *args, timeout_s, stage='reader', **kwargs):
    return function(*args, **kwargs)


def run(out_dir, *, workers=DEFAULT_WORKERS, fixture_admission=False) -> dict:
    """Run every current development-list map; sealed inputs remain refused."""
    out_dir = Path(out_dir).resolve()
    from experiments.mvp.pipeline.runner import core, limits
    # Other executives may add unreferenced modules during a development replay.
    # Existing source bytes stay pinned; sealed execution uses the live inventory
    # and refuses additions as well as edits. This fixture cannot be released.
    initial_sources = _development_sources()
    stubs = STUBS + (['fixture admission telemetry; no planning budget is executed']
                    if fixture_admission else [])
    config = dict(mode='dry-run', workers=workers, fixture_surrogate=True,
                  stubbed=stubs, profile='standard', twostep_budget_s=30)
    with ExitStack() as patches:
        patches.enter_context(patch.object(driver, 'source_inventory', lambda: initial_sources))
        patches.enter_context(patch.object(core, 'engine_call', _no_engine))
        patches.enter_context(patch.object(core, 'configure_caches', lambda: None))
        patches.enter_context(patch.object(limits, 'run_in_process_with_limit', _direct))
        patches.enter_context(patch.object(worker, '_twostep', _no_twostep))
        driver.freeze(config, out_dir)
        admission = (lambda _: dict(fixture_surrogate=True,
            reason='No planner or timed numerical work is executed.')) if fixture_admission else None
        result = driver.run(out_dir, worker=worker.run_job, admission_wait=admission)
    atomic_json(out_dir / 'DRY-VERIFIED.json', dict(
        mode='development fixture surrogate', sealed_maps_opened=0,
        rows=result['rows'], expected_rows=result['expected_rows'],
        report_shape_complete=result['complete'], stubbed=stubs,
        admission='fixture telemetry' if fixture_admission else 'live fleet telemetry',
        sealed_claim_eligible=False, spend_usd=0,
        limitations='No engine, two-step performance, reader accuracy or sealed claim is measured.'))
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out-dir', default=str(HERE / 'runs' / 'development-dry'))
    parser.add_argument('--workers', type=int, default=DEFAULT_WORKERS)
    parser.add_argument('--fixture-admission', action='store_true',
                        help='Stub telemetry only for this no-plan offline report fixture.')
    args = parser.parse_args()
    summary = run(args.out_dir, workers=args.workers, fixture_admission=args.fixture_admission)
    print(f"Development fixture: {summary['rows']}/{summary['expected_rows']} rows; "
          f"complete={summary['complete']}; sealed claims={summary['sealed_claim_eligible']}")


if __name__ == '__main__':
    main()
