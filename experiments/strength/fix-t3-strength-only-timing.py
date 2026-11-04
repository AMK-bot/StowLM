"""Paired strength-part timing; does not invoke the full checker/cache_warm.

Compare HEAD's pre-fix strength implementation with current strength on each
identical synthetic loaded C1 state. Remove only the archived function's frozen
registration decorator in memory to avoid registering a duplicate interface.
"""
from __future__ import annotations

import ast
import hashlib
import json
from pathlib import Path
from stowage.conventions import canonical_vessel_id
import resource
import statistics
import subprocess
import sys
import time
import types

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / 'experiments/strength/fix-t3-strength-only-retiming'
REVIEW = ROOT / 'experiments/physics-review/convergence/post_d30'
sys.path[:0] = [str(REVIEW), str(ROOT / 'experiments/ballast/effectiveness')]


def digest(value):
    return hashlib.sha256(value).hexdigest()


def sources():
    paths = [*sorted((ROOT / 'src/stowage/checker').glob('strength*.py')),
             *sorted((ROOT / 'src/stowage/vessel').glob('*.py')),
             *sorted((ROOT / 'params/fleet').glob('*.toml')),
             Path(__file__).resolve(), REVIEW / 'harness2.py', REVIEW.parent / 'harness.py']
    return {str(p.relative_to(ROOT)): digest(p.read_bytes()) for p in paths}


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    start = time.perf_counter()
    metadata = dict(status='running', estimate=dict(cpu_threads=1, peak_ram_gb=1,
        gpu=False, wall_s=120, basis='prior strength-only medians 28–127 ms; '
        '3 fleets, 2 warmups + 10 paired calls each, plus current fixture assembly'),
        source_hashes_before=sources(), rows=[])
    artifact = OUT / 'timing.json'
    artifact.write_text(json.dumps(metadata, indent=2)+'\n')

    import harness2
    from regression import witness_masses
    from stowage.checker import strength as after
    from stowage.vessel.fleet import load_ship_params, load_vessel

    before_bytes = subprocess.check_output(['git', 'show', 'HEAD:src/stowage/checker/strength.py'], cwd=ROOT)
    expected = json.loads((ROOT / 'experiments/strength/fix-t3-before-timing.json').read_text())[
        'source_hashes_before']['src/stowage/checker/strength.py']
    assert digest(before_bytes) == expected, 'HEAD no longer matches recorded pre-fix strength'
    module = types.ModuleType('_fix_t3_strength_before')
    module.__file__ = str(OUT / 'archived-before-strength.py')
    sys.modules[module.__name__] = module
    tree = ast.parse(before_bytes)
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name == 'check_strength':
            assert len(node.decorator_list) == 1
            node.decorator_list = []
    exec(compile(tree, module.__file__, 'exec'), module.__dict__)
    metadata.update(before_source_sha256=digest(before_bytes),
        before_git_blob=subprocess.check_output(['git', 'rev-parse', 'HEAD:src/stowage/checker/strength.py'], cwd=ROOT).decode().strip(),
        after_source_sha256=digest(Path(after.__file__).read_bytes()),
        measurement='strength part only; no full/stability checker invocation',
        repeats=10, initial_warmups_per_version=1, alternating_pair_order=True)

    for name in ('feeder_500', 'feeder_800', 'feeder_1700'):
        historical = next(record for path in REVIEW.glob('search_*_C1.json')
                          if canonical_vessel_id((record := json.loads(path.read_text()))['ship']) == name)
        candidate = historical.get('witness') or historical['candidates'][0]
        vessel = load_vessel(name)
        masses = witness_masses(vessel, candidate['label'])
        instance, info = harness2.build(name, vessel, load_ship_params(name), 'C1', sum(masses.values()))
        for box in instance.containers:
            box.max_gross_mass_kg = 30480.
        ship = instance.onboard.model_copy(deep=True)
        ship.tank_masses_kg.update(masses)
        original = (instance.model_dump_json(), ship.model_dump_json())
        (OUT / f'{name}-instance.json').write_text(original[0]+'\n')
        (OUT / f'{name}-ship.json').write_text(original[1]+'\n')
        row = dict(ship=name, condition='C1', info=info, historical_label=candidate['label'],
            instance_sha256=digest(original[0].encode()), ship_state_sha256=digest(original[1].encode()))
        functions = dict(before=module.check_strength, after=after.check_strength)
        samples = {label: [] for label in functions}
        reports = {}
        for label, call in functions.items():
            reports[label] = call(instance, ship, call_index=instance.call_index)
            assert reports[label].ran and not reports[label].violations, reports[label]
        for repeat in range(10):
            order = ('before', 'after') if repeat % 2 == 0 else ('after', 'before')
            for label in order:
                tick = time.perf_counter()
                report = functions[label](instance, ship, call_index=instance.call_index)
                samples[label].append(time.perf_counter()-tick)
                assert report == reports[label], 'Non-deterministic strength report'
        assert original == (instance.model_dump_json(), ship.model_dump_json()), 'Input mutation'
        for label in functions:
            row[label] = dict(median_s=statistics.median(samples[label]),
                min_s=min(samples[label]), max_s=max(samples[label]), samples_s=samples[label],
                report=reports[label].model_dump(mode='json'))
        metadata['rows'].append(row)
        artifact.write_text(json.dumps(metadata, indent=2)+'\n')
        print(json.dumps(dict(ship=name, before_ms=1000*row['before']['median_s'],
            after_ms=1000*row['after']['median_s'])), flush=True)
    metadata.update(status='completed', elapsed_s=time.perf_counter()-start,
        peak_rss_gb=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss/1e9,
        source_hashes_after=sources())
    metadata['source_files_changed_during_run'] = [p for p, h in metadata['source_hashes_before'].items()
        if metadata['source_hashes_after'].get(p) != h]
    artifact.write_text(json.dumps(metadata, indent=2)+'\n')


if __name__ == '__main__':
    main()
