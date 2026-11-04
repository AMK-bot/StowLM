"""Fresh TRAIN layovers; thin wrapper around the existing simulated generator.

The structural cohort deliberately conditions exports to standard non-DG dry20.
No solver outcome controls selection. The generated yard and route are retained.
"""
from __future__ import annotations

import argparse
from dataclasses import asdict
from functools import lru_cache
import hashlib
import json
import os
from pathlib import Path
import sys

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[5]
sys.dont_write_bytecode = True
for name in ('OMP_NUM_THREADS', 'OPENBLAS_NUM_THREADS', 'VECLIB_MAXIMUM_THREADS', 'MKL_NUM_THREADS', 'STOWAGE_IN_PORT_WORKERS'):
    os.environ[name] = '1'
os.environ['STOWAGE_VESSEL_CACHE_DIR'] = str(HERE / 'results/vessel-cache')
sys.path.insert(0, str(ROOT / 'src'))

SEED_START = 331401000
MAPS_PER_CELL = 12
SPEC = [(size, occupancy, SEED_START + i)
        for i, (size, occupancy) in enumerate(
            (s, p) for s in ('tiny', 'small', 'medium') for p in (.30, .50)
            for _ in range(MAPS_PER_CELL))]


def dump(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, sort_keys=True, indent=2) + '\n')


def validate_arrival(instance):
    """Check physical prefix/mass/E1 invariants without a solver outcome."""
    from stowage.conventions import cells_of_slot
    boxes = {b.id: b for b in instance.containers}
    occupied = {}
    for placement in instance.onboard.placements:
        used = cells_of_slot(placement.slot)
        if len(used) != 1:
            raise ValueError('structural cohort requires native dry20 slots')
        if used[0] in occupied:
            raise ValueError('arrival cargo overlaps')
        occupied[used[0]] = placement.container_id
    checks = []
    for stack in instance.vessel.stacks:
        ids = [occupied[c] for c in stack.cell_ids if c in occupied]
        if not ids:
            continue
        if [c for c in stack.cell_ids if c in occupied] != stack.cell_ids[:len(ids)]:
            raise ValueError(f'{stack.id}: arrival cargo lacks bottom support')
        masses = [boxes[b].gross_mass_kg for b in ids]
        if masses != sorted(masses, reverse=True):
            raise ValueError(f'{stack.id}: E1 mass order violated')
        cap = stack.max_mass_20_kg if stack.max_mass_20_kg is not None else stack.max_mass_kg
        if cap is not None and sum(masses) > cap+1e-6:
            raise ValueError(f'{stack.id}: arrival stack mass exceeds rating')
        checks.append(dict(stack=stack.id, boxes=len(ids), mass_kg=sum(masses), mass_cap_kg=cap))
    return dict(instance_id=instance.id, stack_checks=checks, passed=True)


@lru_cache(maxsize=3)
def decision_capacity(size):
    """Memoize seed-independent geometry used only to choose booking count."""
    from stowage.generate.random_yard import _decision_region, _tiny_region
    from stowage.vessel.fleet import load_vessel
    vessel = _decision_region(load_vessel('feeder_500', with_hull=True), size)
    if size == 'tiny':
        vessel = _tiny_region(vessel)
    return sum(c.accepts_20 for c in vessel.cells)


def build(size, occupancy, seed):
    from stowage.generate.instances import generate_simulated_instance, GenerationReport
    from stowage.generate.instances_voyage import build_voyage_spec
    from stowage.model import Instance, Placement
    from stowage.vessel.fleet_slices import recondition_slice
    from stowage.vessel.fleet_conditions import departure_tank_masses

    capacity = decision_capacity(size)
    total = max(4, round(.70 * capacity))
    report = GenerationReport()
    raw = generate_simulated_instance(size, seed=seed, noise=.2, n_boxes=total,
        dense_20ft=True, tiny_dg_enabled=False, without_dg=True, report=report)
    # Preserve generated loaded/empty mix and masses. Tiny's generator option
    # explicitly emits empty dry20. Medium cannot support the all-empty
    # background construction attempted during pre-solver cohort setup.
    boxes = list(raw.containers)
    mass = sum(b.gross_mass_kg for b in boxes if b.id in raw.load_list)
    vessel = recondition_slice(raw.vessel, expected_load_mass_kg=mass, seed=seed)
    cells = {c.id: c for c in vessel.cells}
    # Layer-first puts a bottom-closed prefix in every used stack, keeps masses
    # low, and includes both hold and deck so arrival covers can be blocked.
    stack_order = sorted(vessel.stacks, key=lambda s: (
        abs(cells[s.cell_ids[0]].tcg_m), cells[s.cell_ids[0]].lcg_m, s.id))
    positions = [s.cell_ids[level] for level in range(max(map(lambda s: len(s.cell_ids), stack_order)))
                 for s in stack_order if level < len(s.cell_ids) and cells[s.cell_ids[level]].accepts_20]
    target = round(occupancy * capacity)
    selected = sorted(sorted((b for b in boxes if b.id in raw.load_list),
                      key=lambda b: (b.gross_mass_kg, b.id))[:target],
                      key=lambda b: (-b.gross_mass_kg, b.id))
    # Force one genuine discharge below retained cargo, without changing masses.
    # Reserve the first two cells of a hold stack before the layer-first fill.
    hold = next(s for s in stack_order if not s.on_deck and len(s.cell_ids) >= 2)
    pair = hold.cell_ids[:2]
    chosen_slots = pair + [p for p in positions if p not in pair][:target - 2]
    aboard = {b.id for b in selected}
    due = selected[0].id
    current = raw.port.locode
    late = raw.rotation.calls[-2].locode
    boxes = [b.model_copy(update={'pod': current if b.id == due else late})
             if b.id in aboard else b for b in boxes]
    stacks = [s.model_copy(update={'boxes': [b for b in s.boxes if b not in aboard]})
              for s in raw.yard.stacks if any(b not in aboard for b in s.boxes)]
    identity = f'train.layover.{size}.o{round(100*occupancy)}.s{seed}'
    data = raw.model_dump(mode='json')
    data.update(id=identity, stage='B', containers=[b.model_dump(mode='json') for b in boxes],
        vessel=vessel.model_dump(mode='json'),
        load_list=[b for b in raw.load_list if b not in aboard], discharge_list=[due],
        yard=raw.yard.model_copy(update={'stacks': stacks}).model_dump(mode='json'),
        onboard=raw.onboard.model_copy(update={
            'placements': [Placement(container_id=b.id, slot=p) for b, p in zip(selected, chosen_slots)],
            'tank_masses_kg': departure_tank_masses(vessel)}).model_dump(mode='json'),
        voyage=build_voyage_spec(instance_id=identity, rotation=raw.rotation,
            port=raw.port, call_index=raw.call_index).model_dump(mode='json'))
    instance = Instance.model_validate(data)
    # Revalidation establishes slot support/length/list invariants independently
    # of solver construction. No departure feasibility is asserted here.
    output = HERE / 'cohort' / f'{identity}.json'
    dump(output, instance.model_dump(mode='json'))
    return dict(size=size, occupancy_target=occupancy, occupancy_actual=target/capacity,
        seed=seed, path=str(output.relative_to(ROOT)), capacity_cells=capacity,
        onboard_boxes=target, load_boxes=len(instance.load_list), discharge_boxes=1,
        due_box=due, retained_blocker=selected[1].id, blocked_cover=hold.hatch_cover_id,
        sha256=hashlib.sha256(output.read_bytes()).hexdigest(), generator_report=asdict(report),
        original_instance_id=raw.id, original_sha256=hashlib.sha256(raw.model_dump_json().encode()).hexdigest())


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--size', choices=('tiny', 'small', 'medium'))
    parser.add_argument('--map-seed', type=int)
    parser.add_argument('--spec-only', action='store_true')
    parser.add_argument('--validate-only', action='store_true')
    args = parser.parse_args()
    print(json.dumps(dict(pid=os.getpid(), seed_start=SEED_START, maps=len(SPEC))), flush=True)
    specification = dict(split='TRAIN', seed_range=[SEED_START, SEED_START+len(SPEC)-1],
        maps_per_size_occupancy_cell=MAPS_PER_CELL,
        selection='72 fresh seeds predeclared in COHORT.md; no outcome selection; old six excluded',
        conditioning='C3 wrapper unchanged: non-DG standard dry20 cargo; native loaded/empty masses (tiny empty); lightest cargo selected aboard; rebuilt synthetic background for actual total mass; one current-call discharge underneath retained late cargo',
        specification=[dict(size=s, occupancy_target=p, seed=n) for s,p,n in SPEC])
    dump(HERE/'cohort/SPEC.json', specification)
    if args.spec_only:
        return
    manifest_path = HERE / 'cohort/MANIFEST.json'
    rows = json.loads(manifest_path.read_text())['maps'] if manifest_path.exists() else []
    if args.validate_only:
        from stowage.io import load_instance
        checked = [validate_arrival(load_instance(ROOT/row['path'])) for row in rows]
        dump(HERE/'results/cohort_validation.json', dict(maps=checked,
            maps_present=len(rows), maps_expected=len(SPEC), cohort_complete=len(rows)==len(SPEC),
            passed=all(r['passed'] for r in checked)))
        return
    for size, occupancy, seed in SPEC:
        if args.size and size != args.size:
            continue
        if args.map_seed is not None and seed != args.map_seed:
            continue
        if any(r['seed'] == seed for r in rows):
            continue
        row = build(size, occupancy, seed)
        rows.append(row)
        dump(manifest_path, specification | dict(maps=sorted(rows, key=lambda r: r['seed'])))
        print(json.dumps({k:v for k,v in row.items() if k != 'generator_report'}), flush=True)


if __name__ == '__main__':
    main()
