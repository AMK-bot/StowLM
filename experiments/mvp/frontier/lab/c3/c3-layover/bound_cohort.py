"""Measure arrival bounds only on this experiment's frozen TRAIN manifest."""
from __future__ import annotations

import argparse
from dataclasses import asdict
from hashlib import sha256
import importlib.util
import json
from pathlib import Path
import sys

from stowage.io import load_instance

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[5]
MANIFEST = HERE / 'cohort' / 'MANIFEST.json'

spec = importlib.util.spec_from_file_location('layover_measured_bound', HERE / 'bound.py')
bound = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = bound
spec.loader.exec_module(bound)


def digest(path):
    return sha256(path.read_bytes()).hexdigest()


def write_json(path, value):
    temporary = path.with_suffix(path.suffix + '.tmp')
    temporary.write_text(json.dumps(value, indent=2, sort_keys=True) + '\n')
    temporary.replace(path)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out', type=Path, default=HERE / 'bound-results')
    parser.add_argument('--small-budget-s', type=float, default=3)
    parser.add_argument('--medium-budget-s', type=float, default=5)
    parser.add_argument('--require-maps', type=int, default=6)
    args = parser.parse_args()
    out = args.out.resolve()
    if not out.is_relative_to(HERE):
        parser.error('output must stay inside c3-layover')
    manifest_bytes = MANIFEST.read_bytes()
    manifest = json.loads(manifest_bytes)
    if manifest.get('split') != 'TRAIN' or len(manifest['maps']) != args.require_maps:
        parser.error('TRAIN manifest must contain the complete predeclared cohort')
    out.mkdir(parents=True, exist_ok=True)
    rows = []
    for entry in manifest['maps']:
        path = (ROOT / entry['path']).resolve()
        if not path.is_relative_to(HERE / 'cohort') or path.suffix != '.json' or path == MANIFEST:
            raise ValueError('manifest input escapes this experiment cohort')
        input_sha = digest(path)
        if input_sha != entry['sha256']:
            raise ValueError(f'manifest digest mismatch: {path.name}')
        instance = load_instance(path)
        budget = args.medium_budget_s if entry['size'] == 'medium' else args.small_budget_s
        result = bound.lower_bound(instance, budget_s=budget, seed=0)
        row = dict(instance_id=instance.id, input=entry['path'], input_sha256=input_sha,
                   size=entry['size'], occupancy_target=entry['occupancy_target'],
                   occupancy_actual=entry['occupancy_actual'], budget_s=budget,
                   bound_source_sha256=digest(HERE / 'bound.py'), **asdict(result))
        write_json(out / f'{instance.id}.json', row)
        rows.append(row)
        print(json.dumps({key: row[key] for key in ('instance_id', 'lower_bound',
                        'counting_lower_bound', 'status', 'wall_time_s')}), flush=True)
    if MANIFEST.read_bytes() != manifest_bytes:
        raise RuntimeError('manifest changed during run; measurements not finalized')
    summary = dict(split='TRAIN', manifest_sha256=sha256(manifest_bytes).hexdigest(),
                   bound_source_sha256=digest(HERE / 'bound.py'),
                   runner_source_sha256=digest(Path(__file__)),
                   total_wall_s=sum(row['wall_time_s'] for row in rows), maps=rows,
                   note='Certified lower bounds; relaxation witnesses are not checker-passed plans.')
    write_json(out / 'summary.json', summary)
    lines = ['# Arrival-aware TRAIN bounds', '',
             'No bound witness is counted as a plan pass. Current arrival restows cost at least one lift per moved box; future restows use D15.', '',
             '| Size | Occupancy | Certified lower | Counting lower | Status | Seconds | Witness hatch | Witness current restow | Witness future restow |',
             '| --- | ---: | ---: | ---: | --- | ---: | ---: | ---: | ---: |']
    for row in rows:
        def value(key):
            return '\u2014' if row[key] is None else f'{row[key]:g}'
        lines.append(f"| {row['size']} | {row['occupancy_target']:.0%} | {value('lower_bound')} | "
                     f"{value('counting_lower_bound')} | {row['status']} | {row['wall_time_s']:.3f} | "
                     f"{value('witness_hatch')} | {value('witness_current_restow')} | {value('witness_restow')} |")
    lines += ['', f"Total bound wall time: {summary['total_wall_s']:.3f} seconds. Seed 0; one CP-SAT worker per solve.", '']
    (out / 'TABLE.md').write_text('\n'.join(lines))


if __name__ == '__main__':
    main()
