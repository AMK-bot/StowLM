"""Bounded current tiny pipeline evidence, never production C2-off gold.

Select at most three pinned v2 train requests before generation. Each current
producer supplies its own D50 voyage declaration. All solver, ballast, checker,
and production-mask gates belong to driver.py; this runner does not bypass them.
No model, GPU, network or cloud service is used.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone
from hashlib import sha256
import importlib.util
import json
import os
from pathlib import Path
import sys
import time

for _name in ('OPENBLAS_NUM_THREADS', 'OMP_NUM_THREADS', 'VECLIB_MAXIMUM_THREADS',
              'MKL_NUM_THREADS', 'NUMEXPR_NUM_THREADS'):
    os.environ[_name] = '1'


def _load_driver():
    name = 'tiny_gold_smoke_driver'
    spec = importlib.util.spec_from_file_location(name, Path(__file__).with_name('driver.py'))
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def select_entries(inventory, count, start_index=0):
    if type(count) is not int or not 1 <= count <= 3:
        raise ValueError('smoke count must be between 1 and 3')
    if type(start_index) is not int or not 0 <= start_index <= 2 or start_index + count > 3:
        raise ValueError('start index and count must select within the original 3 smoke requests')
    if inventory.version != 'v2':
        raise ValueError('current smoke requires frozen split v2')
    eligible = [entry for entry in inventory.manifest['entries']
                if entry['split'] == 'train' and entry['spec']['rung'] == 'tiny']
    # Cover two independent producers and a second hull before repeating a family.
    preferred = [('random_yard', 'feeder_500'), ('simulated', 'feeder_500'),
                 ('random_yard', 'feeder_1000')]
    selected = []
    for generator, vessel in preferred:
        candidates = [entry for entry in eligible if entry['spec']['generator'] == generator
                      and entry['spec']['vessel'] == vessel]
        candidates.sort(key=lambda entry: (entry['spec']['port'] != 'laem_chabang_like',
                                           entry['spec']['seed']))
        if candidates:
            selected.append(candidates[0])
    if len(selected) < start_index + count:
        raise ValueError('selected release lacks requested tiny smoke families')
    for entry in selected[start_index:start_index + count]:
        inventory.require_membership(entry['spec'], split='train')
    return selected[start_index:start_index + count]


def generation_request(spec, *, max_attempts):
    """Translate the frozen complete request to the current v3 producer API."""
    from stowage.generate.instances_v3 import CAMPAIGN_VERSION
    return dict(campaign_version=CAMPAIGN_VERSION,
        generator='simulated_yard' if spec['generator'] == 'simulated' else spec['generator'],
        profile='tiny', size=spec['rung'], seed=spec['seed'], vessel=spec['vessel'],
        port=spec['port'], noise=spec['noise'] if spec['generator'] == 'simulated' else spec['blocking_ratio'],
        n_boxes=spec['n_boxes'], max_attempts=max_attempts, dense_20ft=spec['dense_20ft'],
        tiny_dg_enabled=spec['tiny_dg_enabled'], cargo_profile=spec['cargo_profile'],
        full_export=False, c2_screened=False, identity_version=spec['identity_version'],
        equipment_profile=spec['equipment_profile'], without_dg=spec['without_dg'])


def _generate(request):
    from stowage.generate.instances_v3 import generate, CampaignRejection
    try:
        instance, audit = generate(request)
        return dict(status='generated', instance=instance, audit=audit)
    except CampaignRejection as error:
        return dict(status='rejected', error=str(error), audit=error.audit)
    except Exception as error:
        return dict(status='error', error=f'{type(error).__name__}: {error}')


def _bounded_generate(request, limit):
    from stowage.solver.exact_deadline import run_with_deadline
    started = time.monotonic()
    outcome = run_with_deadline(_generate, (request,), deadline=started + limit)
    return {**(outcome.value or {'status': 'timeout', 'error': 'generation deadline'}),
            'wall_time_s': time.monotonic() - started, 'worker_pid': outcome.worker_pid,
            'limit_s': limit, 'timed_out': outcome.timed_out}


def _gate_summary(result):
    return {key: result.get(key) for key in ('accepted', 'status', 'message', 'error',
        'checker_passed', 'mask_verified', 'shippable', 'mask_replay', 'phases')}


def run_smoke(output, *, count=1, phase_s=10., generation_s=30., max_attempts=2,
              split_manifest=None, start_index=0):
    from eval.splits import load_inventory, canonical_spec, spec_from_instance
    from stowage.io import save_instance
    inventory = load_inventory(split_manifest)
    entries = select_entries(inventory, count, start_index)
    if not 0 < phase_s <= 30 or not 0 < generation_s <= 180:
        raise ValueError('smoke phase limits must be in (0, 30] seconds; generation in (0, 180]')
    if type(max_attempts) is not int or not 1 <= max_attempts <= 20:
        raise ValueError('smoke generation retry cap must be in [1, 20]')
    output = Path(output).resolve()
    if (output / 'results.json').exists() or any((output / 'campaign').glob('plans/*.json')):
        raise ValueError('use a fresh output directory for each smoke epoch')
    driver = _load_driver()
    started = time.monotonic()
    summary = dict(format='tiny-gold-smoke@1.0', production_evidence=False, c2='off/diagnostic',
        accepted=False, processed=0, gold_published=0, diagnostic_records=0, start_index=start_index, generation_max_attempts=max_attempts,
        split_version=inventory.version, split_entries_sha256=inventory.entries_sha256,
        outcomes=[], compute=dict(cores=1, ram_gb_estimate=2, gpu=False,
            generation_limit_s=generation_s, per_phase_limit_s=phase_s,
            admitted='single CPU worker; parent fleet load/memory admission required'))
    driver.atomic_json(output / 'compute-admission.json', summary['compute'])
    driver.atomic_json(output / 'results.json', summary)
    for entry in entries:
        request = generation_request(entry['spec'], max_attempts=max_attempts)
        generation = _bounded_generate(request, generation_s)
        instance = generation.pop('instance', None)
        row = dict(spec=entry['spec'], accepted=False, generation=generation)
        if instance is not None:
            try:
                driver._require_clean(instance)
                inventory.require_membership(spec_from_instance(instance), split='train')
                if canonical_spec(spec_from_instance(instance)) != entry['spec']:
                    raise ValueError('generated request identity differs from selected inventory member')
                if instance.voyage is None:
                    raise ValueError('current producer omitted required D50 voyage declaration')
                group = request['generator'] + '/tiny'
                inputs = output / 'inputs'
                path = inputs / group / f"seed-{request['seed']}.json"
                save_instance(instance, path)
                shard = dict(instance=path.name, sha256=sha256(path.read_bytes()).hexdigest(),
                    split='train', split_spec=entry['spec'], spec_key=generation['audit']['spec_key'])
                driver.atomic_json(inputs / 'manifest.json', dict(groups={group: {'shards': [shard]}}))
                record = driver.inspect_manifest(inputs, split_manifest=split_manifest)[0]
                # The driver's existing budget reserve remains authoritative.
                stop = datetime.now(timezone.utc) + timedelta(seconds=600)
                config = driver.Config(output=output / 'campaign', c2_off=True,
                    split_manifest=split_manifest, search_s=phase_s, scalable_s=phase_s,
                    finalize_s=phase_s, checker_s=phase_s, stop_at=stop.isoformat(),
                    hard_stop_at=(stop + timedelta(seconds=30)).isoformat())
                attempt = driver.run_one(record, config)
                row.update(instance_id=instance.id, input_sha256=shard['sha256'],
                    voyage_declared=True, outcome=attempt,
                    exact=_gate_summary(attempt.get('exact', {})),
                    scalable=_gate_summary(attempt.get('scalable', {})))
                if attempt.get('accepted') or attempt.get('gold_file'):
                    raise RuntimeError('C2-off diagnostic unexpectedly published production gold')
                if attempt.get('diagnostic_file'):
                    summary['diagnostic_records'] += 1
            except Exception as error:
                row['pipeline_error'] = f'{type(error).__name__}: {error}'
        summary['outcomes'].append(row)
        summary['processed'] += 1
        summary['gold_published'] = len(list((output / 'campaign' / 'plans').glob('*.json')))
        summary['wall_time_s'] = time.monotonic() - started
        driver.atomic_json(output / 'results.json', summary)
    return summary


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--count', type=int, default=1, choices=(1, 2, 3))
    parser.add_argument('--start-index', type=int, default=0, choices=(0, 1, 2),
                        help='retry an original selected request without selecting a fourth identity')
    parser.add_argument('--phase-s', type=float, default=10.)
    parser.add_argument('--generation-s', type=float, default=30.)
    parser.add_argument('--max-attempts', type=int, default=2)
    parser.add_argument('--split-manifest', type=Path)
    args = parser.parse_args()
    result = run_smoke(args.output, count=args.count, phase_s=args.phase_s,
        generation_s=args.generation_s, max_attempts=args.max_attempts,
        split_manifest=args.split_manifest, start_index=args.start_index)
    print(json.dumps(result, sort_keys=True, indent=2, allow_nan=False))


if __name__ == '__main__':
    main()
