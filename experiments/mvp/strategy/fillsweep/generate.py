#!/usr/bin/env python3
"""D192 TRAIN-only matched medium fill/mass ladder; no planner execution.

Parents use the unmodified MVP corpus builder. Derivatives are explicit
experimental inputs, NOT producer-certified loading plans. No source guards,
physics rules, acceptance gates, or original corpus files are modified.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import sys
import time

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[3]
sys.path.insert(0, str(ROOT / 'experiments/mvp/corpus_tools'))
import build

FILLS = (40, 50, 60, 70, 80)
MASSES = {'low': .65, 'high': 1.0}
TOLERANCE = .025


def configure_runtime(path):
    global build
    runtime = Path(path).resolve()
    assert (runtime / 'src/stowage').is_dir() and (runtime / 'params').is_dir()
    assert not any(name == 'stowage' or name.startswith('stowage.') for name in sys.modules), 'configure before stowage imports'
    sys.path.insert(0, str(runtime / 'src'))
    spec = importlib.util.spec_from_file_location('fillsweep_pinned_build', runtime / 'experiments/mvp/corpus_tools/build.py')
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    module.ROOT = runtime
    build = module


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def stamp():
    return datetime.now(timezone.utc).isoformat()


def specs():
    return [dict(split='train', rung='medium', vessel=ship, generator='random_yard',
                 seed=192_200_000 + 100 * index + rep, port='laem_chabang_like', noise=.5,
                 n_boxes=None, dense_20ft=False, tiny_dg_enabled=True, max_attempts=100,
                 booking_slack=0.0, booking_mass_fraction=None)
            for index, ship in enumerate(build.VESSELS) for rep in range(3)]


def map_row(spec, fill, mass):
    return dict(map_id=f"medium-{spec['vessel']}-f{fill:02d}-{mass}-s{spec['seed']}",
                split='train', size='medium', ship=spec['vessel'], family='random_yard',
                seed=spec['seed'], fill_target=fill, mass_level=mass,
                mass_multiplier=MASSES[mass], generation_status='pending')


def freeze():
    prereg = HERE / 'PREREG.md'
    assert prereg.exists(), 'PREREG.md must exist before freezing requests or generation'
    request_path = HERE / 'requests.json'
    if request_path.exists():
        value = json.loads(request_path.read_text())
        assert value['prereg_sha256'] == sha(prereg), 'preregistration changed after freeze'
        assert value['amendment_sha256'] == sha(HERE / 'AMENDMENT-1.md'), 'amendment changed after freeze'
        assert value['specs'] == specs()
        assert value['runtime_root'] == str(build.ROOT), 'use the registered --runtime for resume'
        assert value['builder_sha256'] == sha(build.__file__)
        return value
    # Split metadata only; never open held-out map or plan contents.
    used = set()
    manifest = ROOT / 'experiments/mvp/corpus/manifest.json'
    for row in json.loads(manifest.read_text())['files']:
        used.add(row['seed'])
    for split in ('train', 'val', 'test'):
        requested = ROOT / f'experiments/mvp/corpus/{split}.requested.json'
        if requested.exists():
            used.update(row['seed'] for row in json.loads(requested.read_text())['specs'])
    cohort = ROOT / 'experiments/mvp/strategy/blockrule/cohort-medium.json'
    used.update(row['seed'] for row in json.loads(cohort.read_text())['maps'])
    assert not used.intersection(row['seed'] for row in specs())
    value = dict(format='D192-fillsweep-requests-v1', split='train', frozen_at_utc=stamp(),
                 prereg_sha256=sha(prereg), amendment_sha256=sha(HERE / 'AMENDMENT-1.md'),
                 specs=specs(), source_files=build.source_snapshot(),
                 builder_sha256=sha(build.__file__), runtime_root=str(build.ROOT),
                 derivation_sha256=sha(__file__), root_seed_overlap_with_all_split_metadata=0,
                 train_manifest_sha256=sha(manifest), fill_targets=list(FILLS),
                 mass_multipliers=MASSES, physical_fill_tolerance=TOLERANCE,
                 expected_maps=90)
    build.atomic_json(request_path, value)
    publish(value)
    return value


def footprint(box):
    return 1 if box['length_ft'] == 20 else 2


def stratum(box):
    return json.dumps([box['pod'], box['length_ft'], box['reefer'],
                       box['dg'] is not None, box['empty'], box['special_stowage']], sort_keys=True)


def nested_order(cargo, seed):
    """Proportional-deficit interleaving; deterministic hashed order in strata."""
    groups = defaultdict(list)
    for box in cargo:
        groups[stratum(box)].append(box)
    for group in groups.values():
        group.sort(key=lambda box: hashlib.sha256(f"D192:{seed}:{box['id']}".encode()).hexdigest())
    counts = {key: len(group) for key, group in groups.items()}
    used = Counter()
    result = []
    for n in range(len(cargo)):
        key = min((key for key in groups if used[key] < counts[key]),
                  key=lambda key: (-((n + 1) * counts[key] / len(cargo) - used[key]), key))
        result.append(groups[key][used[key]])
        used[key] += 1
    return result


def nearest_prefix(order, target_cells):
    occupancy = [0]
    for box in order:
        occupancy.append(occupancy[-1] + footprint(box))
    # Prefer lower occupancy on an exact tie; all rungs share this one order.
    count = min(range(1, len(order) + 1), key=lambda n: (abs(occupancy[n] - target_cells), n))
    return order[:count]


def derive_payload(parent, selected, map_id, multiplier, catalogue):
    payload = deepcopy(parent)
    keep = {box['id'] for box in selected}
    payload['id'] = f'D192.{map_id}'
    payload['load_list'] = sorted(keep)
    for box in payload['containers']:
        if box['id'] in keep:
            box['gross_mass_kg'] = max(box['tare_kg'], multiplier * box['gross_mass_kg'])
            box['weight_class'] = catalogue.classify(box['gross_mass_kg'])
            # Do not retain a stale interchangeability key after mass changes.
            box['category'] = None
    # Unselected targets remain ordinary ashore cargo; full parent yard and
    # inventory stay fixed. They are not loading demand or movable onboard mass.
    # Keep the parent's producer fields as lineage, while marking the derived identity.
    payload['family'].update(fillsweep='D192', fillsweep_parent_id=parent['id'],
                             fillsweep_mass_multiplier=repr(multiplier),
                             n_boxes=str(len(keep)))
    payload['family']['booking_utilisation_teu'] = repr(sum(box['length_ft'] / 20 for box in selected) /
                                                       len(parent['vessel']['cells']))
    return payload


def publish(requests):
    rows = []
    for spec in requests['specs']:
        complete = HERE / 'parents' / f"seed-{spec['seed']}.rows.json"
        if complete.exists():
            rows.extend(json.loads(complete.read_text())['maps'])
        else:
            rows.extend(map_row(spec, fill, mass) for fill in FILLS for mass in MASSES)
    build.atomic_json(HERE / 'cohort.json', dict(format='D192-fillsweep-cohort-v1', split='train',
                      request_sha256=sha(HERE / 'requests.json'), generated_at_utc=stamp(),
                      expected_maps=90, maps=rows,
                      generation_counts=dict(Counter(row['generation_status'] for row in rows))))


def generate_parent(requests, index):
    from stowage.frozen import current_provenance, require_clean
    from stowage.io import load_instance, save_instance
    from stowage.model.instance import Instance
    from stowage.rules.catalogue import load_catalogue, rule_freeze_v1_enabled
    from stowage.generate.random_yard import completion_failures, regional_booking_mass_budget, _full_vessel
    from stowage.generate.random_export import full_export_policy
    from stowage.params import load_param_file

    assert rule_freeze_v1_enabled() is False
    assert sha(HERE / 'PREREG.md') == requests['prereg_sha256']
    assert sha(__file__) == requests['derivation_sha256'], 'derivation changed after request freeze'
    assert build.source_snapshot() == requests['source_files'], 'source epoch changed'
    spec = requests['specs'][index]
    rows_path = HERE / 'parents' / f"seed-{spec['seed']}.rows.json"
    if rows_path.exists():
        print(json.dumps(dict(event='resumed_parent', seed=spec['seed'])), flush=True)
        return
    result = build.generate_one((spec, str(HERE / 'parents'), requests['source_files']))
    print(json.dumps(result), flush=True)
    rows = [map_row(spec, fill, mass) for fill in FILLS for mass in MASSES]
    if result['status'] == 'failed':
        for row in rows:
            row.update(generation_status='failed', generation_error=result['error'])
        build.atomic_json(rows_path, dict(parent_result=result, maps=rows))
        return
    path = build.shard_path(spec, HERE / 'parents')
    audit, parent_instance = build.validate_shard(path, current_sources=requests['source_files'])
    require_clean(parent_instance, what='D192 derivative parent')
    parent = parent_instance.model_dump(mode='json')
    cargo = [box for box in parent['containers'] if box['id'] in set(parent['load_list'])]
    order = nested_order(cargo, spec['seed'])
    capacity = len(parent['vessel']['cells'])
    reference = full_export_policy(_full_vessel(spec['vessel']), load_param_file('cargo', 'feeder_export'))
    background_digest = digest(parent['vessel'])
    for row in rows:
        selected = nearest_prefix(order, row['fill_target'] / 100 * capacity)
        physical = sum(footprint(box) for box in selected)
        fill = physical / capacity
        if abs(fill - row['fill_target'] / 100) > TOLERANCE + 1e-12:
            row.update(generation_status='failed', generation_error='parent cannot supply target physical fill within 2.5pp',
                       physical_cell_fill=fill, realised_cell_fill=fill)
            continue
        payload = derive_payload(parent, selected, row['map_id'], row['mass_multiplier'], load_catalogue().weight_classes)
        provenance = current_provenance(seed=spec['seed'], created_by='experiment.D192.fillsweep').merge_nested(
            parent_instance, parent_ids=[parent_instance.id])
        payload['provenance'] = provenance.model_dump(mode='json')
        child = Instance.model_validate(payload)
        require_clean(child, what='D192 derivative input')
        assert child.vessel == parent_instance.vessel and child.onboard == parent_instance.onboard
        cargo_child = [box for box in child.containers if box.id in set(child.load_list)]
        mass = sum(box.gross_mass_kg for box in cargo_child)
        budget = regional_booking_mass_budget(child.vessel, reference, mass)
        child.family['booking_utilisation_mass'] = repr(mass / budget)
        out = HERE / 'maps/train/medium' / spec['vessel'] / f"f{row['fill_target']}-{row['mass_level']}" / f"seed-{spec['seed']}.json"
        out.parent.mkdir(parents=True, exist_ok=True)
        if out.exists():
            assert load_instance(out) == child, 'existing derivative differs; no replacement allowed'
        else:
            save_instance(child, out)
        assert load_instance(out) == child
        c1_error = None
        try:
            c1 = list(completion_failures(child))
        except Exception as error:
            c1 = None
            c1_error = f'{type(error).__name__}: {error}'
        counts = Counter(box.weight_class for box in cargo_child)
        row.update(generation_status='accepted', input=str(out.relative_to(ROOT)), sha256=sha(out),
                   physical_cell_fill=fill, realised_cell_fill=fill, physical_cell_demand=physical,
                   physical_cell_capacity=capacity, nominal_teu=sum(box.length_ft / 20 for box in cargo_child),
                   cargo_tonnes=mass / 1000, n_loads=len(cargo_child), generator_mass_fraction=mass / budget,
                   generator_mass_budget_kg=budget, weight_classes=dict(counts),
                   cargo_mix=dict(pod=dict(Counter(box.pod for box in cargo_child)),
                                  length_ft=dict(Counter(box.length_ft for box in cargo_child)),
                                  reefer=sum(box.reefer for box in cargo_child), dg=sum(box.dg is not None for box in cargo_child),
                                  empty=sum(box.empty for box in cargo_child),
                                  special_stowage=dict(Counter(request for box in cargo_child for request in box.special_stowage))),
                   fixed_background_mass_kg=sum(load.mass_kg for load in child.vessel.fixed_loads),
                   vessel_sha256=background_digest, movable_onboard_placements=len(child.onboard.placements),
                   parent_input=str(path.relative_to(ROOT)), parent_sha256=sha(path),
                   parent_audit=str(path.with_suffix('.audit.json').relative_to(ROOT)),
                   parent_audit_sha256=sha(path.with_suffix('.audit.json')),
                   parent_producer_gate_passed=True, derivative_producer_gate_passed=None,
                   c1_failures=c1, c1_error=c1_error, plan_checker_passed=None)
        build.atomic_json(out.with_suffix('.derivation.json'), dict(format='D192-derived-train-input-v1',
                          prereg_sha256=requests['prereg_sha256'], amendment_sha256=requests['amendment_sha256'],
                          request_sha256=sha(HERE / 'requests.json'),
                          derivation_sha256=sha(__file__), source_files=requests['source_files'],
                          selected_ids=sorted(box.id for box in cargo_child),
                          fixed_parent_vessel_preserved=True, fixed_parent_onboard_preserved=True,
                          fixed_parent_yard_preserved=True,
                          unselected_parent_targets_retained_ashore=True,
                          derived_input_is_not_a_producer_certificate=True, **row))
        print(json.dumps(dict(event='derived', map_id=row['map_id'], fill=fill, cargo_tonnes=mass / 1000)), flush=True)
    build.atomic_json(rows_path, dict(parent_result=result, completed_at_utc=stamp(), maps=rows))


def verify():
    from stowage.frozen import require_clean
    from stowage.io import load_instance
    requests = freeze()
    publish(requests)
    cohort = json.loads((HERE / 'cohort.json').read_text())
    seen = set()
    accepted = []
    for row in cohort['maps']:
        assert row['split'] == 'train' and row['map_id'] not in seen
        seen.add(row['map_id'])
        if row['generation_status'] != 'accepted':
            continue
        out = ROOT / row['input']
        assert sha(out) == row['sha256']
        child = load_instance(out)
        require_clean(child, what='D192 derivative verification')
        parent = load_instance(ROOT / row['parent_input'])
        assert child.vessel == parent.vessel and child.onboard == parent.onboard
        assert child.voyage == parent.voyage and child.port == parent.port
        assert child.yard == parent.yard
        cargo = [box for box in child.containers if box.id in set(child.load_list)]
        assert sum(1 if box.length_ft == 20 else 2 for box in cargo) == row['physical_cell_demand']
        assert abs(row['physical_cell_fill'] - row['fill_target'] / 100) <= TOLERANCE + 1e-12
        accepted.append(row)
    assert len(seen) == 90
    for spec in requests['specs']:
        prior = set()
        for fill in FILLS:
            twins = [row for row in accepted if row['seed'] == spec['seed'] and row['fill_target'] == fill]
            if not twins:
                continue
            assert len(twins) == 2
            pair = [load_instance(ROOT / row['input']) for row in twins]
            assert pair[0].load_list == pair[1].load_list and pair[0].yard == pair[1].yard
            assert prior <= set(pair[0].load_list)
            prior = set(pair[0].load_list)
    result = dict(verified_at_utc=stamp(), requested=90, accepted=len(accepted),
                  counts=cohort['generation_counts'], all_accepted_hashes_schema_and_lineage_valid=True,
                  all_accepted_inputs_clean=True, exact_background_and_onboard_preserved=True,
                  mass_twins_same_cargo_and_yard=True, fill_subsets_nested=True)
    build.atomic_json(HERE / 'generation-verification.json', result)
    print(json.dumps(result), flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=('freeze', 'generate', 'publish', 'verify'))
    parser.add_argument('--parent-index', type=int, choices=range(9))
    parser.add_argument('--runtime', type=Path, default=HERE / 'runs/main/runtime')
    args = parser.parse_args()
    configure_runtime(args.runtime)
    if args.action == 'verify':
        verify()
        return
    requests = freeze()
    if args.action == 'generate':
        started = time.monotonic()
        for index in ([args.parent_index] if args.parent_index is not None else range(9)):
            generate_parent(requests, index)
            publish(requests)
        print(json.dumps(dict(event='generation_invocation_done', elapsed_s=time.monotonic() - started)), flush=True)
    publish(requests)


if __name__ == '__main__':
    main()
