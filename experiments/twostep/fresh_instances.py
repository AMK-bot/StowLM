"""Small fresh fallback cohort when the canonical v3 release is unavailable.

Specifications are assigned by the frozen 5.3 policy before any generation.
Only train/val inputs are generated; test specifications are skipped without
loading their instances. Output uses the canonical manifest shape so benchmark.py
remains path-parameterised. This cohort is labelled scoped, never canonical v3.
"""
from __future__ import annotations
import argparse
from dataclasses import asdict
import hashlib
import json
import os
import signal
from pathlib import Path
import time

for key in ('OPENBLAS_NUM_THREADS', 'OMP_NUM_THREADS', 'VECLIB_MAXIMUM_THREADS', 'MKL_NUM_THREADS'):
    os.environ[key] = '1'


def main():
    from benchmark import source_epoch
    from eval.splits import InstanceSpec, SPLIT_POLICY, split_of
    from stowage.generate.random_yard import generate_random_instance, RandomGenerationReport
    from stowage.io import save_instance
    from stowage.versions import component_versions
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--rungs', nargs='+', default=['tiny', 'small', 'medium'])
    parser.add_argument('--count', type=int, default=1)
    parser.add_argument('--seed', type=int, default=410000)
    parser.add_argument('--output', type=Path, default=Path('experiments/twostep/fresh-v3'))
    parser.add_argument('--generation-seconds', type=int, default=60,
                        help='independent wall cap per generated instance')
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    manifest = {'campaign_version': 'twostep-scoped-final-ships-2', 'canonical': False,
                'selection_scope': 'exploratory generated specs; frozen split assignment, not released inventory membership',
                'versions': component_versions(), 'groups': {}}
    for rung_index, rung in enumerate(args.rungs):
        group = f'random_yard/{rung}'
        shards = []
        manifest['groups'][group] = {'shards': shards}
        directory = args.output / group
        directory.mkdir(parents=True, exist_ok=True)
        seed = args.seed+rung_index*10000
        while len(shards) < args.count:
            spec = InstanceSpec('random_yard', rung, 'feeder_500', rung,
                                'laem_chabang_like', None, 0.3, seed)
            seed += 1
            split = split_of(spec)
            if split == 'test':
                continue
            started = time.perf_counter()
            epoch = source_epoch()
            def expired(*_):
                raise TimeoutError('bounded generation exceeded its wall cap')
            signal.signal(signal.SIGALRM, expired)
            signal.alarm(args.generation_seconds)
            audit = RandomGenerationReport()
            instance = generate_random_instance(rung, seed=spec.seed, blocking_ratio=spec.blocking_ratio,
                                               vessel_name=spec.vessel, port_name=spec.port, report=audit)
            signal.alarm(0)
            name = f'seed-{spec.seed:08d}.json'
            path = directory / name
            save_instance(instance, path)
            if source_epoch() != epoch:
                path.unlink()
                raise RuntimeError("Source epoch changed during generation; retry under stable sources")
            row = dict(instance=name, seed=spec.seed, split=split, split_policy=SPLIT_POLICY,
                       split_spec=asdict(spec), sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
                       generation_seconds=time.perf_counter()-started, audit=asdict(audit))
            row["source_epoch"] = epoch
            shards.append(row)
            (args.output/'manifest.json').write_text(json.dumps(manifest, indent=2, sort_keys=True)+'\n')
            print(json.dumps(dict(rung=rung, **row)), flush=True)


if __name__ == '__main__':
    main()
