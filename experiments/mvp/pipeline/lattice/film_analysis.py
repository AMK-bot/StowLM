"""Authenticated recorded-plan diversity and final-slot displacement; no gates."""
from __future__ import annotations

import argparse
from collections import defaultdict
from contextlib import ExitStack
from datetime import datetime, timezone
import fcntl
import hashlib
import json
from pathlib import Path

from experiments.mvp.pipeline.contract.schema import sha256_json
from .acceptance import recorded_gate_pass
from .store import _atomic, canonical_rule_set, entry_key

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[3]
ATTRIBUTION = ('Final slots are compared with the frozen baseline, not with loading moves. '
               'Pointwise forbidden baseline slots identify direct rule displacement; other changes '
               'may reflect search and coupled constraints. This is not a minimal-change certificate.')


def _inside(path):
    path = Path(path).resolve()
    if not path.is_relative_to(HERE.resolve()):
        raise ValueError('film analysis paths must remain inside lattice')
    return path


def authenticated_snapshot(path, *, expected_sha256=None):
    """Capture exact catalogue-bound row bytes under the producer's shared lock."""
    path = _inside(path)
    if not path.exists():
        return dict(path=str(path), present=False, catalogue_sha256=None, rows=[])
    if not (path/'catalogue.json').exists() and not (path/'.catalogue.lock').exists():
        return dict(path=str(path), present=True, catalogue_sha256=None, rows=[])
    with ExitStack() as stack:
        lock = path/'.catalogue.lock'
        if lock.exists():
            stream = stack.enter_context(lock.open('r'))
            fcntl.flock(stream, fcntl.LOCK_SH)
        raw = (path/'catalogue.json').read_bytes()
        pin = hashlib.sha256(raw).hexdigest()
        if expected_sha256 is not None and pin != expected_sha256:
            raise ValueError('analysis catalogue differs from its independently pinned digest')
        catalogue = json.loads(raw)
        rows = []
        for key, digest in sorted(catalogue.items()):
            if not isinstance(key, str) or len(key) != 64 or any(c not in '0123456789abcdef' for c in key):
                raise ValueError('malformed catalogue key')
            source = path/(key+'.json')
            content = source.read_bytes()
            if hashlib.sha256(content).hexdigest() != digest:
                raise ValueError('analysis entry failed byte authentication: '+key)
            row = json.loads(content)
            if entry_key(row['instance_sha256'], row['canonical_rules']) != key:
                raise ValueError('analysis entry identity differs from its catalogue key')
            rows.append(dict(key=key, path=str(source), raw_sha256=digest, record=row))
        if (path/'catalogue.json').read_bytes() != raw:
            raise ValueError('analysis catalogue changed while its snapshot was read')
    return dict(path=str(path), present=True, catalogue_sha256=pin, rows=rows)


def move_content(plan):
    """Keep operational cargo/cover IDs; discard only annotation IDs/metadata."""
    annotations = {'id', 'metadata', 'provenance', 'created_at', 'created_utc', 'label'}
    return [{key: value for key, value in move.items() if key not in annotations} for move in plan['moves']]


def final_positions(instance, plan):
    from stowage.model import MovePlan
    from stowage.moves import apply_plan
    replay = apply_plan(instance, MovePlan.model_validate(plan))
    if not replay.legal:
        raise ValueError('recorded plan cannot be legally replayed for final placement analysis')
    return {p.container_id: p.slot for p in replay.final_state.ship.placements}


def _occupied(instance, positions):
    from stowage.conventions import cells_of_slot, parse_slot
    cells = {cell.id: cell for cell in instance.vessel.cells}
    occupied = defaultdict(set)
    for slot in positions.values():
        for cid in cells_of_slot(slot):
            occupied[cells[cid].stack_id].add(parse_slot(cid).tier)
    return occupied


def _hatch_bound(instance, positions, *, removed=()):
    from stowage.conventions import cells_of_slot
    stacks = {stack.id: stack for stack in instance.vessel.stacks}
    cells = {cell.id: cell for cell in instance.vessel.cells}
    boxes = {box.id: box for box in instance.containers}
    listed = {sid: cover.id for cover in instance.vessel.hatch_covers
              for sid in cover.hold_stack_ids + cover.deck_stack_ids}
    ports, memberships = defaultdict(lambda: defaultdict(set)), defaultdict(set)
    for bid, slot in positions.items():
        if bid in removed:
            continue
        for cid in cells_of_slot(slot):
            stack = stacks[cells[cid].stack_id]
            cover = stack.hatch_cover_id or listed.get(stack.id)
            if cover:
                ports[cover][str(boxes[bid].pod)].add(bid)
                memberships[bid].add(cover)
    rows = []
    for cover, groups in sorted(ports.items()):
        counts = {pod: len(ids) for pod, ids in groups.items()}
        total = sum(counts.values())
        rows.append(dict(cover_id=cover, pod_counts=counts, boxes=total,
                         minimum_removals=total-max(counts.values(), default=0)))
    # A 40-ft footprint can touch two covers. Never count that box twice in
    # the global lower bound; the per-cover minima retain their local meaning.
    total = sum(row['minimum_removals'] for row in rows)
    overlap = max((len(covers) for covers in memberships.values()), default=1)
    bound = max(max((row['minimum_removals'] for row in rows), default=0), (total+overlap-1)//overlap)
    return dict(covers=rows, minimum_moved_boxes_lower_bound=bound,
                identities_determined=False, forced_ids=None,
                overlap_policy='max(per-cover minimum, ceiling(sum minima / maximum covers touched per box))')


def displacement(instance, base_positions, positions, rules):
    """Rule attribution on exact old footprints, with ambiguous hatch lower bounds."""
    from experiments.mvp.reader.schema import context_from_instance, validate_answer
    from experiments.mvp.reader.occupancy import apply_reader_constraint
    from stowage.conventions import odd_bays_of
    canonical = canonical_rule_set(rules)
    context = context_from_instance(instance)
    moved = {bid for bid in set(base_positions)|set(positions) if base_positions.get(bid) != positions.get(bid)}
    pointwise, rows, has_hatch = set(), [], False
    before_occupied, after_occupied = _occupied(instance, base_positions), _occupied(instance, positions)
    for envelope in canonical:
        answer = validate_answer(json.dumps(envelope), context)
        if answer.type == 'one_port_per_hatch':
            has_hatch = True
            rows.append(dict(envelope=envelope, kind='hatch_lower_bound', **_hatch_bound(instance, base_positions)))
            continue
        compiled = apply_reader_constraint(instance, answer)
        forbidden = {bid for bid, slot in base_positions.items() if compiled.forbids(bid, slot) is not None}
        pointwise.update(forbidden)
        details = dict(envelope=envelope, kind='pointwise_footprint', forbidden_baseline_ids=sorted(forbidden),
                       forced_moved_ids=sorted(forbidden & moved), forced_moved_count=len(forbidden & moved))
        if answer.type in ('max_tiers', 'close_bay'):
            bays = set(odd_bays_of(answer.bay)) if answer.bay is not None else None
            stacks = [stack for stack in instance.vessel.stacks
                      if (answer.type == 'close_bay' or stack.on_deck == (answer.zone == 'deck'))
                      and (bays is None or stack.bay in bays)]
            details['target_stacks'] = [dict(stack_id=stack.id, cap=getattr(answer, 'max_tiers', None),
                baseline_occupied_tiers=sorted(before_occupied[stack.id]), plan_occupied_tiers=sorted(after_occupied[stack.id]),
                baseline_count=len(before_occupied[stack.id]), plan_count=len(after_occupied[stack.id]))
                for stack in stacks if before_occupied[stack.id] or after_occupied[stack.id]]
            details['tier_semantics'] = ('Forced IDs use the actual reader footprint predicate. Occupied-tier '
                'counts use distinct physical cell tiers per stack, including both halves of long boxes; '
                'violation strings and load/final mentions are not counted as separate boxes.')
        rows.append(details)
    forced = pointwise & moved
    residual = _hatch_bound(instance, base_positions, removed=forced) if has_hatch else None
    lower = len(forced) + (residual['minimum_moved_boxes_lower_bound'] if residual else 0)
    if lower > len(moved):
        raise ValueError('observed displacement is smaller than the instruction lower bound')
    return dict(moved_count=len(moved), moved_ids=sorted(moved), pointwise_forced_ids=sorted(forced),
                forced_ids=None if has_hatch else sorted(forced),
                forced_count=None if has_hatch else len(forced), forced_minimum_count=lower,
                additional_moved_ids=None if has_hatch else sorted(moved-forced),
                additional_moved_count=None if has_hatch else len(moved-forced),
                additional_moved_count_upper_bound=len(moved)-lower if has_hatch else len(moved-forced),
                hatch_after_pointwise_removals=residual, rules=rows, attribution=ATTRIBUTION)


def analyze(*, store_dirs=None, expected_catalogue_sha256=None, base_paths=None):
    """Read authenticated stores and frozen bases; never computes a checker/gauge."""
    from stowage.model import Instance
    from .sequence_provenance import frame_provenance
    directories = store_dirs or [HERE/'store', HERE/'backup-campaign/store']
    expected = expected_catalogue_sha256 or {}
    snapshots = [authenticated_snapshot(path, expected_sha256=expected.get(str(Path(path).resolve()))) for path in directories]
    checked = [source for snapshot in snapshots for source in snapshot['rows'] if source['record'].get('status') == 'checked_plan']
    frozen = {}
    for path in (base_paths or [HERE/'fixtures/hero-base.json', HERE/'fixtures/backup-base.json']):
        path = _inside(path)
        row = json.loads(path.read_bytes())
        candidates = [source for source in checked if source['record']['instance_sha256'] == row['instance_sha256']
                      and not source['record']['canonical_rules'] and sha256_json(source['record']['plan']) == sha256_json(row['plan'])]
        if not candidates:
            raise ValueError('frozen baseline is absent from the authenticated stores: '+str(path))
        frozen[row['instance_sha256']] = row['plan']
    heroes, replay_cache = {}, {}
    for source in checked:
        row = source['record']
        digest = row['instance_sha256']
        if digest != sha256_json(row['instance']) or not recorded_gate_pass(row['instance'], row['plan'], row['canonical_rules'], row.get('gate', {})):
            raise ValueError('recorded checked row has unbound or incomplete gate evidence: '+source['key'])
        if digest not in frozen:
            raise ValueError('no frozen baseline for stored hero '+digest)
        hero = heroes.setdefault(digest, dict(instance_sha256=digest, instance_id=row['instance']['id'],
            boxes=len(row['instance']['load_list']), base_lifts=None, base_final_placement_sha256=None,
            records=[], groups=dict(full_plan_sha256={}, move_content_sha256={}, final_placement_sha256={})))
        instance = Instance.model_validate(row['instance'])
        def positions(plan):
            key = digest, sha256_json(move_content(plan))
            if key not in replay_cache:
                replay_cache[key] = final_positions(instance, plan)
            return replay_cache[key]
        base_positions, after = positions(frozen[digest]), positions(row['plan'])
        hero['base_final_placement_sha256'] = sha256_json(base_positions)
        if not row['canonical_rules']:
            hero['base_lifts'] = row['gate'].get('lifts')
        hashes = dict(full_plan_sha256=sha256_json(row['plan']), move_content_sha256=sha256_json(move_content(row['plan'])),
                      final_placement_sha256=sha256_json(after))
        item = dict(id=row['id'], key=source['key'], source_path=source['path'], source_raw_sha256=source['raw_sha256'],
                    canonical_rules=row['canonical_rules'], sentences=row.get('sentences', []),
                    computed_utc=row.get('computed_utc'), plan_producer=row.get('plan_producer'),
                    frame_provenance=frame_provenance(row),
                    lifts=row['gate'].get('lifts'), recorded_gate_bound=True,
                    recorded_checker_pass=True, recorded_all_frame_pass=True, recorded_frames=row['gate']['gauges']['n_frames'],
                    fresh_native_verification=False, **hashes,
                    displacement=displacement(instance, base_positions, after, row['canonical_rules']))
        hero['records'].append(item)
        for kind, value in hashes.items():
            hero['groups'][kind].setdefault(value, []).append(dict(id=row['id'], key=source['key'], source_path=source['path']))
    for hero in heroes.values():
        instruction_keys = {row['key'] for row in hero['records'] if row['canonical_rules']}
        for row in hero['records']:
            row['group_memberships'] = {kind: hero['groups'][kind][row[kind]] for kind in hero['groups']}
            row['unique_final_placement'] = len(row['group_memberships']['final_placement_sha256']) == 1
            row['unique_among_instruction_placements'] = sum(member['key'] in instruction_keys
                for member in row['group_memberships']['final_placement_sha256']) == 1
            row['differs_from_base'] = row['final_placement_sha256'] != hero['base_final_placement_sha256']
        hero['counts'] = dict(checked_records=len(hero['records']), base_count=sum(not r['canonical_rules'] for r in hero['records']),
                             instructions=sum(bool(r['canonical_rules']) for r in hero['records']), unique_instruction_keys=len(instruction_keys),
                             **{'instruction_'+kind: len({r[kind] for r in hero['records'] if r['canonical_rules']}) for kind in hero['groups']},
                             **{kind: len(groups) for kind, groups in hero['groups'].items()})
    return dict(schema_version='stowlm.lattice-film-analysis/1', computed_utc=datetime.now(timezone.utc).isoformat(),
                analyzer_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(), attribution=ATTRIBUTION,
                verification='authenticated byte snapshots and bound recorded gate verdicts only; no new checker, gauges or search',
                snapshots=[{key: value for key, value in snapshot.items() if key != 'rows'} for snapshot in snapshots], heroes=heroes)


def render_readme_table(analysis):
    """Return a marked table; the caller chooses whether/where to publish it."""
    lines = ['<!-- FILM_ANALYSIS_START -->', '## Film instruction audit', '',
        f"Authenticated checkpoint snapshot at {analysis['computed_utc']}. Full details and source digests: [film-analysis.json](film-analysis.json).",
        'Counts cover the current catalogues, including the isolated 163-box campaign; frozen copies of the same keys are not additional plans. Refusals have no plan and are excluded.', '',
        '| Ship | Stored instruction keys | Distinct instruction plans / crane sequences / final placements | Distinct including base | Base frames and provenance |',
        '|---|---:|---|---:|---|']
    for hero in analysis['heroes'].values():
        counts = hero['counts']
        base = next(row for row in hero['records'] if not row['canonical_rules'])
        distinct = ' / '.join(str(counts['instruction_'+kind]) for kind in
            ('full_plan_sha256', 'move_content_sha256', 'final_placement_sha256'))
        lines.append(f"| {hero['boxes']} boxes | {counts['instructions']} | {distinct} | {counts['full_plan_sha256']} | {base['recorded_frames']} passed; {base['frame_provenance']['label']} |")
    lines += ['', 'Equal lift totals do not imply equal plans. Full hashes cover canonical plan JSON; crane-sequence hashes remove annotation IDs and metadata; placement hashes cover final box-to-slot mappings. Parenthesized group sizes below count matching stored records.', '',
        'Recorded, source-bound gate verdicts only: **crane frames walked on this plan**. No new native sweep was run for this analysis. Directly forced boxes occupied baseline footprints forbidden by the rule; other changed boxes may reflect search and coupled constraints. This is not a minimal-change certificate. Long boxes count once even when two physical stacks shrink.', '',
        '| Hero / instruction | Key | Full / moves / placement groups (all record members) | Unique instruction placement / changed from base | Directly forced / additional changed boxes | Stack occupied tiers before → after | Extra lifts | Frames | Sequence provenance |',
        '|---|---|---|---|---|---|---:|---:|---|']
    for hero in analysis['heroes'].values():
        for row in hero['records']:
            if not row['canonical_rules']:
                continue
            displacement = row['displacement']
            groups = ' / '.join(f'{row[kind][:10]} ({len(row["group_memberships"][kind])})'
                                for kind in ('full_plan_sha256', 'move_content_sha256', 'final_placement_sha256'))
            forced = str(displacement['forced_count']) if displacement['forced_count'] is not None else f'≥{displacement["forced_minimum_count"]} (IDs undetermined)'
            additional = str(displacement['additional_moved_count']) if displacement['additional_moved_count'] is not None else f'≤{displacement["additional_moved_count_upper_bound"]}'
            stacks = '; '.join(f'{stack["stack_id"]}: {stack["baseline_count"]} → {stack["plan_count"]}'
                for rule in displacement['rules'] for stack in rule.get('target_stacks', [])
                if stack['baseline_count'] != stack['plan_count']) or '—'
            label = row['id']+': '+('; '.join(row['sentences']) or json.dumps(row['canonical_rules'], sort_keys=True))
            label = label.replace('|', '\\|').replace('\n', ' ')
            provenance = row['frame_provenance']['label'].replace('|', '\\|')
            distinct = ('yes' if row['unique_among_instruction_placements'] else 'no')+' / '+('yes' if row['differs_from_base'] else 'no')
            lines.append(f'| {hero["boxes"]} boxes / {label} | {row["key"][:12]} | {groups} | {distinct} | {forced} / {additional} | {stacks} | {row["lifts"]} | {row["recorded_frames"]} | {provenance} |')
    lines += ['', 'Refresh once with `python -m experiments.mvp.pipeline.lattice.film_report` using the offline environment below. A separate read-only observer watches accepted catalogue checkpoints and refreshes this section and the candidate JSON; [film-observer/state.json](film-observer/state.json) records its PID and last refresh. It never signals the solver batch, changes a plan, or grants a gate. Demo launch still regates the selected stored plan.', '']
    lines.append('<!-- FILM_ANALYSIS_END -->')
    return '\n'.join(lines)+'\n'


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, default=HERE/'film-analysis.json')
    args = parser.parse_args()
    result = analyze()
    _atomic(_inside(args.output), result)
    print(json.dumps({key: hero['counts'] for key, hero in result['heroes'].items()}))


if __name__ == '__main__':
    main()
