"""Film recommendations from authenticated recorded metrics; no native gates."""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
import json
from pathlib import Path

from experiments.mvp.pipeline.contract.schema import canonical_json, sha256_file, sha256_json

HERE = Path(__file__).resolve().parent


def _spoken_number(value):
    small = ('zero one two three four five six seven eight nine ten eleven twelve '
             'thirteen fourteen fifteen sixteen seventeen eighteen nineteen').split()
    if 0 <= value < 20:
        return small[value]
    tens = {2: 'twenty', 3: 'thirty', 4: 'forty', 5: 'fifty', 6: 'sixty',
            7: 'seventy', 8: 'eighty', 9: 'ninety'}
    if 20 <= value < 100:
        return tens[value // 10] + (' ' + small[value % 10] if value % 10 else '')
    return str(value)


def _rule_wordings(body):
    kind = body['type']
    if kind == 'close_bay':
        bay = _spoken_number(body['bay'])
        return [f"Don't load any containers into bay {bay}.",
                f"Keep bay {bay} out of this container loading plan.",
                f"No container loading in bay {bay}, please."]
    if kind == 'max_tiers':
        scope = 'every bay' if body['bay'] is None else f"bay {_spoken_number(body['bay'])}"
        cap = _spoken_number(body['max_tiers'])
        tier = 'tier' if body['max_tiers'] == 1 else 'tiers'
        zone = body['zone']
        return [f"In {scope}, keep each {zone} stack to {cap} {tier}.",
                f"For {scope}, cap the {zone} stacks at {cap} occupied {tier}.",
                f"Don't stack {zone} cargo more than {cap} {tier} high in {scope}."]
    if kind == 'dg_keep_away':
        cls = ' point '.join(_spoken_number(int(part)) for part in body['imdg_class'].split('.'))
        distance = _spoken_number(body['min_bays'])
        bay = 'bay' if body['min_bays'] == 1 else 'bays'
        anchor = 'the accommodation' if body['away_from']['kind'] == 'accommodation' \
            else f"bay {_spoken_number(body['away_from']['bay'])}"
        return [f"Keep all class {cls} dangerous goods at least {distance} twenty-foot {bay} away from {anchor}.",
                f"Leave at least {distance} twenty-foot {bay} between every class {cls} DG container and {anchor}.",
                f"All class {cls} DG cargo must stay at least {distance} twenty-foot {bay} from {anchor}."]
    if kind == 'one_port_per_hatch':
        return ["Keep each physical hatch cover to a single discharge port, across the hold and deck.",
                "For each physical hatch cover, use one discharge port for its hold and deck cargo together.",
                "Use no more than one discharge port per physical hatch cover, counting both hold and deck cargo."]
    raise ValueError(f'Unsupported film instruction: {kind}')


def spoken_variants(rules):
    """Authored alternatives share targets; reader accuracy is not measured here."""
    canonical = sorted(deepcopy(rules), key=canonical_json)
    wordings = [_rule_wordings(rule['constraint']) for rule in canonical]
    return [dict(utterances=[forms[index] for forms in wordings],
                 canonical_rules=deepcopy(canonical),
                 confirmation_required=True, reader_model_tested=False,
                 provenance='Authored spoken variants with the same reference target; not measured model readings.',
                 delivery='Read and confirm each utterance separately.' if len(canonical) > 1 else 'Single instruction.')
            for index in range(3)]


def _base_documents():
    result = {}
    for name in ('hero-base.json', 'backup-base.json'):
        path = HERE / 'fixtures' / name
        if not path.exists():
            continue
        row = json.loads(path.read_text())
        sha = sha256_json(row['instance'])
        if sha != row['instance_sha256']:
            raise ValueError('Frozen base instance digest mismatch')
        result[sha] = dict(boxes=len(row['instance']['load_list']), lifts=row['lifts'],
                           fixture=str(path), fixture_sha256=sha256_file(path), row=row)
    return result


def _stack_shrinks(record):
    stacks = {}
    for rule in record.get('displacement', {}).get('rules', []):
        for stack in rule.get('target_stacks', []):
            before, after = stack.get('baseline_count'), stack.get('plan_count')
            if isinstance(before, int) and isinstance(after, int) and after < before:
                stacks[stack['stack_id']] = deepcopy(stack)
                stacks[stack['stack_id']]['observed_tier_reduction'] = before-after
                cap = stack.get('cap')
                stacks[stack['stack_id']]['tier_reduction_required_by_cap_at_least'] = max(0, before-cap) if isinstance(cap, int) else None
    return [stacks[key] for key in sorted(stacks)]


def _candidate(record, hero):
    displacement = record.get('displacement') or {}
    base_hashes = {r['final_placement_sha256'] for r in hero.get('records', [])
                   if not r.get('canonical_rules') and r.get('final_placement_sha256')}
    placement = record.get('final_placement_sha256')
    group = hero.get('groups', {}).get('final_placement_sha256', {}).get(placement, [])
    differs = bool(base_hashes) and bool(placement) and placement not in base_hashes and record.get('differs_from_base') is not False
    unique = len(group) == 1 and bool(placement) and record.get('unique_final_placement') is not False
    forced = displacement.get('forced_count')
    shrinks = _stack_shrinks(record)
    conditions = dict(recorded_gate_bound=record.get('recorded_gate_bound') is True,
        recorded_full_checker_pass=record.get('recorded_checker_pass') is True,
        recorded_all_frames_pass=record.get('recorded_all_frame_pass') is True,
        at_least_two_pointwise_forced_boxes=isinstance(forced, int) and not isinstance(forced, bool) and forced >= 2,
        actual_target_stack_shrink=bool(shrinks),
        distinct_from_frozen_base=differs is True,
        unique_final_placement_among_stored_records=unique is True)
    return dict(id=record['id'], key=record.get('key'), canonical_rules=deepcopy(record['canonical_rules']),
        source_path=record.get('source_path'), forced_boxes=forced,
        sentences=deepcopy(record.get('sentences', [])), lifts=record.get('lifts'),
        forced_ids=displacement.get('forced_ids'), moved_boxes=displacement.get('moved_count'),
        additional_changed_boxes=displacement.get('additional_moved_count'), target_stack_shrinks=shrinks,
        final_placement_sha256=placement, full_plan_sha256=record.get('full_plan_sha256'),
        move_content_sha256=record.get('move_content_sha256'), placement_group=deepcopy(group),
        frames_checked=record.get('recorded_frames'), eligibility=conditions,
        frame_provenance=deepcopy(record.get('frame_provenance')),
        eligible=all(conditions.values()), spoken_variants=spoken_variants(record['canonical_rules']),
        attribution='Pointwise forced means the old slot is forbidden by the instruction. Other changed slots may reflect search or coupled constraints; they are not certified avoidable or individually caused by this rule.',
        gate_scope='Authenticated recorded gates, not a fresh native sweep; demo launch still requires regating.')


def _pending_backup(base):
    """Compare queued max-tier requests on the frozen base using public replay only."""
    from stowage.model import Instance, MovePlan
    from stowage.moves import apply_plan
    from stowage.conventions import cells_of_slot, parse_slot
    from llm.instructions.apply import apply as constrain
    from experiments.mvp.reader.schema import context_from_instance, validate_answer
    instructions = HERE / 'backup-campaign/instructions.json'
    audit_path = HERE / 'backup-campaign/selection-audit.json'
    if not instructions.exists() or not audit_path.exists():
        return None
    selection, audit = json.loads(instructions.read_text()), json.loads(audit_path.read_text())
    if audit['instructions_sha256'] != sha256_file(instructions) or \
            selection['base_fixture_sha256'] != base['fixture_sha256']:
        raise ValueError('Pending backup selection is not bound to the frozen base')
    row = base['row']
    instance, plan = Instance.model_validate(row['instance']), MovePlan.model_validate(row['plan'])
    context = context_from_instance(instance)
    replay = apply_plan(instance, plan)
    if not replay.legal:
        raise ValueError('Pending backup base replay is illegal')
    cells = {c.id: c for c in instance.vessel.cells}
    occupied = {cell for p in replay.final_state.ship.placements for cell in cells_of_slot(p.slot)}
    candidates = []
    for spec in selection['instructions']:
        if len(spec['rules']) != 1 or spec['rules'][0]['constraint']['type'] != 'max_tiers':
            continue
        body = spec['rules'][0]['constraint']
        constrained = constrain(instance, validate_answer(canonical_json(spec['rules'][0]), context))
        forced = [p for p in replay.final_state.ship.placements if constrained.forbids(p.container_id, p.slot)]
        targets = {cells[cell].stack_id for cell in constrained._all_boxes}
        stacks = []
        for stack_id in sorted(targets):
            tiers = sorted({parse_slot(cell).tier for cell in occupied if cell in cells and cells[cell].stack_id == stack_id})
            if len(tiers) > body['max_tiers']:
                stacks.append(dict(stack_id=stack_id, baseline_occupied_tiers=tiers,
                    baseline_count=len(tiers), required_cap=body['max_tiers'], plan_count=None,
                    required_tier_reduction_at_least=len(tiers)-body['max_tiers']))
        candidates.append(dict(id=spec['id'], canonical_rules=spec['rules'],
            forced_boxes=len(forced), forced_ids=sorted(p.container_id for p in forced),
            actual_baseline_placements=[dict(container_id=p.container_id, slot=p.slot) for p in forced],
            target_stacks_requiring_shrink=stacks, spoken_variants=spoken_variants(spec['rules']),
            eligible=False, eligibility='pending_full_instruction_result_and_every_frame_gate',
            recorded_all_frames_pass=False, actual_after_stack_counts=None,
            selection_source=str(instructions),
            reason='Strongest queued max-tier candidate by forced boxes and required target-stack reduction; ties use request ID. No qualifying stored option is claimed.'))
    candidates.sort(key=lambda r: (-r['forced_boxes'], -len(r['target_stacks_requiring_shrink']),
        -sum(s['required_tier_reduction_at_least'] for s in r['target_stacks_requiring_shrink']), r['id']))
    return candidates[0] if candidates else None


def build_candidates(analysis):
    bases = _base_documents()
    heroes = {}
    for sha, hero in analysis['heroes'].items():
        base = bases.get(sha, {})
        rows = [_candidate(record, hero) for record in hero.get('records', []) if record.get('canonical_rules')]
        eligible = [row for row in rows if row['eligible']]
        eligible.sort(key=lambda r: (-r['forced_boxes'], -len(r['target_stack_shrinks']),
                                     r['additional_changed_boxes'] if r['additional_changed_boxes'] is not None else 10**9, r['id']))
        tier_options = [row for row in eligible if len(row['canonical_rules']) == 1 and
                        row['canonical_rules'][0]['constraint']['type'] == 'max_tiers']
        pending = _pending_backup(base) if not eligible and base.get('boxes') == 163 else None
        heroes[sha] = dict(instance_sha256=sha, instance_id=hero.get('instance_id'), boxes=base.get('boxes'),
            base_lifts=base.get('lifts'), compared_counts=hero.get('counts'),
            recommendation=eligible[0] if eligible else None,
            recommendation_reason='Most pointwise forced boxes among stored, source-bound all-frame passes with actual stack shrink and a unique final placement.' if eligible else 'No stored option meets every film eligibility condition in this snapshot.',
            stack_focused_alternative=tier_options[0] if tier_options and (not eligible or tier_options[0]['key'] != eligible[0]['key']) else None,
            pending_candidate=pending, compared_candidates=rows)
    return dict(schema_version='stowlm.hero-instruction-candidates/1', computed_utc=datetime.now(timezone.utc).isoformat(),
        analysis_computed_utc=analysis.get('computed_utc'), analysis_sha256=sha256_json(analysis), heroes=heroes,
        method='Rank actual pointwise displacement first, then visible target-stack shrink. Require a recorded full gate, all-frame pass, at least two forced boxes, a changed final placement and no shared final-placement group.',
        spoken_wording_caveat='Three authored alternatives share each exact canonical target. Their reader-model outputs have not been measured; confirm each reading.',
        production_caveat="It replans from scratch; keeping changes small isn't built yet. Sequence postprocessing and stored provenance must retain their own labels.")


def write_candidates(analysis, destination):
    result = build_candidates(analysis)
    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_suffix('.tmp')
    temporary.write_text(json.dumps(result, indent=2, allow_nan=False) + '\n')
    temporary.replace(destination)
    return result


def render_candidates_table(candidates):
    lines = ['### Film instruction candidates', '',
             'Recorded, authenticated comparison: [candidate details and spoken variants](hero-instruction-candidates.json). These are recorded gates, not a fresh native sweep. Three authored wordings share each canonical target; reader-model accuracy is unmeasured and every reading needs confirmation.', '',
             '| Voyage | Recommendation | Forced / other changed boxes | Evidence |',
             '| --- | --- | --- | --- |']
    spoken = []
    for hero in candidates['heroes'].values():
        choice = hero['recommendation']
        if choice:
            provenance = choice.get('frame_provenance') or {}
            label = provenance.get('label', 'sequence provenance unavailable')
            baseline = (provenance.get('base_sequence') or {}).get('label', 'baseline provenance unavailable')
            rule = ' '.join(choice['spoken_variants'][0]['utterances'])
            lines.append(f"| {hero.get('boxes')} boxes | {choice['id']}: {rule} | {choice['forced_boxes']} / {choice['additional_changed_boxes']} | {choice['frames_checked']} recorded frames; unique placement; {len(choice['target_stack_shrinks'])} target stacks shrink; selected: {label}; baseline: {baseline} |")
            spoken.append((f"{hero.get('boxes')}-box choice: {choice['id']}", choice))
            alternative = hero.get('stack_focused_alternative')
            if alternative:
                spoken.append((f"{hero.get('boxes')}-box stack-focused alternative: {alternative['id']}", alternative))
        else:
            pending = hero.get('pending_candidate')
            label = f"Pending: {pending['id']}" if pending else 'No qualifying stored option'
            lines.append(f"| {hero.get('boxes')} boxes | {label} | {pending['forced_boxes'] if pending else '—'} / unknown | No qualifying stored instruction result; after-state and gates remain pending |")
            if pending:
                spoken.append((f"{hero.get('boxes')}-box pending candidate: {pending['id']}", pending))
    for title, choice in spoken:
        lines.extend(['', f'**{title}.**'])
        for variant in choice['spoken_variants']:
            lines.append('\n- ' + ' '.join(variant['utterances']))
        if choice.get('frame_provenance'):
            provenance = choice['frame_provenance']
            lines.extend(['', f"Recorded selected-plan sequence: {provenance.get('label')}; baseline: {(provenance.get('base_sequence') or {}).get('label')}. Crane frames walked on this plan; retain the stored producer credit."])
    lines.extend(['', 'Pointwise forced boxes cannot retain their baseline slots under the rule. Other changes may reflect search or coupled constraints; this is not a minimal-change certificate. Physical stack counts include both halves of long containers. An observed tier drop beyond the cap requirement is not wholly attributed to that cap.', '',
                  "It replans from scratch; keeping changes small isn't built yet."])
    return '\n'.join(lines)
