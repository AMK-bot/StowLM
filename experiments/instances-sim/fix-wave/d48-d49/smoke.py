"""D48 bounded real-hull generation; C1 only, no plan certification."""
from argparse import ArgumentParser
from dataclasses import asdict
from hashlib import sha256
import json
from pathlib import Path
from time import perf_counter
from datetime import datetime, timezone

from stowage.generate.instances import GenerationReport, INSTANCES_VERSION, generate_simulated_instance
from stowage.generate.cargo import CARGO_GENERATOR_VERSION
from stowage.io import save_instance, load_instance
from stowage.vessel.fleet_conditions import CONDITION_VERSION
from stowage.yard.simulator import SIMULATOR_VERSION

OUTPUT = Path(__file__).resolve().parent
VESSELS = ('feeder_500', 'feeder_800', 'feeder_1700')
SIZES = ('tiny', 'small', 'medium')


def fingerprint():
    # Pin the producer's actual source closure; campaign adapters/C2 are not
    # called by this C1-only rerun of the prior real-hull smoke.
    root = Path('src/stowage')
    paths = set(Path('params').rglob('*.toml'))
    for folder in ('vessel', 'model', 'checker', 'rules', 'ports', 'yard', 'feasibility', 'moves'):
        paths.update((root / folder).rglob('*.py'))
        paths.update((root / folder).rglob('*.toml'))
    paths.update(root / 'generate' / name for name in
                 ('instances.py', 'instances_screen.py', 'random_yard.py', 'random_export.py', 'cargo.py'))
    paths.update((root / 'generate').glob('cargo_*.py'))
    paths.update(root / name for name in ('versions.py', 'frozen.py', 'params.py', 'io.py', 'conventions.py'))
    return {str(path): sha256(path.read_bytes()).hexdigest() for path in sorted(paths)}


def write(path, value):
    path.write_text(json.dumps(value, indent=2, sort_keys=True, allow_nan=False) + '\n')


def main():
    parser = ArgumentParser(description=__doc__)
    parser.add_argument('--limit', type=int, default=9)
    parser.add_argument('--resume', action='store_true')
    args = parser.parse_args()
    if not 1 <= args.limit <= 9:
        parser.error('limit must be 1..9')
    results = []
    started = perf_counter()
    for vessel, size in [(v, s) for v in VESSELS for s in SIZES][:args.limit]:
        config = dict(size=size, vessel_name=vessel, seed=1, noise=0.2,
                      without_dg=True, max_attempts=3)
        stem = f'{vessel}-{size}-seed-1'
        audit_path = OUTPUT / f'{stem}.audit.json'
        before = fingerprint()
        if results and before != results[0]['sources']:
            raise RuntimeError('source epoch changed between shards; retain audits and restart')
        if args.resume and audit_path.exists():
            record = json.loads(audit_path.read_text())
            if record['config'] != config or record['sources'] != before or record['source_drift']:
                raise ValueError(f'resume source/config mismatch: {audit_path}')
            if record['status'] == 'accepted':
                path = OUTPUT / record['instance']
                if sha256(path.read_bytes()).hexdigest() != record['instance_sha256']:
                    raise ValueError(f'instance checksum mismatch: {path}')
                load_instance(path)
            results.append(record)
            print(f'{vessel}/{size}: resumed {record["status"]}', flush=True)
            continue
        report = GenerationReport()
        shard_start = perf_counter()
        record = dict(config=config, sources=before, started_at=datetime.now(timezone.utc).isoformat(),
                      c2_screened=False, full_checker_certified=False)
        try:
            instance = generate_simulated_instance(**config, report=report)
        except ValueError as error:
            if not str(error).startswith('screening exhausted'):
                raise
            record.update(status='rejected', error=str(error), json_roundtrip=False)
        else:
            assert instance.vessel.hull is not None
            path = save_instance(instance, OUTPUT / f'{stem}.json')
            assert load_instance(path) == instance
            record.update(status='accepted', instance=path.name, n_boxes=len(instance.load_list),
                          instance_sha256=sha256(path.read_bytes()).hexdigest(), json_roundtrip=True)
        after = fingerprint()
        drift = sorted(p for p in before.keys() | after.keys() if before.get(p) != after.get(p))
        record.update(audit=asdict(report), elapsed_s=perf_counter() - shard_start, source_drift=drift)
        write(audit_path, record)
        results.append(record)
        print(f'{vessel}/{size}: {record["status"]}; attempts={report.attempts}; '
              f'balancing={report.balancing_rejections}; reasons={report.rejections}; '
              f'{record["elapsed_s"]:.3f}s', flush=True)
        if drift:
            raise RuntimeError(f'source drift during shard: {drift}')
    write(OUTPUT / 'manifest.json', dict(generator=INSTANCES_VERSION, cargo_generator=CARGO_GENERATOR_VERSION,
          yard_simulator=SIMULATOR_VERSION, vessel_condition=CONDITION_VERSION,
          results=results, accepted=sum(r['status'] == 'accepted' for r in results),
          rejected=sum(r['status'] == 'rejected' for r in results), elapsed_this_run_s=perf_counter()-started,
          total_shard_s=sum(r['elapsed_s'] for r in results), full_checker_certified=False, c2_screened=False))


if __name__ == '__main__':
    main()
