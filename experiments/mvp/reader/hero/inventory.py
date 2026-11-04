"""Read-only archival inventory for a curated hero, never an accuracy cohort.

Only named validation run directories and their validation/train inputs are
opened. No checker, planner, move replay, model, or paid API runs here. A recorded
pass/audit is historical evidence; the new sentence still needs a new exact read
and a fresh independent before/after audit.
"""
from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path

from llm.instructions.apply import _class_matches, _dg_classes, apply
from stowage.conventions import parse_slot

from experiments.mvp.reader.brief_cohort import baseline_violations, static_screen
from experiments.mvp.reader.schema import SCHEMA_VERSION, IMDG_CLASSES, canonicalize, context_from_instance, validate_answer
from experiments.mvp.reader.select_binding import _selection_instance, physical_slots_for_box

ROOT = Path(__file__).resolve().parents[4]
HERE = Path(__file__).resolve().parent
BEFORE_RUNS = ('val-0.9', 'tiny48-0.9', 'unseen15-0.9')
READER_RUNS = ('binding', 'brief', 'brief2')
FORBIDDEN = {'test', 'test-v3', 'test_v3'}


def digest(data):
    return hashlib.sha256(data).hexdigest()


def canonical(value):
    if value is None:
        raise ValueError('Missing structured constraint')
    return json.dumps(canonicalize(value), sort_keys=True, separators=(',', ':'))


def sentence_for(constraint):
    kind = constraint['type']
    bay = constraint.get('bay')
    if kind == 'dg_keep_away':
        target = constraint['away_from']
        distance = constraint['min_bays']
        dg = constraint['imdg_class']
        if dg == '8' and distance == 1 and target == {'kind': 'bay', 'bay': 1}:
            return 'Keep the corrosives one bay clear of bay one.'
        cargo = {'8': 'corrosive containers', '3': 'flammable liquids'}.get(dg, f'IMDG class {dg} containers')
        number = 'one' if distance == 1 else str(distance)
        anchor = f'bay {target["bay"]}' if target['kind'] == 'bay' else 'the accommodation'
        return f'Keep {cargo} at least {number} bay{"" if distance == 1 else "s"} away from {anchor}.'
    if kind == 'close_bay':
        return f'Do not load any containers in bay {bay}.'
    if kind == 'max_tiers':
        zone = 'on deck' if constraint['zone'] == 'deck' else 'in the hold'
        scope = f' in bay {bay}' if bay is not None else ' across the vessel'
        return f'Use at most {constraint["max_tiers"]} tiers {zone}{scope}.'
    raise ValueError('Unsupported hero instruction type')


def _final_positions(raw, plan):
    positions = {p['container_id']: p['slot'] for p in raw['onboard']['placements']
                 if p['container_id'] not in set(raw['discharge_list'])}
    for move in plan['moves']:
        if move['kind'] in ('load', 'restow_on', 'restow_shift'):
            positions[move['container_id']] = move['dst']
        elif move['kind'] in ('discharge', 'restow_off'):
            positions.pop(move['container_id'], None)
    return positions


def placement_changes(raw, before, after, matching=()):
    left, right = _final_positions(raw, before), _final_positions(raw, after)
    changed = sorted(box for box in raw['load_list'] if left.get(box) != right.get(box))
    matched = sorted(box for box in matching if left.get(box) != right.get(box))
    bay_changes = sorted(box for box in matched if box in left and box in right
                         and parse_slot(left[box]).bay != parse_slot(right[box]).bay)
    signature = lambda p: digest(json.dumps(p['moves'], sort_keys=True, separators=(',', ':')).encode())
    return {'changed_loaded_box_placements': len(changed), 'changed_loaded_boxes': changed,
            'changed_matched_dg_box_placements': len(matched), 'changed_matched_dg_boxes': matched,
            'changed_matched_dg_box_bays': len(bay_changes),
            'before_plan_signature': signature(before), 'after_plan_signature': signature(after),
            'container_plan_changed': signature(before) != signature(after)}


def generated_constraints(raw):
    """Plain one-bay DG, bay closures and strict physical deck caps only.

    Additional exact rules with recorded after plans are joined separately.
    This deliberately bounded universe follows the frozen sentence priorities.
    """
    context = context_from_instance(raw)
    instance = _selection_instance(raw)
    active = set(instance.load_list) | {p.container_id for p in instance.onboard.placements
                                       if p.container_id not in set(instance.discharge_list)}
    classes = sorted({c.rstrip('ABCDEFGHJKLNS') for box in instance.containers if box.id in active
                      for c in _dg_classes(box)} & IMDG_CLASSES)
    for dg in classes:
        for bay in context['bays']:
            yield {'type': 'dg_keep_away', 'imdg_class': dg, 'min_bays': 1,
                   'away_from': {'kind': 'bay', 'bay': bay}}
    for bay in context['bays']:
        yield {'type': 'close_bay', 'bay': bay}
        for limit in range(context['max_tiers_by_bay'][str(bay)]['deck']):
            yield {'type': 'max_tiers', 'bay': bay, 'zone': 'deck', 'max_tiers': limit}


def remaining_capacity(raw, expected):
    instance = _selection_instance(raw)
    compiled = apply(instance, validate_answer(json.dumps(expected), context_from_instance(raw)))
    counts = [sum(compiled.forbids(box.id, slot) is None for slot in physical_slots_for_box(instance, box))
              for box in instance.containers if box.id in set(instance.load_list)]
    return {'minimum_allowed_footprints_per_load_box': min(counts, default=0),
            'sum_allowed_footprints': sum(counts),
            'note': 'per-box footprint counts can overlap; not a joint capacity or feasibility certificate'}


def ranking(candidate):
    after = candidate.get('preferred_after')
    constraint = candidate['expected']['constraint']
    category = (0 if constraint['type'] == 'dg_keep_away' and constraint['imdg_class'] == '8' else
                1 if constraint['type'] == 'dg_keep_away' else 2 if constraint['type'] == 'close_bay' else 3)
    reuse = 0 if after and after['recorded_audited'] else 1 if after else 2
    delta = after['move_delta'] if after and after['move_delta'] is not None else float('inf')
    capacity = candidate['remaining_static_footprint_capacity']
    cost_or_capacity = delta if after else -capacity['sum_allowed_footprints']
    return (reuse, category, cost_or_capacity,
            -after['changed_matched_dg_box_placements'] if after else 0,
            -after['changed_loaded_box_placements'] if after else 0,
            candidate['n_load'], candidate['input'], canonical(candidate['expected']))


def build_inventory(*, root=ROOT):
    root = Path(root).resolve()
    here = root/'experiments/mvp/reader/hero'
    sources, exclusions = {}, Counter()

    def read(path):
        path = Path(path)
        absolute = (root/path).resolve() if not path.is_absolute() else path.resolve()
        relative = absolute.relative_to(root)
        if set(relative.parts) & FORBIDDEN:
            raise ValueError('Forbidden held-out path; no file opened')
        payload = absolute.read_bytes()
        sources[str(relative)] = digest(payload)
        return json.loads(payload)

    criteria_path = here/'CRITERIA.json'
    criteria = read(criteria_path)
    frozen = read(here/'criteria-freeze.json')
    criteria_hash = sources[str(criteria_path.relative_to(root))]
    if criteria_hash != frozen['criteria_sha256'] or criteria.get('schema_version') != 'hero-criteria/1':
        raise ValueError('Hero criteria do not match the pre-inventory freeze')
    maps = {}
    before_count = 0
    for run in BEFORE_RUNS:
        for path in sorted((root/'experiments/mvp/frontier/runs'/run).glob('*.json')):
            if any(part in path.stem for part in ('test-v3', 'test_v3')):
                exclusions['forbidden_filename'] += 1
                continue
            row = read(path)
            if not isinstance(row, dict) or not row.get('input'):
                continue
            input_path = Path(row['input'])
            absolute = (root/input_path).resolve() if not input_path.is_absolute() else input_path.resolve()
            relative = absolute.relative_to(root)
            if set(relative.parts) & FORBIDDEN or not set(relative.parts) & {'val', 'train'}:
                exclusions['forbidden_or_unknown_split'] += 1
                continue
            if not row.get('passed') or not row.get('plan'):
                exclusions['stored_baseline_not_passed'] += 1
                continue
            input_name = str(relative)
            if input_name not in maps:
                raw = read(input_name)
                maps[input_name] = {'raw': raw, 'baselines': [], 'rules': {}}
            if row.get('input_sha256') and row['input_sha256'] != sources[input_name]:
                raise ValueError('Stored baseline input hash mismatch')
            maps[input_name]['baselines'].append((str(path.relative_to(root)), row))
            before_count += 1

    archived = {}
    for run in READER_RUNS:
        manifest_path = (root/'experiments/mvp/reader/binding-cases.json' if run == 'binding' else
                         root/'experiments/mvp/reader/runs'/run/'cohort.json')
        if not manifest_path.exists():
            continue
        manifest = read(manifest_path)
        for case in manifest['cases']:
            input_name = str(Path(case['input']))
            if input_name not in maps or case.get('type') not in ('dg_keep_away', 'close_bay', 'max_tiers'):
                continue
            expected = case['expected']
            key = (input_name, canonical(expected))
            maps[input_name]['rules'][key[1]] = expected
            plan_file = root/'experiments/mvp/reader/runs'/run/'plans'/f'{case["case_id"]}.json'
            if not plan_file.exists():
                continue
            record = read(plan_file)
            if not record.get('passed') or not record.get('plan') or record.get('repaired') is not False:
                exclusions['archived_after_not_unrepaired_pass'] += 1
                continue
            if run == 'binding':
                parsed = record.get('parsed')
                parser = record.get('parser', {})
            else:
                voyage = [s for s in record.get('evidence', {}).get('sentences', [])
                          if s.get('role') == 'voyage' and s.get('binds')]
                if len(voyage) != 1:
                    exclusions['archive_not_exact_single_hard_rule'] += 1
                    continue
                parsed, parser = voyage[0].get('parsed'), voyage[0].get('parser', {})
            try:
                exact = canonical(parsed) == key[1]
            except (TypeError, ValueError):
                exact = False
            if not exact:
                exclusions['archive_parse_not_exact'] += 1
                continue
            audit_file = plan_file.parent.parent/'audits'/plan_file.name
            audit = read(audit_file) if audit_file.exists() else None
            plan_name = str(plan_file.relative_to(root))
            if audit and audit.get('plan_record_sha256', audit.get('plan_sha256')) not in (None, sources[plan_name]):
                exclusions['archive_audit_hash_mismatch'] += 1
                audit = None
            parser_file = plan_file.parent.parent/'parser'/plan_file.name
            parser_name = str(parser_file.relative_to(root)) if parser_file.exists() else None
            if parser_name:
                read(parser_file)
                if record.get('parser_sha256') not in (None, sources[parser_name]):
                    exclusions['archive_parser_hash_mismatch'] += 1
                    continue
            archived.setdefault(key, []).append({'run': run, 'case': case, 'record': record,
                'plan_file': plan_name, 'audit_file': str(audit_file.relative_to(root)) if audit_file.exists() else None,
                'audit': audit, 'parser_file': parser_name, 'parser': parser})

    candidates = []
    for input_name, inventory in sorted(maps.items()):
        raw, rules = inventory['raw'], inventory['rules']
        context = context_from_instance(raw)
        for constraint in generated_constraints(raw):
            expected = {'schema_version': SCHEMA_VERSION, 'constraint': constraint}
            rules[canonical(expected)] = expected
        instance = _selection_instance(raw)
        active = set(instance.load_list) | {p.container_id for p in instance.onboard.placements
                                           if p.container_id not in set(instance.discharge_list)}
        for rule_key, expected in sorted(rules.items()):
            constraint = expected['constraint']
            matching = sorted(box.id for box in instance.containers if box.id in active and
                              constraint['type'] == 'dg_keep_away' and
                              _class_matches(constraint['imdg_class'], _dg_classes(box)))
            if constraint['type'] == 'dg_keep_away' and not matching:
                exclusions['vacuous_dg_rule'] += 1
                continue
            screen = static_screen(raw, expected)
            if screen['proven_impossible']:
                exclusions['statically_proven_impossible_rule'] += 1
                continue
            violations_by_before = [(path, row, baseline_violations(raw, row['plan'], expected))
                                    for path, row in inventory['baselines']]
            violating = [(path, row, errors) for path, row, errors in violations_by_before if errors]
            if not violating:
                exclusions['stored_baselines_already_obey'] += 1
                continue
            after_options = []
            for archive in archived.get((input_name, rule_key), []):
                record, audit = archive['record'], archive['audit']
                before = next((b for b in violating if b[0] == archive['case']['baseline_file']), None)
                if before is None:
                    continue
                if record.get('baseline_sha256') not in (None, sources[before[0]]):
                    exclusions['archive_baseline_hash_mismatch'] += 1
                    continue
                if record['plan'].get('instance_id') != raw['id']:
                    exclusions['archive_instance_mismatch'] += 1
                    continue
                if baseline_violations(raw, record['plan'], expected):
                    exclusions['archived_after_violates_intended_rule'] += 1
                    continue
                changes = placement_changes(raw, before[1]['plan'], record['plan'], matching)
                if not changes['container_plan_changed'] or not changes['changed_loaded_box_placements']:
                    exclusions['archive_no_changed_loaded_placement'] += 1
                    continue
                linked = bool(audit and audit.get('plan_record_sha256', audit.get('plan_sha256')) == sources[archive['plan_file']])
                fresh = bool(audit and (audit.get('fresh_process') or
                             (audit.get('audit_pid') is not None and audit.get('audit_pid') != record.get('planner_pid'))))
                audited = bool(audit and linked and fresh and audit.get('passed')
                               and audit.get('repaired', False) is False
                               and audit.get('checker_passed', audit.get('full_checker_passed', False))
                               and audit.get('binding', audit.get('compliant_pass', False))
                               and not audit.get('instruction_violations') and not audit.get('parsed_violations'))
                moves = record.get('constrained_moves')
                cost_source = 'stored constrained_moves'
                if moves is None and audit:
                    moves = audit.get('constrained_moves', audit.get('moves'))
                    cost_source = 'stored audit constrained_moves/moves'
                if moves is None:
                    tries = record.get('evidence', {}).get('planner', {}).get('tries', [])
                    costs = [t['weighted'] for t in tries if t.get('passed') and t.get('instruction_passed') and 'weighted' in t]
                    moves = min(costs) if costs else None
                    cost_source = 'minimum stored passed instruction-aware finishing try; fresh cost verification pending'
                parser = archive['parser']
                option = {'plan_file': archive['plan_file'], 'plan_record_sha256': sources[archive['plan_file']],
                          'baseline_file': before[0], 'baseline_sha256': sources[before[0]],
                          'audit_file': archive['audit_file'],
                          'audit_sha256': sources.get(archive['audit_file']),
                          'parser_file': archive['parser_file'], 'parser_sha256': sources.get(archive['parser_file']),
                          'case_id': archive['case']['case_id'], 'source_run': archive['run'],
                          'archived_sentence': archive['case']['sentence'], 'recorded_passed': True,
                          'recorded_audited': audited, 'parsed_exact': True,
                          'parser_provenance': {k: parser[k] for k in ('parser', 'model', 'adapter_sha256', 'request_id', 'prompt_sha256') if k in parser},
                          'constrained_moves': moves, 'moves_source': cost_source,
                          'move_delta': moves - before[1]['weighted'] if moves is not None else None,
                          'wall_s': record.get('wall_s'), 'parse_wall_s': record.get('parse_wall_s'),
                          'pipeline_wall_s': record.get('pipeline_wall_s'),
                          'audit_wall_s': audit.get('wall_s', audit.get('audit_wall_s')) if audit else None,
                          'historical_only': True, **changes}
                after_options.append(option)
            after_options.sort(key=lambda a: (not a['recorded_audited'],
                a['move_delta'] if a['move_delta'] is not None else float('inf'),
                -a['changed_matched_dg_box_placements'], -a['changed_loaded_box_placements'], a['plan_file']))
            preferred = after_options[0] if after_options else None
            before = next((b for b in violating if preferred and b[0] == preferred['baseline_file']), violating[0])
            identity = f'{input_name}:{rule_key}'
            candidate = {'candidate_id': 'hero-' + digest(identity.encode())[:16],
                         'input': input_name, 'input_sha256': sources[input_name], 'instance_id': raw['id'],
                         'baseline_file': before[0], 'baseline_sha256': sources[before[0]],
                         'before_file': before[0], 'after_file': preferred['plan_file'] if preferred else None,
                         'expected': expected, 'context': context, 'sentence': sentence_for(constraint),
                         'n_load': len(raw['load_list']), 'matching_active_cargo': matching,
                         'baseline_violations': before[2], 'baseline_weighted': before[1]['weighted'],
                         'baseline_passed': True, 'static_screen': screen,
                         'remaining_static_footprint_capacity': remaining_capacity(raw, expected),
                         'preferred_after': preferred, 'recorded_after_options': after_options,
                         'new_plain_sentence_parse_pending': True, 'fresh_hero_audit_pending': True,
                         'eligible_for_new_read_and_audit': bool(preferred),
                         'selection_is_blind': False}
            candidates.append(candidate)
    candidates.sort(key=ranking)
    for index, candidate in enumerate(candidates):
        candidate['inventory_rank'] = index + 1
    proposed = [c['candidate_id'] for c in candidates[:criteria['max_shortlist']]]
    return {'schema_version': 'hero-inventory/1', 'criteria_file': str(criteria_path.relative_to(root)),
            'criteria_sha256': criteria_hash, 'selection_is_blind': False,
            'purpose': criteria['purpose'], 'shortlist_frozen': False,
            'proposed_shortlist_candidate_ids': proposed,
            'baseline_run_dirs': list(BEFORE_RUNS), 'recorded_after_run_dirs': list(READER_RUNS),
            'split_scope': 'Only validation/train inputs referenced by the named historical validation run dirs; no train run directory was assumed or swept.',
            'generated_rule_scope': 'Real active DG classes one bay from every context bay; every context bay closure; strict deck tier caps in each bay; plus exact archived rules.',
            'mandatory_gate_status': 'Historical claims and pure placement/static predicates only. New wording exact Spark read, provenance and fresh process full checker/replay/mask audit remain pending.',
            'no_new_model_planner_checker_or_replay_calls': True,
            'stored_before_plan_count': before_count, 'unique_input_count': len(maps),
            'candidate_count': len(candidates),
            'same_rule_recorded_after_candidate_count': sum(bool(c['preferred_after']) for c in candidates),
            'same_rule_recorded_audited_candidate_count': sum(bool(c['preferred_after'] and c['preferred_after']['recorded_audited']) for c in candidates),
            'exclusions': dict(exclusions), 'sources': dict(sorted(sources.items())), 'candidates': candidates}


def write_inventory(output_path=None, *, root=ROOT):
    root = Path(root).resolve()
    output_path = Path(output_path) if output_path is not None else root/'experiments/mvp/reader/hero/inventory.json'
    if output_path.exists():
        raise FileExistsError('Inventory already exists; root review required before replacing it')
    value = build_inventory(root=root)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    # Exclusive creation protects a completed review artifact from concurrent runs.
    with output_path.open('x') as stream:
        stream.write(json.dumps(value, indent=2, sort_keys=True)+'\n')
    return value


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, default=HERE/'inventory.json')
    args = parser.parse_args()
    value = write_inventory(args.output)
    print(json.dumps({k: value[k] for k in ('candidate_count', 'same_rule_recorded_after_candidate_count',
                     'same_rule_recorded_audited_candidate_count', 'proposed_shortlist_candidate_ids')}))


if __name__ == '__main__':
    main()
