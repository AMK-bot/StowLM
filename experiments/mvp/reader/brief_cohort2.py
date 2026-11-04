"""D222: a second, explicitly static-screen-selected cohort on the same maps.

This preserves the first cohort. It enumerates a finite instruction universe,
screens every candidate, and freezes a seeded draw without a model, planner,
checker, or constrained-outcome selection. Screen admission is not feasibility.
"""
from __future__ import annotations

import argparse
from collections import Counter
import fcntl
import json
from pathlib import Path
import random

from llm.instructions.apply import _class_matches, _dg_classes, apply
from stowage.conventions import parse_slot

from experiments.mvp.reader.brief_cohort import (
    ROOT, READER, STANDING_RULES, TYPES, _atomic_write, _bytes, _sha,
    baseline_violations, static_screen,
)
from experiments.mvp.reader.schema import SCHEMA_VERSION, IMDG_CLASSES, context_from_instance, validate_answer
from experiments.mvp.reader.select_binding import _selection_instance

DEFAULT_SEED = 22220261031
POLICY = [
    'Exactly the same maps and order as the frozen first D220 cohort; no map swaps.',
    'Enumerate all context bay closures; all bay-specific and vessel-wide physical tier caps from zero through the real maximum; DG bay targets and physically derived distances for classes present in active cargo.',
    'DG candidates require matching active load or retained cargo; DG-free maps use other types and are never assigned a fabricated class.',
    'Admit only candidates the unchanged first-cohort static screen cannot prove impossible; this is stated selection, not a feasibility certificate.',
    'Prefer tightening candidates on each map; use a physical-maximum tier cap only if no tightening candidate survives that map\'s screen.',
    'Minimize the sum of squared type counts over possible assignments; seeded matching breaks ties, then seed-draw one admissible candidate of the assigned type per map.',
    'No baseline plan, pass flag, move count, violation or constrained result enters candidate admission or the draw. Baselines are evaluated only after all instructions are fixed.',
    'Expected labels and candidate manifests are evaluation-only; they are never supplied to the parser or planner.',
]


def _random(seed, label):
    return random.Random(int(_sha(f'{seed}:{label}'.encode()), 16))


def _key(seed, label):
    return _sha(f'{seed}:{label}'.encode())


def _envelope(constraint):
    return {'schema_version': SCHEMA_VERSION, 'constraint': constraint}


def candidate_universe(raw):
    """Enumerate the declared universe using physical values and active cargo."""
    context = context_from_instance(raw)
    instance = _selection_instance(raw)
    candidates = []
    active = set(instance.load_list) | {p.container_id for p in instance.onboard.placements
                                       if p.container_id not in set(instance.discharge_list)}
    boxes = [box for box in instance.containers if box.id in active]

    def add(constraint, sentence):
        expected = _envelope(constraint)
        answer = validate_answer(json.dumps(expected), context)
        compiled = apply(instance, answer)
        matching = (sorted(box.id for box in boxes
                           if _class_matches(answer.imdg_class, _dg_classes(box)))
                    if answer.type == 'dg_keep_away' else [])
        candidates.append({'candidate_id': f'candidate-{len(candidates) + 1:04d}',
                           'type': answer.type, 'sentence': sentence, 'expected': expected,
                           'is_tightening': bool(compiled._all_boxes or any(compiled._per_box.values())),
                           'matching_active_cargo': matching,
                           'vacuous': answer.type == 'dg_keep_away' and not matching,
                           'static_screen': static_screen(raw, expected)})

    for bay in context['bays']:
        add({'type': 'close_bay', 'bay': bay}, f'Do not load any containers in bay {bay:02d}.')
    scopes = [(None, context['max_tiers'])] + [
        (int(bay), caps) for bay, caps in sorted(context['max_tiers_by_bay'].items(), key=lambda p: int(p[0]))]
    for bay, caps in scopes:
        for zone in ('hold', 'deck'):
            if not caps[zone]:
                continue
            for limit in range(caps[zone] + 1):
                prefix = 'Across the vessel' if bay is None else f'In bay {bay:02d}'
                add({'type': 'max_tiers', 'bay': bay, 'zone': zone, 'max_tiers': limit},
                    f'{prefix}, use at most {limit} occupied tiers per {zone} stack.')
    # Compatibility suffixes (e.g. 1.4S) identify a real division; the reader's
    # schema represents the division and the shared predicate matches suffixes.
    classes = sorted({value.rstrip('ABCDEFGHJKLNS') for box in boxes for value in _dg_classes(box)})
    if set(classes) - IMDG_CLASSES:
        raise ValueError('Active cargo has a DG class outside the reader contract')
    physical_bays = sorted({parse_slot(c.id).bay for c in instance.vessel.cells})
    for imdg_class in classes:
        for bay in context['bays']:
            # Every positive 20-ft distance through one beyond the physical
            # span is included. Greater values close the same complete vessel.
            max_distance = max(abs(p - bay) // 2 for p in physical_bays)
            for distance in range(1, min(20, max_distance + 1) + 1):
                add({'type': 'dg_keep_away', 'imdg_class': imdg_class, 'min_bays': distance,
                     'away_from': {'kind': 'bay', 'bay': bay}},
                    f'Keep all IMDG class {imdg_class} containers at least {distance} '
                    f'20-foot bays away from bay {bay:02d}.')
    return candidates


def eligible_candidates(candidates):
    admitted = [c for c in candidates if not c['static_screen']['proven_impossible']]
    tightening = [c for c in admitted if c['is_tightening']]
    return tightening or admitted


def balanced_assignment(available_types, seed=DEFAULT_SEED):
    """Seeded ordinary bipartite matching, minimizing squared type counts.

    This is only instruction assignment; no placement solver is imported or run.
    Enumerating count triples is bounded by (n + 1)(n + 2)/2 for n maps.
    """
    n = len(available_types)
    if any(not kinds for kinds in available_types):
        raise ValueError('A fixed map has no statically admitted instruction; no replacement allowed')
    limits = {kind: sum(kind in kinds for kinds in available_types) for kind in TYPES}
    vectors = [(a, b, n-a-b) for a in range(n + 1) for b in range(n-a + 1)
               if a <= limits[TYPES[0]] and b <= limits[TYPES[1]] and n-a-b <= limits[TYPES[2]]]
    vectors.sort(key=lambda v: (sum(c*c for c in v), _key(seed, f'counts:{v}')))
    order = sorted(range(n), key=lambda i: _key(seed, f'map-order:{i}'))
    preferences = {i: sorted(available_types[i], key=lambda kind: _key(seed, f'type-order:{i}:{kind}'))
                   for i in range(n)}
    for vector in vectors:
        capacities = dict(zip(TYPES, vector))
        occupants = {kind: [] for kind in TYPES}
        assignment = {}

        def place(index, seen):
            if index in seen:
                return False
            seen.add(index)
            for kind in preferences[index]:
                if len(occupants[kind]) < capacities[kind]:
                    occupants[kind].append(index)
                    assignment[index] = kind
                    return True
                for previous in list(occupants[kind]):
                    if place(previous, seen):
                        occupants[kind].remove(previous)
                        occupants[kind].append(index)
                        assignment[index] = kind
                        return True
            return False

        if all(place(index, set()) for index in order):
            return [assignment[i] for i in range(n)]
    raise ValueError('No type assignment for fixed maps; no replacement allowed')


def _first_cohort(root, first_cohort_path=None):
    path = Path(first_cohort_path) if first_cohort_path is not None else root / 'experiments/mvp/reader/runs/brief/cohort.json'
    payload = path.read_bytes()
    first = json.loads(payload)
    if first.get('schema_version') != 'reader-brief-cohort/1' or not first.get('validation_only'):
        raise ValueError('Expected frozen validation-only first brief cohort')
    prereg = json.loads(path.with_name('preregister.json').read_bytes())
    if prereg['cohort_sha256'] != _sha(payload):
        raise ValueError('First cohort preregistration hash mismatch')
    if first['count'] != len(first['cases']) or len({c['input'] for c in first['cases']}) != first['count']:
        raise ValueError('Invalid first-cohort map inventory')
    return first, _sha(payload)


def build_cohort(*, root=ROOT, seed=DEFAULT_SEED, first_cohort_path=None):
    root = Path(root).resolve()
    first, first_hash = _first_cohort(root, first_cohort_path)
    records, universes = [], []
    for index, prior in enumerate(first['cases']):
        input_path = (root / prior['input']).resolve()
        relative = input_path.relative_to(root)
        if ('val' not in relative.parts or 'small' not in relative.parts
                or any(part in relative.parts for part in ('train', 'test', 'test-v3'))):
            raise ValueError('Second cohort may read only the fixed small validation maps')
        raw_bytes = input_path.read_bytes()
        if _sha(raw_bytes) != prior['input_sha256']:
            raise ValueError('Frozen first-cohort input changed')
        raw = json.loads(raw_bytes)
        if raw['id'] != prior['instance_id']:
            raise ValueError('First-cohort instance ID mismatch')
        universe = candidate_universe(raw)
        eligible = eligible_candidates(universe)
        universes.append({'index': index, 'instance_id': raw['id'], 'input': prior['input'],
                          'candidate_count': len(universe), 'candidates': universe,
                          'admitted_count': sum(not c['static_screen']['proven_impossible'] for c in universe),
                          'eligible_candidate_ids': [c['candidate_id'] for c in eligible],
                          'physical_cap_fallback': not any(c['is_tightening'] for c in eligible)})
        records.append((prior, raw, eligible))
    assignment = balanced_assignment([{c['type'] for c in eligible} for _, _, eligible in records], seed)
    selected, draw = [], []
    for index, ((prior, raw, eligible), kind) in enumerate(zip(records, assignment)):
        options = [c for c in eligible if c['type'] == kind]
        chosen = _random(seed, f'candidate:{raw["id"]}:{kind}').choice(options)
        case = {key: prior[key] for key in ('index', 'input', 'input_sha256', 'baseline_file', 'baseline_sha256', 'instance_id')}
        case.update(case_id=f'brief2-{index + 1:02d}', first_cohort_case_id=prior['case_id'],
                    candidate_id=chosen['candidate_id'], type=kind,
                    sentence=chosen['sentence'], expected=chosen['expected'],
                    context=context_from_instance(raw), standing_rules=list(STANDING_RULES),
                    voyage_instructions=[chosen['sentence']], static_screen=chosen['static_screen'],
                    instruction_selection={'selection': 'seeded draw after static-screen admission; not certified feasible',
                                           'is_tightening': chosen['is_tightening'],
                                           'matching_active_cargo': chosen['matching_active_cargo'],
                                           'vacuous': chosen['vacuous'], 'dg_class_source': 'real active cargo' if kind == 'dg_keep_away' else None})
        selected.append((case, raw))
        draw.append({'index': index, 'instance_id': raw['id'], 'type': kind,
                     'candidate_id': chosen['candidate_id'],
                     'draw_pool_candidate_ids': [c['candidate_id'] for c in options]})
    # Only now are stored plans and baseline outcomes opened and evaluated.
    for case, raw in selected:
        baseline_bytes = (root / case['baseline_file']).read_bytes()
        if _sha(baseline_bytes) != case['baseline_sha256']:
            raise ValueError('Frozen stored baseline changed')
        baseline = json.loads(baseline_bytes)
        case.update(baseline_passed=bool(baseline.get('passed')), baseline_weighted=baseline.get('weighted'))
        violations = baseline_violations(raw, baseline['plan'], case['expected']) if baseline.get('plan') else None
        case.update(baseline_violations=violations,
                    baseline_instruction_obeyed=not violations if violations is not None else None,
                    baseline_violation_count=len(violations) if violations is not None else None)
    counts = Counter(assignment)
    return {'schema_version': 'reader-brief2-cohort/1', 'decision': 'D222',
            'validation_only': True, 'selection_is_blind': False,
            'selection_basis': 'static necessary-condition screens, never plan outcomes; not feasibility-certified',
            'first_cohort_sha256': first_hash, 'count': len(selected), 'seed': seed,
            'standing_rules': list(STANDING_RULES), 'assumptions': POLICY,
            'planner_calls_in_selection': 0, 'parser_calls_in_selection': 0,
            'candidate_universe_sha256': _sha(_bytes(universes)), 'candidate_universe': universes,
            'draw_manifest': {'type_counts': {kind: counts[kind] for kind in TYPES},
                              'squared_type_count_objective': sum(counts[k] ** 2 for k in TYPES),
                              'draw': draw},
            'no_matching_cargo_instruction_count': sum(c['instruction_selection']['vacuous'] for c, _ in selected),
            'physical_cap_fallback_count': sum(u['physical_cap_fallback'] for u in universes),
            'cases': [case for case, _ in selected]}


def prepare(output_dir=None, *, seed=DEFAULT_SEED, root=ROOT, first_cohort_path=None):
    """Freeze cohort and complete universe/draw manifest; never change cohort 1."""
    root = Path(root).resolve()
    output_dir = Path(output_dir) if output_dir is not None else root / 'experiments/mvp/reader/runs/brief2'
    output_dir.mkdir(parents=True, exist_ok=True)
    with (output_dir / '.prepare.lock').open('a+b') as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        cohort_path, prereg_path = output_dir / 'cohort.json', output_dir / 'preregister.json'
        first, first_hash = _first_cohort(root, first_cohort_path)
        if prereg_path.exists() and not cohort_path.exists():
            raise ValueError('Frozen preregistration exists without cohort; refusing reconstruction')
        if cohort_path.exists():
            payload = cohort_path.read_bytes()
            cohort = json.loads(payload)
            if (cohort.get('schema_version') != 'reader-brief2-cohort/1' or cohort.get('seed') != seed
                    or cohort.get('first_cohort_sha256') != first_hash or cohort.get('count') != first['count']):
                raise ValueError('Existing frozen cohort differs from requested configuration')
        else:
            cohort = build_cohort(root=root, seed=seed, first_cohort_path=first_cohort_path)
            payload = _bytes(cohort)
            _atomic_write(cohort_path, payload)
        prereg = {'schema_version': 'reader-brief2-preregister/1', 'decision': 'D222',
                  'cohort_sha256': _sha(payload), 'first_cohort_sha256': first_hash,
                  'candidate_universe_sha256': cohort['candidate_universe_sha256'],
                  'seed': seed, 'count': cohort['count'], 'policy': POLICY,
                  'frozen_before_reader_or_planner_runs': True, 'first_cohort_preserved': True,
                  'selection_is_blind': False, 'screen_admission_is_not_feasibility': True,
                  'no_matching_cargo_instruction_count': cohort['no_matching_cargo_instruction_count'],
                  'physical_cap_fallback_count': cohort['physical_cap_fallback_count'],
                  'type_counts': cohort['draw_manifest']['type_counts'],
                  'max_parser_spend_usd': 2, 'max_workers': 2, 'no_repair': True,
                  'admission': {'min_mask_cores': 8, 'max_load1': 45, 'max_age_s_exclusive': 120},
                  'baseline': 'stored val-0.9 plans only; no reruns'}
        if prereg_path.exists():
            if json.loads(prereg_path.read_bytes()) != prereg:
                raise ValueError('Frozen cohort/preregistration integrity mismatch')
        else:
            _atomic_write(prereg_path, _bytes(prereg))
        return cohort


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output-dir', type=Path, default=READER / 'runs/brief2')
    parser.add_argument('--seed', type=int, default=DEFAULT_SEED)
    args = parser.parse_args()
    cohort = prepare(args.output_dir, seed=args.seed)
    print(json.dumps({'count': cohort['count'], 'seed': cohort['seed'],
                      'type_counts': cohort['draw_manifest']['type_counts'],
                      'no_matching_cargo_instruction_count': cohort['no_matching_cargo_instruction_count'],
                      'physical_cap_fallback_count': cohort['physical_cap_fallback_count'],
                      'cohort_sha256': _sha(_bytes(cohort))}))


if __name__ == '__main__':
    main()
