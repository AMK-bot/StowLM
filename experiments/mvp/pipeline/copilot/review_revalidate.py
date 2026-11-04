"""Offline review replay of immutable historical model actions.

Original-output rescoring and current-guard playback are separate measurements.
Playback is not another model evaluation: changed authorization can deny an old
action, end a turn early, or leave recorded actions unconsumed. No paid client is
imported or constructed, and no campaign definitions or native tools are needed.
"""
from __future__ import annotations

import argparse
from copy import deepcopy
from datetime import datetime, timezone
import json
from pathlib import Path

from experiments.mvp.pipeline.contract.schema import sha256_file, sha256_json
from .agent import Copilot
from .authorization import ActionGrant, TurnRequest
from .scenarios import METRICS, ScenarioTools, score_turn


HERE = Path(__file__).resolve().parent
DEFAULT_SOURCES = tuple(HERE / 'fixtures' / name for name in
                        ('live-scenarios.json', 'tuned-v3.json', 'fresh-v3.json'))
NOTE = ('Offline compatibility replay of historical model actions against current guards and '
        'synthetic tools. No new model decisions, paid calls, native planning, or live performance measurement.')


def runtime_fingerprint() -> dict:
    files = {name: sha256_file(HERE / name) for name in
             ('agent.py', 'authorization.py', 'safety.py', 'tools.py', 'jobs.py',
              'scenarios.py', 'voice.py', 'review_revalidate.py')}
    files['../contract/schema.py'] = sha256_file(HERE.parent / 'contract' / 'schema.py')
    return {'files': files, 'sha256': sha256_json(files)}


class TurnActions:
    """One original turn only. Exhaustion never borrows another turn's action."""
    mode = 'recorded'

    def __init__(self, trace):
        self.actions = [row['model_raw'] for row in trace
                        if row.get('role') == 'model' and isinstance(row.get('model_raw'), str)]
        self.index = 0
        self.exhausted = False

    def complete(self, prompt):
        if self.index >= len(self.actions):
            self.exhausted = True
            raise RuntimeError('Historical turn actions exhausted')
        raw = self.actions[self.index]
        self.index += 1
        return raw

    def accounting(self):
        return {'recorded_actions': len(self.actions), 'consumed_actions': self.index,
                'unconsumed_raw_actions': self.actions[self.index:],
                'unconsumed_actions': len(self.actions) - self.index,
                'exhausted': self.exhausted,
                'original_actions_sha256': sha256_json(self.actions)}


def _context(turn):
    return next((deepcopy(row['content']) for row in turn.get('trace', [])
                 if row.get('role') == 'context' and isinstance(row.get('content'), dict)), {})


def _calls(turn):
    calls = deepcopy(turn.get('tools', turn.get('turn', {}).get('tool_calls', [])))
    context = _context(turn)
    confirmed = context.get('confirmed_reading_ids', [])
    if context.get('confirmed_reading_id'):
        confirmed = [*confirmed, context['confirmed_reading_id']]
    for call in calls:
        call.setdefault('arguments', {})
        call.setdefault('confirmed_at_call', list(confirmed))
        call.setdefault('reader_rules_at_call', deepcopy(context.get('confirmed_rules', [])))
    return calls


def _aggregate(rows):
    return {'scenario_count': len(rows), 'passed': sum(row['passed'] for row in rows),
            'metrics': {metric: {'passed': sum(row['scores'][metric] for row in rows),
                                 'total': len(rows)} for metric in METRICS},
            'failures': [row['id'] for row in rows if not row['passed']], 'results': rows}


def _original_rescore(record):
    turns, actual = [], []
    scores = {metric: True for metric in METRICS[1:]}
    for row in record.get('turns', []):
        calls = _calls(row)
        measured = score_turn(row['turn'], calls)
        actual.extend(call['name'] for call in calls)
        for name in scores:
            scores[name] &= measured[name]
        turns.append({'message': row['message'], 'scores': measured,
                      'source_turn_sha256': sha256_json(row), 'recorded_tools': len(calls)})
    expected = record.get('expected_tool_sequence', [])
    historical_routing = record.get('scores', {}).get('right_tool_sequence')
    if type(historical_routing) is bool:
        routing_score = historical_routing
        routing_source = 'historical_recorded_score; original allowed-sequence definitions are not reconstructed'
    else:
        routing_score = actual == expected and bool(turns)
        routing_source = 'exact_recorded_expected_sequence; historical routing score unavailable'
    scores = {'right_tool_sequence': routing_score, **scores}
    return {'id': record['id'], 'scores': scores, 'passed': all(scores.values()),
            'historical_scores': deepcopy(record.get('scores')),
            'right_tool_sequence_source': routing_source,
            'expected_tool_sequence': deepcopy(expected), 'actual_tool_sequence': actual,
            'turns': turns}


def _scenario_from_record(record):
    """Recover only synthetic-double settings already visible in the recording.

    This never reads the frozen cohort definitions and does not reconstruct a
    grant or a rule from a model action. Missing initial host state is reported
    by playback rather than guessed.
    """
    scenario = {'id': record['id'], 'kind': record['kind']}
    for turn in record.get('turns', []):
        context = _context(turn)
        document = context.get('document')
        if isinstance(document, dict) and isinstance(document.get('text'), str):
            scenario['document'] = document['text']
        for call in _calls(turn):
            result = call.get('result', {})
            if result.get('reason') in ('box_count', 'ship', 'fill', 'arrival'):
                scenario['envelope_reason'] = result['reason']
            if call.get('name') == 'replan' and result.get('status') in ('running', 'queued', 'pending'):
                scenario['background'] = True
    return scenario


def _request(value):
    if value is None or isinstance(value, TurnRequest):
        return value
    if not isinstance(value, dict) or set(value) - {'selected_sentence', 'actions'}:
        raise ValueError('Host request must explicitly contain selected_sentence/actions')
    actions = tuple(ActionGrant(row['name'], deepcopy(row.get('arguments', {})))
                    for row in value.get('actions', []))
    return TurnRequest(selected_sentence=value.get('selected_sentence'), actions=actions)


def _host_events(tools, source_turn):
    context = _context(source_turn)
    ids = list(context.get('confirmed_reading_ids', []))
    if isinstance(context.get('confirmed_reading_id'), str):
        ids.append(context['confirmed_reading_id'])
    events = []
    for reading_id in dict.fromkeys(ids):
        if reading_id in tools.confirmed:
            continue
        if reading_id not in tools.readings:
            events.append({'event': 'planner_confirmation', 'reading_id': reading_id,
                           'applied': False, 'reason': 'Reader result absent in current playback'})
            continue
        tools.confirm_reading(reading_id)
        events.append({'event': 'planner_confirmation', 'reading_id': reading_id, 'applied': True,
                       'source': 'recorded_host_context'})
    return events


def _current_playback(record, *, scenario=None, host_requests=None):
    tools = ScenarioTools(scenario or _scenario_from_record(record))
    copilot = Copilot(tools, TurnActions([]))
    turns, actual, errors = [], [], []
    scores = {metric: True for metric in METRICS[1:]}
    total_recorded = total_consumed = total_unconsumed = 0
    for index, source_turn in enumerate(record.get('turns', [])):
        client = TurnActions(source_turn.get('trace', []))
        copilot.client = client
        events = _host_events(tools, source_turn)
        supplied = host_requests[index] if host_requests is not None and index < len(host_requests) else None
        request = _request(supplied if supplied is not None else source_turn.get('host_request'))
        start = len(tools.calls)
        try:
            turn = copilot.turn(source_turn['message'], request=request).model_dump(mode='json')
            calls = deepcopy(tools.calls[start:])
            measured = score_turn(turn, calls)
            for name in scores:
                scores[name] &= measured[name]
            actual.extend(call['name'] for call in calls)
            replay = {'message': source_turn['message'], 'turn': turn, 'tools': calls,
                      'scores': measured, 'trace': deepcopy(copilot.last_trace)}
        except Exception as error:
            errors.append({'turn_index': index, 'error_type': type(error).__name__})
            scores = {name: False for name in scores}
            replay = {'message': source_turn['message'], 'error_type': type(error).__name__}
        accounting = client.accounting()
        total_recorded += accounting['recorded_actions']
        total_consumed += accounting['consumed_actions']
        total_unconsumed += accounting['unconsumed_actions']
        replay.update(accounting, host_events=events,
                      source_turn_sha256=sha256_json(source_turn),
                      host_request_source=('explicit_host_request' if request is not None
                                           else 'current_conservative_message_authorization'))
        if any(not event['applied'] for event in events):
            errors.append({'turn_index': index, 'error_type': 'HostConfirmationNotReplayable'})
        turns.append(replay)
    expected = record.get('expected_tool_sequence', [])
    scores = {'right_tool_sequence': actual == expected and bool(turns) and not errors, **scores}
    playback_complete = not errors and total_unconsumed == 0 and not any(row['exhausted'] for row in turns)
    return {'id': record['id'], 'kind': record['kind'], 'scores': scores,
            'passed': all(scores.values()) and playback_complete,
            'strict_action_playback_complete': playback_complete,
            'expected_historical_tool_sequence': deepcopy(expected), 'actual_tool_sequence': actual,
            'recorded_actions': total_recorded, 'consumed_actions': total_consumed,
            'unconsumed_actions': total_unconsumed, 'errors': errors,
            'authorization_denials': sum(row.get('role') == 'authorization'
                                        for turn in turns for row in turn.get('trace', [])),
            'turns': turns}


def revalidate_recording(recording, *, source_sha256=None, scenarios=None, host_requests=None):
    """Pure in-memory rescoring/replay; original records are never modified.

    Optional ``scenarios`` is a host-supplied id->synthetic-definition mapping.
    Optional ``host_requests`` is an id->per-turn explicit host request mapping.
    Defaults grant no capability by inspecting historical model actions.
    """
    original, current = [], []
    for record in recording['results']:
        original.append(_original_rescore(record))
        current.append(_current_playback(record,
            scenario=(scenarios or {}).get(record['id']),
            host_requests=(host_requests or {}).get(record['id'])))
    return {'source_file_sha256': source_sha256,
            'source_content_sha256': sha256_json(recording), 'label': NOTE,
            'historical_summary': {key: deepcopy(recording[key]) for key in
                ('scenario_count', 'passed', 'metrics', 'failures', 'measured_cost_usd', 'cap_usd') if key in recording},
            'original_safety_rescore': _aggregate(original),
            'current_guard_action_replay': _aggregate(current),
            'paid_calls': 0, 'new_spend_usd': '0',
            'confirmation_assumption': 'Only recorded host-context confirmation events are replayed; absent current readings remain unresolved.',
            'authorization_assumption': 'Current message authorization unless an explicit host request was separately supplied; no grants inferred from model actions.'}


def build_review_record(sources=None, *, destination=None):
    """Hash final runtime and sources; optionally write one new review artifact."""
    sources = tuple(Path(path) for path in (DEFAULT_SOURCES if sources is None else sources))
    if destination is not None and Path(destination).resolve() in {path.resolve() for path in sources}:
        raise ValueError('Review output cannot overwrite an immutable source recording')
    before = runtime_fingerprint()
    records = []
    for path in sources:
        digest = sha256_file(path)
        value = revalidate_recording(json.loads(path.read_text()), source_sha256=digest)
        if sha256_file(path) != digest:
            raise RuntimeError('Source recording changed during offline replay')
        value['source_name'] = path.name
        records.append(value)
    after = runtime_fingerprint()
    stable = before == after
    output = {'schema_version': 'stowlm.copilot-review-revalidation/1', 'mode': 'recorded',
              'created_utc': datetime.now(timezone.utc).isoformat(), 'label': NOTE,
              'runtime_stable': stable, 'runtime_before': before, 'runtime_after': after,
              'final_runtime_sha256': after['sha256'] if stable else None,
              'source_sha256': {row['source_name']: row['source_file_sha256'] for row in records},
              'recordings': records, 'paid_calls': 0, 'new_spend_usd': '0',
              'warning': 'Historical live performance summaries remain immutable. These are independent offline rescores and guard compatibility replays.'}
    if destination is not None:
        Path(destination).write_text(json.dumps(output, indent=2, ensure_ascii=False) + '\n')
    return output


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('sources', nargs='*', type=Path)
    parser.add_argument('--output', type=Path, default=HERE / 'fixtures' / 'review-revalidated.json')
    args = parser.parse_args()
    value = build_review_record(args.sources or None, destination=args.output)
    print(json.dumps({'runtime_stable': value['runtime_stable'],
                      'final_runtime_sha256': value['final_runtime_sha256'],
                      'recordings': len(value['recordings']), 'paid_calls': 0,
                      'output': str(args.output)}, indent=2))


if __name__ == '__main__':
    main()
