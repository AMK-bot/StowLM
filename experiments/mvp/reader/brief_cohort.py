"""Freeze D220's unselected validation brief cohort, without running a planner.

The instruction generator sees ship/cargo geometry only. Stored plan outcomes are
examined only after every instruction has been fixed. No failed or impossible
case is replaced, and independent expected labels never enter the live parser.
"""
from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import os
from pathlib import Path
import random
import tempfile

from llm.instructions.apply import _class_matches, _dg_classes, apply
from stowage.conventions import parse_slot
from stowage.model import MovePlan

from experiments.mvp.reader.schema import SCHEMA_VERSION, context_from_instance, validate_answer
from experiments.mvp.reader.select_binding import _selection_instance, per_box_slot_screen

ROOT = Path(__file__).resolve().parents[3]
READER = ROOT / 'experiments/mvp/reader'
DEFAULT_SEED = 22020261031
STANDING_RULES = ['One port per hatch.']
TYPES = ('close_bay', 'max_tiers', 'dg_keep_away')
ASSUMPTIONS = [
    'Fixed order is ascending stored val-0.9 result filename, first 30 validation small maps; no pass or violation filtering.',
    'A fixed CloseBay/MaxTiers/DgKeepAway cycle is assigned before inspecting stored outcomes.',
    'DG-free maps retain their assigned DG instruction using fixed class 3; it is explicitly labelled vacuous, never replaced.',
    'Static screens prove only the recorded necessary-condition failures; passing a screen does not certify feasibility.',
    'Standing rules are preferences and voyage instructions are hard; independent labels are evaluation-only.',
]


def _bytes(value):
    return (json.dumps(value, indent=2, sort_keys=True) + '\n').encode()


def _sha(data):
    return hashlib.sha256(data).hexdigest()


def _relative(path, root):
    return str(path.resolve().relative_to(root.resolve()))


def instruction_for(raw, index, seed=DEFAULT_SEED):
    """Deterministically choose one restriction using real ship values only."""
    context = context_from_instance(raw)
    instance = _selection_instance(raw)
    rng = random.Random(int(_sha(f'{seed}:{raw["id"]}'.encode()), 16))
    kind = TYPES[index % len(TYPES)]
    bay = rng.choice(context['bays'])
    metadata = {'value_source': 'instance physical geometry and cargo; no stored plan inspected'}
    if kind == 'close_bay':
        constraint = {'type': kind, 'bay': bay}
        sentence = f'Do not load any containers in bay {bay:02d}.'
    elif kind == 'max_tiers':
        eligible = [(int(b), zone, cap) for b, zones in sorted(context['max_tiers_by_bay'].items())
                    for zone, cap in sorted(zones.items()) if cap > 0]
        if not eligible:
            raise ValueError(f'{raw["id"]}: no physical tiers for MaxTiers')
        bay, zone, cap = rng.choice(eligible)
        limit = cap - 1
        constraint = {'type': kind, 'bay': bay, 'zone': zone, 'max_tiers': limit}
        sentence = f'In bay {bay:02d}, use at most {limit} occupied tiers per {zone} stack.'
    else:
        classes = sorted({c for box in instance.containers for c in _dg_classes(box)})
        imdg_class = rng.choice(classes) if classes else '3'
        physical_bays = sorted({parse_slot(c.id).bay for c in instance.vessel.cells})
        span = max(physical_bays) // 2 - min(physical_bays) // 2
        distance = rng.randint(1, min(20, max(1, span)))
        constraint = {'type': kind, 'imdg_class': imdg_class, 'min_bays': distance,
                      'away_from': {'kind': 'bay', 'bay': bay}}
        sentence = (f'Keep all IMDG class {imdg_class} containers at least {distance} '
                    f'20-foot bays away from bay {bay:02d}.')
        active = set(instance.load_list) | {p.container_id for p in instance.onboard.placements
                                           if p.container_id not in set(instance.discharge_list)}
        matching = sorted(b.id for b in instance.containers
                          if b.id in active and _class_matches(imdg_class, _dg_classes(b)))
        metadata.update(dg_class_source='real cargo' if classes else 'fixed class 3: no DG cargo',
                        matching_active_cargo=matching, vacuous=not matching)
    expected = {'schema_version': SCHEMA_VERSION, 'constraint': constraint}
    validate_answer(json.dumps(expected), context)
    return {'type': kind, 'sentence': sentence, 'expected': expected,
            'context': context, 'instruction_selection': metadata,
            'standing_rules': list(STANDING_RULES), 'voyage_instructions': [sentence]}


def static_screen(raw, expected):
    """Sound, permissive necessary conditions; no solver or checker calls."""
    instance = _selection_instance(raw)
    answer = validate_answer(json.dumps(expected), context_from_instance(raw))
    compiled = apply(instance, answer)
    reasons = []
    shortfall = compiled.capacity_shortfall()
    if shortfall:
        reasons.append({'proof': 'open_cell_capacity', 'detail': shortfall})
    no_slots = per_box_slot_screen(instance, compiled)
    if no_slots:
        reasons.append({'proof': 'no_size_compatible_allowed_footprint', 'containers': no_slots})
    # The reader's MaxTiers/DG semantics restrict fixed final retained occupancy.
    # CloseBay bars new placements and leaves unmoved retained cargo alone.
    if answer.type != 'close_bay':
        retained = [p for p in instance.onboard.placements
                    if p.container_id not in set(instance.discharge_list)]
        conflicts = [p.container_id for p in retained if compiled.forbids(p.container_id, p.slot)]
        if conflicts:
            reasons.append({'proof': 'fixed_retained_cargo_conflict', 'containers': conflicts})
    return {'proven_impossible': bool(reasons), 'reasons': reasons,
            'scope': 'necessary instruction-only capacity, size footprints, fixed retained occupancy; no feasibility claim when screens pass'}


def baseline_violations(raw, plan_raw, expected):
    """Independent gold-constraint predicate, including final retained occupancy."""
    instance = _selection_instance(raw)
    answer = validate_answer(json.dumps(expected), context_from_instance(raw))
    compiled = apply(instance, answer)
    plan = MovePlan.model_validate(plan_raw)
    violations = compiled.violations(plan)
    if answer.type != 'close_bay':
        positions = {p.container_id: p.slot for p in instance.onboard.placements
                     if p.container_id not in set(instance.discharge_list)}
        # These restrictions are placement-local. The stored planner's legal
        # final move destination is sufficient; the full checker is a later gate.
        for move in plan.moves:
            if move.kind in ('load', 'restow_on', 'restow_shift'):
                positions[move.container_id] = move.dst
            elif move.kind in ('discharge', 'restow_off'):
                positions.pop(move.container_id, None)
        violations.extend(f'final {bid} at {slot}: {reason}' for bid, slot in sorted(positions.items())
                          if (reason := compiled.forbids(bid, slot)))
    return sorted(set(violations))


def build_cohort(*, root=ROOT, seed=DEFAULT_SEED, count=30):
    root = Path(root).resolve()
    selected = []
    for baseline_file in sorted((root / 'experiments/mvp/frontier/runs/val-0.9').glob('*.json')):
        baseline_bytes = baseline_file.read_bytes()
        baseline = json.loads(baseline_bytes)
        if not isinstance(baseline, dict) or not baseline.get('input'):
            continue
        input_path = Path(baseline['input'])
        absolute = input_path if input_path.is_absolute() else root / input_path
        relative = absolute.resolve().relative_to(root)
        if any(part in relative.parts for part in ('train', 'test', 'test-v3')):
            raise ValueError(f'Forbidden non-validation baseline input: {relative}')
        if 'val' not in relative.parts or 'small' not in relative.parts:
            continue
        if any(s['input'] == str(relative) for s, _, _ in selected):
            raise ValueError('Duplicate validation map in fixed baseline order')
        raw_bytes = absolute.read_bytes()
        raw = json.loads(raw_bytes)
        digest = _sha(raw_bytes)
        if baseline.get('input_sha256') and baseline['input_sha256'] != digest:
            raise ValueError(f'Stored baseline input hash mismatch: {relative}')
        index = len(selected)
        case = {'case_id': f'brief-{index + 1:02d}', 'index': index,
                'instance_id': raw['id'], 'input': str(relative), 'input_sha256': digest,
                'baseline_file': _relative(baseline_file, root),
                'baseline_sha256': _sha(baseline_bytes), **instruction_for(raw, index, seed)}
        selected.append((case, raw, baseline))
        if len(selected) == count:
            break
    if len(selected) != count:
        raise ValueError(f'Need {count} small validation maps; found {len(selected)}')
    # Instruction selection for the entire cohort has finished before any plan,
    # objective, pass flag or instruction feasibility is inspected below.
    for case, raw, baseline in selected:
        case['static_screen'] = static_screen(raw, case['expected'])
        case['baseline_passed'] = bool(baseline.get('passed'))
        case['baseline_weighted'] = baseline.get('weighted')
        if baseline.get('plan'):
            violations = baseline_violations(raw, baseline['plan'], case['expected'])
            case.update(baseline_violations=violations,
                        baseline_instruction_obeyed=not violations,
                        baseline_violation_count=len(violations))
        else:
            case.update(baseline_violations=None, baseline_instruction_obeyed=None,
                        baseline_violation_count=None)
    return {'schema_version': 'reader-brief-cohort/1', 'validation_only': True,
            'selection_is_blind': True, 'seed': seed, 'count': count,
            'standing_rules': list(STANDING_RULES), 'assumptions': ASSUMPTIONS,
            'planner_calls_in_selection': 0, 'parser_calls_in_selection': 0,
            'cases': [case for case, _, _ in selected]}


def _atomic_write(path, data):
    fd, temporary = tempfile.mkstemp(prefix=f'.{path.name}.', dir=path.parent)
    try:
        with os.fdopen(fd, 'wb') as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


def prepare(output_dir=None, *, seed=DEFAULT_SEED, count=30, root=ROOT):
    """Atomically create immutable artifacts, or verify and reuse frozen ones.

    cohort.json is written first. An interrupted preregister write can be safely
    resumed from those exact frozen bytes; existing cohorts are never rebuilt.
    """
    if count < 1:
        raise ValueError('count must be positive')
    output_dir = Path(output_dir) if output_dir is not None else Path(root) / 'experiments/mvp/reader/runs/brief'
    output_dir.mkdir(parents=True, exist_ok=True)
    lock = output_dir / '.prepare.lock'
    # Keep a stable lock inode: unlinking a lock can let another process enter
    # through a different inode. flock releases automatically after a crash.
    stream = lock.open('a+b')
    try:
        fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        cohort_path, prereg_path = output_dir / 'cohort.json', output_dir / 'preregister.json'
        if prereg_path.exists() and not cohort_path.exists():
            raise ValueError('Frozen preregistration exists without cohort; refusing reconstruction')
        if cohort_path.exists():
            payload = cohort_path.read_bytes()
            cohort = json.loads(payload)
            if (cohort.get('schema_version') != 'reader-brief-cohort/1'
                    or cohort.get('seed') != seed or cohort.get('count') != count):
                raise ValueError('Existing frozen cohort differs from requested configuration')
        else:
            cohort = build_cohort(root=root, seed=seed, count=count)
            payload = _bytes(cohort)
            _atomic_write(cohort_path, payload)
        prereg = {'schema_version': 'reader-brief-preregister/1', 'decision': 'D220',
                  'cohort_sha256': _sha(payload), 'count': count, 'seed': seed,
                  'frozen_before_reader_or_planner_runs': True,
                  'standing_rules': list(STANDING_RULES), 'assumptions': ASSUMPTIONS,
                  'max_parser_spend_usd': 2, 'max_workers': 2,
                  'admission': {'min_mask_cores': 8, 'max_load1': 45, 'max_age_s_exclusive': 120},
                  'no_repair': True, 'baseline': 'stored val-0.9 plans only; no reruns'}
        if prereg_path.exists():
            if json.loads(prereg_path.read_bytes()) != prereg:
                raise ValueError('Frozen cohort/preregistration integrity mismatch')
        else:
            _atomic_write(prereg_path, _bytes(prereg))
        return cohort
    finally:
        stream.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output-dir', type=Path, default=READER / 'runs/brief')
    parser.add_argument('--seed', type=int, default=DEFAULT_SEED)
    parser.add_argument('--count', type=int, default=30)
    args = parser.parse_args()
    cohort = prepare(args.output_dir, seed=args.seed, count=args.count)
    print(json.dumps({'count': cohort['count'], 'seed': cohort['seed'],
                      'output_dir': str(args.output_dir),
                      'static_impossible': sum(c['static_screen']['proven_impossible'] for c in cohort['cases'])}))


if __name__ == '__main__':
    main()
