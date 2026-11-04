"""Compare two checked plans on exactly the same starting world.

Move-count deltas describe observed plans, not an optimality proof. Matching a
multiset (a collection retaining repetitions) of lift types and actors identifies
extra lifts without mistaking a different relocation destination for a saved
lift. The source of counts and legality is stowage.moves / checker.full; see
docs/brief.md's demo storyboard and the D11 definition in MoveCount.
"""
from __future__ import annotations

from collections import Counter
import json
from typing import Any


COUNT_LABELS = (
    ('loads', 'Loads'), ('discharges', 'Discharges'),
    ('relocations', 'Yard rehandles'), ('restow_lifts', 'Restow lifts now'),
    ('hatch_cover_lifts', 'Hatch-cover lifts now'),
    ('future_restow_lifts', 'Projected later-port restows'),
    ('future_hatch_cover_lifts', 'Projected later-port hatch lifts'),
    ('future_total_lifts', 'Projected later-port lifts (restows + hatch lifts)'),
    ('total_lifts_now', 'Total lifts now'),
    ('total_lifts_with_projection', 'Lifts now + projected'),
    ('weighted_nonproductive', 'D11 weighted nonproductive lifts'),
)
NONPRODUCTIVE = {'relocate', 'restow_shift', 'restow_off', 'restow_on',
                 'hatch_cover_off', 'hatch_cover_on'}


def shipping_evidence(replay: dict[str, Any]) -> dict[str, Any]:
    """Bound checker, validation, shipping, provenance and repair evidence of one replay (no counts or prose)."""
    from stowage.checker import report_matches_inputs
    from stowage.model import CheckReport, Instance, MovePlan
    from app.validation import report_validation

    checker = replay.get('checker') or {}
    report = checker.get('report') or {}
    bound = False
    if report:
        try:
            instance = Instance.model_validate(replay['instance'])
            plan = MovePlan.model_validate(replay['plan'])
            checked = CheckReport.model_validate(report)
            bound = report_matches_inputs(checked, instance, plan)
        except (ValueError, TypeError, KeyError):
            pass
    if not bound:
        report = {}
    metadata = replay.get('metadata') or {}
    result = replay.get('planner_result') or {}
    validation = report_validation(report, instance=replay.get('instance'), plan=replay.get('plan'),
                                   planner_result=replay.get('planner_result'))
    clean = not metadata.get('stub_tainted', False) and not any(
        (artifact or {}).get('provenance', {}).get('stubs') for artifact in
        (report, replay.get('instance'), replay.get('plan'), result))
    return dict(report=report, bound=bound, validation=validation,
                checker_passed=bound and validation['status'] == 'PASS', clean=clean,
                shippable=bool(bound and validation['validated'] and metadata.get('shippable') is True),
                shut_out=report.get('shut_out', metadata.get('shut_out', [])),
                repaired=bool(metadata.get('repaired') or result.get('repaired')))


def ineligibility(item: dict[str, Any]) -> list[str]:
    """Why a plan cannot establish a saving; empty = eligible. ``item`` = ``shipping_evidence`` or ``_summary``."""
    return [reason for failed, reason in (
        (not item['checker_passed'], 'it did not pass the full checker with complete bound evidence'),
        (not item['shippable'], 'it has no clean shipping approval'),
        (not item['clean'], 'its provenance is stub-tainted'),
        (bool(item['shut_out']), 'it leaves load-list boxes shut out'),
        (item['repaired'], 'solver repair changed it')) if failed]


def racer_evidence(replay: dict[str, Any]) -> dict[str, Any]:
    """JSON-safe eligibility of one racer on the stage: the same test as ``build_comparison``'s ``comparable``."""
    evidence = shipping_evidence(replay)
    metadata = replay.get('metadata') or {}
    approved = metadata.get('departure_approved') is True
    reasons = ineligibility(evidence)
    if not approved and evidence['shippable']:
        reasons.append('its departure is not approved')
    return dict(eligible=not reasons, ineligible_reasons=reasons, departure_approved=approved,
                shippable=evidence['shippable'], repaired=evidence['repaired'], clean=evidence['clean'],
                shut_out_count=len(evidence['shut_out'] or []), validation_status=evidence['validation']['status'],
                provenance=metadata.get('provenance') or {})


def _summary(replay: dict[str, Any]) -> dict[str, Any]:
    evidence = shipping_evidence(replay)
    report, bound, validation = evidence['report'], evidence['bound'], evidence['validation']
    count = report.get('count') or replay['frames'][-1]['counts']
    counts = {key: count.get(key, 0) for key in
              ('loads', 'discharges', 'relocations', 'restow_lifts', 'hatch_cover_lifts',
               'weighted_nonproductive')}
    counts['future_restow_lifts'] = sum(f.get('restow_lifts', 0) for f in count.get('future', []))
    counts['future_hatch_cover_lifts'] = sum(f.get('hatch_cover_lifts', 0) for f in count.get('future', []))
    counts['future_total_lifts'] = counts['future_restow_lifts'] + counts['future_hatch_cover_lifts']
    counts['total_lifts_now'] = sum(counts[k] for k in
                                    ('loads', 'discharges', 'relocations', 'restow_lifts', 'hatch_cover_lifts'))
    counts['total_lifts_with_projection'] = counts['total_lifts_now'] + counts['future_restow_lifts'] + counts['future_hatch_cover_lifts']
    metadata = replay.get('metadata', {})
    # Frames contain only successfully executed moves. Never count the saved
    # plan's unexecuted tail or call reached-prefix totals a complete replay.
    executed_moves = max(0, len(replay['frames']) - 1)
    planned_moves = len(replay['plan']['moves'])
    count_scope = ('complete_plan' if executed_moves == planned_moves
                   and metadata.get('physical_replay_legal') is True else 'executed_prefix')
    passed, clean, shippable, shut_out = (evidence[k] for k in ('checker_passed', 'clean', 'shippable', 'shut_out'))
    from app.explanations import explanation_for_replay
    explanation = explanation_for_replay(replay, source_path=metadata.get('source_path'))
    return dict(plan_id=replay['plan']['id'],
                label=metadata.get('label', replay['plan']['id']),
                planner=metadata.get('planner', 'supplied-plan'),
                model_run=metadata.get('model_run'),
                repaired=evidence['repaired'],
                repairing_solver=metadata.get('repairing_solver'), repair_note=metadata.get('repair_note'),
                repair_status=metadata.get('repair_status'), fallback_needed=metadata.get('fallback_needed'),
                optimality_status=metadata.get('optimality_status'), planner_label=metadata.get('planner_label'),
                checker_passed=passed, clean=clean, shippable=shippable, shut_out=shut_out,
                validation_status=validation['status'], validation_reasons=validation['reasons'],
                provenance=metadata.get('provenance', {}), stub_tainted=not clean,
                bound=bound, binding_status='bound' if bound else 'unbound report',
                shipping_blockers=[*metadata.get('shipping_blockers', []), *([] if bound else ['Unbound report'])],
                executed_moves=executed_moves, planned_moves=planned_moves, count_scope=count_scope,
                counts=counts, explanation=explanation['text'], explanation_source=explanation['source'],
                explanation_model=explanation['model'], explanation_date=explanation['generated_at'],
                explanation_validator=explanation['validator'])


def _moves(replay: dict[str, Any]) -> list[tuple[int, dict]]:
    # Frames only contain successful lifts; rejected or unexecuted tail moves
    # must not be presented as avoided crane work.
    return [(index - 1, frame['move']) for index, frame in enumerate(replay['frames'])
            if index and frame.get('move') and frame['move']['kind'] in NONPRODUCTIVE]


def _key(move: dict) -> tuple:
    return move['kind'], move.get('container_id') or move.get('cover_id')


def _extra(reference: dict, other: dict) -> list[dict]:
    remaining = Counter(_key(move) for _, move in _moves(reference))
    events = []
    for index, move in _moves(other):
        key = _key(move)
        if remaining[key]:
            remaining[key] -= 1
            continue
        frame = other['frames'][index]
        target = frame.get('highlight', {}).get('target_id')
        actor = move.get('container_id') or move.get('cover_id')
        message = f"{move['kind'].replace('_', ' ').capitalize()} of {actor} occurs only in this plan's matched lift counts."
        if move['kind'] == 'relocate' and target:
            message += f' The playback is retrieving {target} at this point.'
        events.append(dict(kind=move['kind'], container_id=move.get('container_id'),
            cover_id=move.get('cover_id'), move_index=index, frame_index=index,
            src=move.get('src'), dst=move.get('dst'), target_id=target, message=message))
    return events


def build_comparison(left_replay: dict, right_replay: dict) -> dict:
    """Left is the selected plan, right its reference; positive deltas favour left.

    Refuse changed cargo, yard, vessel, initial ballast, objectives or other
    instance content even if a caller reuses the same human-readable instance id.
    Failed/partial/stub-tainted plans remain inspectable but cannot earn a win.
    """
    if any(replay.get('plan') is None or not replay.get('frames') for replay in (left_replay, right_replay)):
        raise ValueError('No plan available for comparison')
    identity = lambda value: json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False)
    if identity(left_replay['instance']) != identity(right_replay['instance']):
        raise ValueError('Comparison requires exactly the same instance and starting world')
    left, right = _summary(left_replay), _summary(right_replay)
    comparable = not ineligibility(left) and not ineligibility(right)
    rows = [dict(key=key, label=label, left=left['counts'][key], right=right['counts'][key],
                 saved=right['counts'][key] - left['counts'][key]) for key, label in COUNT_LABELS]
    repaired = [item for item in (left, right) if item['repaired']]
    return dict(instance_id=left_replay['instance']['id'], comparable=comparable,
                notice=('Both plans have clean shipping evidence on the same instance.' if comparable else
                        'Diagnostic comparison only: solver repair changed at least one plan; repaired plans cannot establish an approved planner comparison.' if repaired else
                        'Diagnostic comparison only: both plans must be shippable after full checking of all required calls, loading every box and proving clean provenance before claiming a saving.'),
                left=left, right=right, rows=rows,
                saved_objective=right['counts']['weighted_nonproductive']-left['counts']['weighted_nonproductive'],
                projected_lift_difference=right['counts']['future_total_lifts']-left['counts']['future_total_lifts'],
                avoided_by_left=_extra(left_replay, right_replay),
                avoided_by_right=_extra(right_replay, left_replay),
                evidence_note='Unmatched executed lifts, matched by move type and container/cover, including repetitions. Different destinations alone do not count as avoided lifts. These observations do not prove optimality or why a planner chose a move.')
