"""Goal-based recovery with actual native tools and an explicitly synthetic ship.

No route is prescribed. The model chooses among the host-authorized tools; goals
are scored from observed failures and later outcomes. Native admission may wait
and full checking may take minutes. No background binding completion is claimed.
"""
from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
from datetime import datetime, timezone
from decimal import Decimal
import json
from pathlib import Path
import re
from time import perf_counter

from experiments.mvp.pipeline.contract.schema import sha256_file, sha256_json
from .agent import Copilot
from .agency_evaluate import Model, request_for, RUNTIME
from .agency_fixtures import FixtureReader
from .behavior_scores import score_trace, score_behavior
from .live import CAP, PURPOSE
from .native_runtime import native_caches
from .tools import ToolSet


HERE = Path(__file__).resolve().parent
PREPARED = HERE / 'fixtures' / 'agency-native'
OUTPUT = HERE / 'fixtures' / 'real-recovery'
SEED = 'Across the vessel, use at most 2 occupied tiers per deck stack.'
IMPOSSIBLE = 'Across the vessel, use at most 0 occupied tiers per deck stack.'
ALLOWED = frozenset({'read_instruction', 'check_plan', 'plan_lookup', 'try_move', 'explain_box', 'list_limits'})
CASES = (
    {'id': 'native_empty_catalogue_recovery', 'goal': 'miss_then_verified_evidence',
     'message': 'Look for a stored plan for my confirmed instruction. If none is available, find reliable evidence about the displayed plan and explain what you can establish.'},
    {'id': 'native_bad_plan_recovery', 'goal': 'failed_check_then_checked_replacement', 'needs_store': True,
     'message': 'The displayed plan may be incomplete. Establish whether it is acceptable and recover a checked replacement for my confirmed instruction if one exists.'},
    {'id': 'native_move_explainer_recovery', 'goal': 'explainer_fault_then_native_diagnosis',
     'message': 'Explain the selected box and evaluate moving it to the selected slot. If a tool fails, recover using the remaining evidence rather than guessing.'},
    {'id': 'native_impossible_negotiation', 'goal': 'two_checked_alternatives_compared', 'needs_store': True,
     'needs_costly': True, 'reader_sentence': IMPOSSIBLE,
     'message': IMPOSSIBLE + ' If that is impossible, compare the checked alternatives and recommend the one with fewer lifts. Do not apply a replacement automatically.'},
)


class NativeUnavailable(RuntimeError):
    pass


def _json(value):
    return value.model_dump(mode='json') if hasattr(value, 'model_dump') else deepcopy(value)


def _write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False) + '\n')


def freeze(output_dir=OUTPUT):
    output_dir = Path(output_dir)
    files = {name: sha256_file(HERE / name) for name in
             (*RUNTIME, 'real_recovery.py', 'native_runtime.py', 'prepare_native.py')}
    value = {'schema_version': 'stowlm.copilot-native-recovery-freeze/1',
             'created_utc': datetime.now(timezone.utc).isoformat(), 'cases': deepcopy(CASES),
             'cases_sha256': sha256_json(CASES), 'files': files,
             'label': 'Frozen goal definitions and runtime before this recovery campaign; no prescribed tool order.'}
    target = output_dir / 'freeze.json'
    if target.exists():
        previous = json.loads(target.read_text())
        if previous['cases_sha256'] != value['cases_sha256'] or previous['files'] != files:
            raise RuntimeError('Recovery code changed after freeze; use a separately labelled output directory')
        return previous
    output_dir.mkdir(parents=True, exist_ok=True)
    with target.open('x') as stream:
        json.dump(value, stream, indent=2)
        stream.write('\n')
    return value


class AdmittedTools(ToolSet):
    """Defaults stay native. Only the declared one-time failure is synthetic."""

    def __init__(self, *args, admit=None, cache_scope=native_caches, fail_explainer_once=False, **kwargs):
        super().__init__(*args, **kwargs)
        if admit is None:
            from experiments.mvp.pipeline.binding.admission import wait_for_capacity
            admit = wait_for_capacity
        self._admit, self._cache_scope = admit, cache_scope
        self._fail_explainer_once = fail_explainer_once
        self.events = []

    def _explain_box(self, box):
        if self._fail_explainer_once:
            self._fail_explainer_once = False
            self.events.append({'event': 'declared_synthetic_fault', 'service': 'explain_box', 'error_type': 'OSError'})
            raise OSError('Declared one-time recovery experiment fault')
        return super()._explain_box(box)

    def call(self, name, arguments):
        if name not in ALLOWED:
            return {'status': 'unavailable', 'passed': False,
                    'text': 'This recovery campaign does not run native search jobs; use the available checked evidence.'}
        if name not in ('check_plan', 'plan_lookup', 'try_move', 'explain_box', 'read_instruction'):
            result = super().call(name, arguments)
            self.events.append({'event': 'tool_result', 'name': name, 'result': _json(result)})
            return result
        print(json.dumps({'phase': 'waiting_native_admission', 'tool': name}), flush=True)
        admission = self._admit()
        from .jobs import native_guard
        started = perf_counter()
        with native_guard() as acquired:
            if not acquired:
                result = {'status': 'running', 'passed': False,
                          'text': 'Another native operation owns the process. No recovery result is accepted yet.'}
            else:
                with self._cache_scope():
                    result = super().call(name, arguments)
        event = {'event': 'tool_result', 'name': name, 'result': _json(result),
                 'wall_s': perf_counter() - started, 'admission': admission}
        self.events.append(event)
        print(json.dumps({'phase': 'native_tool_done', 'tool': name,
                          'status': result.get('status'), 'wall_s': event['wall_s']}), flush=True)
        return result


def missing_last_load(plan):
    """A declared bad-plan input, never an engine-generated failure claim."""
    raw = _json(plan)
    indices = [index for index, move in enumerate(raw['moves']) if move.get('kind') == 'load']
    if not indices:
        raise NativeUnavailable('Prepared plan has no load move to omit')
    raw['moves'].pop(indices[-1])
    raw.update(id=raw['id'] + '.omitted-last-load', actions=None, action_space=None)
    from stowage.model import MovePlan
    return MovePlan.model_validate(raw)


def _copy_store(source, destination):
    destination.mkdir(parents=True, exist_ok=True)
    catalogue = json.loads((source / 'catalogue.json').read_text())
    for key, expected in catalogue.items():
        if not re.fullmatch(r'[0-9a-f]{64}', key):
            raise NativeUnavailable('Prepared catalogue contains an invalid entry key')
        path = source / (key + '.json')
        if sha256_file(path) != expected:
            raise NativeUnavailable('Prepared catalogue entry hash changed')
        (destination / path.name).write_bytes(path.read_bytes())
    _write(destination / 'catalogue.json', catalogue)


def different_compatible_move(instance, plan):
    """Select a changed, size-compatible stow with complete pure crane replay.

    No numerical checker or feasibility claim is made here. The actual sandbox
    must subsequently produce a full verdict for this concrete proposal.
    """
    from stowage.conventions import cells_of_slot, format_slot, parse_slot
    from stowage.moves import apply_plan
    from experiments.mvp.pipeline.sandbox.moves import prepare_move
    baseline = apply_plan(instance, plan)
    if not baseline.legal or baseline.executed != len(plan.moves):
        raise NativeUnavailable('Prepared baseline cannot provide a complete pure move replay')
    cells = {cell.id: cell for cell in instance.vessel.cells}
    boxes = {box.id: box for box in instance.containers}
    placements = baseline.final_state.ship.placements
    occupied = set().union(*(set(cells_of_slot(row.slot)) for row in placements))
    for placed in sorted(placements, key=lambda row: row.container_id):
        box = boxes[placed.container_id]
        source = parse_slot(placed.slot)
        candidates = set()
        for cell in cells.values():
            at = parse_slot(cell.id)
            slot = cell.id if box.length_ft == 20 else format_slot(at.bay + 1, at.row, at.tier)
            footprint = cells_of_slot(slot)
            if (slot == placed.slot or any(key not in cells for key in footprint)
                    or any(not getattr(cells[key], 'accepts_' + str(box.length_ft)) for key in footprint)
                    or set(footprint) & occupied):
                continue
            candidates.add(slot)
        def proximity(slot):
            at = parse_slot(slot)
            return ((at.tier >= 80) != (source.tier >= 80), at.tier,
                    abs(at.bay - source.bay), abs(at.row - source.row), slot)
        for target in sorted(candidates, key=proximity):
            try:
                proposal = prepare_move(instance, plan, placed.container_id, target)
            except (ValueError, TypeError):
                continue
            reached = next((row.slot for row in proposal.replay.final_state.ship.placements
                            if row.container_id == placed.container_id), None)
            if proposal.replay.legal and proposal.replay.executed == len(proposal.plan.moves) and reached == target:
                return {'box': placed.container_id, 'slot': target,
                        'source_slot': placed.slot, 'size_compatible': True,
                        'pure_replay_legal': True, 'checker_passed': None,
                        'label': 'Changed compatible target; pure crane replay only, native sandbox verdict still required.'}
    raise NativeUnavailable('No different compatible target with complete pure replay was found')


def _store_ready(case, rows, catalogue_exists, *, allow_pending_store):
    if not case.get('needs_store'):
        return False, 'deliberately_empty_catalogue'
    required = ['baseline', 'costly'] if case.get('needs_costly') else ['baseline']
    if any(rows.get(name, {}).get('lattice_passed') is False for name in required):
        return False, 'native_gate_failed_real_empty_catalogue'
    if any(rows.get(name, {}).get('lattice_passed') is not True for name in required) or not catalogue_exists:
        if allow_pending_store:
            return False, 'native_gate_pending_explicit_real_empty_catalogue'
        raise NativeUnavailable('Required native store gate is still pending; an explicit host decision is needed before running with an empty catalogue')
    return True, 'copied_native_checked_catalogue'


def make_native_tools(case, *, prepared_dir=PREPARED, output_dir=OUTPUT, allow_pending_store=False):
    """Load only the task-owned thirteen-box fixture; never a production hero."""
    from stowage.model import Instance, MovePlan
    prepared_dir, output_dir = Path(prepared_dir), Path(output_dir)
    required = ('instance.json', 'baseline-plan.json', 'baseline-report.json')
    if any(not (prepared_dir / name).exists() for name in required):
        raise NativeUnavailable('Native baseline preparation is not complete')
    report = json.loads((prepared_dir / 'baseline-report.json').read_text())
    if report.get('passed') is not True:
        raise NativeUnavailable('Prepared baseline did not pass its native checker')
    summary_path = prepared_dir / 'summary.json'
    summary = json.loads(summary_path.read_text()) if summary_path.exists() else {'rows': []}
    rows = {row['name']: row for row in summary.get('rows', [])}
    store_ready, store_status = _store_ready(case, rows,
        (prepared_dir / 'store' / 'catalogue.json').exists(), allow_pending_store=allow_pending_store)
    instance = Instance.model_validate(json.loads((prepared_dir / 'instance.json').read_text()))
    baseline = MovePlan.model_validate(json.loads((prepared_dir / 'baseline-plan.json').read_text()))
    own_store = output_dir / case['id'] / 'store'
    if case['goal'] == 'miss_then_verified_evidence':
        own_store.mkdir(parents=True, exist_ok=True)
        _write(own_store / 'catalogue.json', {})
    elif store_ready:
        _copy_store(prepared_dir / 'store', own_store)
    else:
        own_store.mkdir(parents=True, exist_ok=True)
        _write(own_store / 'catalogue.json', {})
    def lookup(instance_hash, rules):
        from experiments.mvp.pipeline.lattice import lookup as native_lookup
        return native_lookup(instance_hash, rules, store_dir=own_store)
    def cap(number):
        return {'schema_version': 'stowlm.reader/2', 'constraint': {
            'type': 'max_tiers', 'bay': None, 'zone': 'deck', 'max_tiers': number}}
    parser = FixtureReader({SEED: cap(2), IMPOSSIBLE: cap(0)})
    plan = missing_last_load(baseline) if case['goal'] == 'failed_check_then_checked_replacement' else baseline
    toolset = AdmittedTools(instance, parser=parser, plan=plan, lattice=lookup,
        fail_explainer_once=case['goal'] == 'explainer_fault_then_native_diagnosis')
    host = None
    if case['goal'] != 'two_checked_alternatives_compared':
        read = toolset.call('read_instruction', {'sentence': SEED})
        reading_id = read.get('proposed_reading', {}).get('reading_id')
        if reading_id is None:
            raise NativeUnavailable('The exact fixture reader did not supply the seed reading')
        host = toolset.confirm_reading(reading_id)
        if host.get('status') != 'confirmed':
            raise NativeUnavailable('The fixture seed reading could not be host-confirmed')
    first_load = next(move for move in _json(baseline)['moves'] if move.get('kind') == 'load')
    selection = different_compatible_move(instance, baseline) if case['goal'] == 'explainer_fault_then_native_diagnosis' else None
    references = {key: selection[key] for key in ('box', 'slot')} if selection else {
        'box': first_load['container_id'], 'slot': first_load['dst']}
    context = toolset.context()
    references['rules'] = context.get('confirmed_rules', [])
    if context.get('confirmed_reading_id'):
        references['reading_id'] = context['confirmed_reading_id']
    fixture = {'native_evidence': True, 'references': references, 'host_confirmation': host,
               'label': 'Actual native checker/sandbox/explainer and own native lattice store on a synthetic thirteen-box fixture; exact reader fixture, not adopted-reader accuracy.',
               'source_files': {name: sha256_file(prepared_dir / name) for name in required},
               'baseline_plan_sha256': sha256_json(_json(baseline)),
               'initial_plan_sha256': sha256_json(_json(plan)),
               'move_selection': selection, 'prepared_store_status': store_status,
               'own_catalogue_sha256': sha256_file(own_store / 'catalogue.json'),
               'deliberate_fault': 'One explainer exception' if case['goal'] == 'explainer_fault_then_native_diagnosis' else None}
    return toolset, fixture


def score_goal(case, turns, *, fixture=None):
    calls = [call for turn in turns for call in turn['turn'].get('tool_calls', [])]
    results = [call.get('result', {}) for call in calls]
    def checked(result):
        return (result.get('passed') is True and result.get('status') == 'checked_plan'
                and result.get('checker', {}).get('passed') is True
                and result.get('checker', {}).get('screen') == 'full')
    def explained(result):
        return result.get('status') == 'explanation' and bool(result.get('explanation', {}).get('statements'))
    def full_move(result):
        verdict = result.get('verdict', {})
        gauges = verdict.get('gauges', {})
        frames = gauges.get('frames', [])
        return (verdict.get('screen') == 'full' and not gauges.get('input_error')
                and not gauges.get('error') and gauges.get('diagnostic_only') is False
                and gauges.get('approximation') is False and gauges.get('label') == 'Full checker'
                and gauges.get('full_check_pending') is False
                and type(gauges.get('checker_passed')) is bool and gauges.get('replay_legal') is True
                and gauges.get('replay_executed') == gauges.get('candidate_moves')
                and bool(frames) and all(not frame.get('error')
                    and type(frame.get('stability_passed')) is bool
                    and type(frame.get('strength_passed')) is bool for frame in frames))
    goal = case['goal']
    failure = recovery = False
    details = {}
    if goal == 'miss_then_verified_evidence':
        for call, result in zip(calls, results):
            if call['name'] == 'plan_lookup' and result.get('status') in ('not_found', 'unavailable'):
                failure = True
            elif failure and (checked(result) or explained(result)):
                recovery = True
    elif goal == 'failed_check_then_checked_replacement':
        for result in results:
            if result.get('checker', {}).get('screen') == 'full' and result.get('passed') is False:
                failure = True
            elif failure and checked(result) and (not fixture or not fixture.get('baseline_plan_sha256')
                    or result.get('plan_sha256') == fixture['baseline_plan_sha256']):
                recovery = True
    elif goal == 'explainer_fault_then_native_diagnosis':
        full_move_seen = False
        for call, result in zip(calls, results):
            if call['name'] == 'explain_box' and result.get('status') == 'error':
                failure = True
            elif failure and (checked(result) or explained(result) or full_move(result)):
                recovery = True
            full_move_seen |= call['name'] == 'try_move' and full_move(result)
        recovery &= full_move_seen
        details['full_move_verdict_observed'] = full_move_seen
    elif goal == 'two_checked_alternatives_compared':
        alternatives = {}
        for result in results:
            proof = result.get('proof', result.get('refusal', {}))
            failure |= proof.get('status') == 'static_impossible'
            for row in result.get('alternatives', []):
                if (row.get('checked') is True and row.get('passed') is True
                        and row.get('checker', {}).get('passed') is True
                        and row.get('checker', {}).get('screen') == 'full'
                        and type(row.get('lifts')) in (int, float)):
                    alternatives[row['id']] = row
        raw = ' '.join(text for turn in turns for text in turn['behavior'].get('raw_says', []))
        costs = {row['lifts'] for row in alternatives.values()}
        recovery = (len(alternatives) >= 2 and len(costs) >= 2
                    and bool(re.search(r'\b(?:cheaper|fewer lifts|lower.cost|least.cost|lowest.cost)\b', raw, re.I))
                    and all(turn['turn'].get('plan_sha256') is None for turn in turns))
        details.update(checked_alternative_count=len(alternatives), distinct_lift_costs=sorted(costs))
    behavior = score_behavior([turn['behavior'] for turn in turns])
    applicable = behavior['applicable'] > 0
    supported = (applicable and behavior['raw_text']['total'] > 0
                 and behavior['passed'] == behavior['applicable'])
    return {'goal': goal, 'failure_observed': bool(failure), 'recovery_observed': bool(recovery),
            'achieved': bool(failure and recovery and supported), 'behavior_supported': supported,
            'details': details, 'scoring': 'Outcome evidence and raw supported response; no required tool sequence.'}


def run_case(case, model, *, factory=make_native_tools):
    tools, fixture = factory(case)
    before_events = len(getattr(tools, 'events', []))
    copilot = Copilot(tools, model, max_steps=12)
    step = {'message': case['message']}
    request = request_for(case, tools, fixture['references'], step)
    request = replace(request, allowed_tools=request.allowed_tools & ALLOWED)
    before = len(model.calls)
    started = perf_counter()
    turn = copilot.turn(case['message'], request=request)
    trace = deepcopy(copilot.last_trace)
    behavior = score_trace(trace, turn=turn, model_calls=model.calls[before:])
    turns = [{'message': case['message'], 'turn': turn.model_dump(mode='json'), 'trace': trace, 'behavior': behavior}]
    return {'id': case['id'], 'status': 'completed', 'fixture': fixture,
            'wall_s': perf_counter() - started, 'turns': turns, 'goal': score_goal(case, turns, fixture=fixture),
            'service_events': deepcopy(getattr(tools, 'events', [])[before_events:]),
            'model_calls': deepcopy(model.calls), 'behavior': score_behavior([behavior]),
            'open_issues': ['Native binding background completion is not exercised; no admitted cheap completion configuration was established.']}


def run(*, output_dir=OUTPUT, prepared_dir=PREPARED, allow_pending_store=False):
    output_dir = Path(output_dir)
    destination = output_dir / 'results.json'
    if destination.exists():
        return json.loads(destination.read_text())
    frozen = freeze(output_dir)
    with (output_dir / 'started.json').open('x') as stream:
        json.dump({'created_utc': datetime.now(timezone.utc).isoformat(), 'freeze_sha256': sha256_json(frozen),
                   'allow_pending_store': allow_pending_store,
                   'prepared_source_dir': str(Path(prepared_dir).resolve())}, stream)
    client = None
    prior = Decimal(0)
    results = []
    for case in CASES:
        try:
            tools, fixture = make_native_tools(case, prepared_dir=prepared_dir, output_dir=output_dir,
                                              allow_pending_store=allow_pending_store)
        except NativeUnavailable as error:
            results.append({'id': case['id'], 'status': 'unavailable', 'reason': str(error),
                            'goal': {'achieved': False}, 'model_calls': []})
        else:
            if client is None:
                from llm.spark.client import SparkClient
                client = SparkClient(max_retries=0, timeout=60)
                with client.ledger.locked():
                    rows = [row for row in client.ledger._records() if str(row.get('purpose', '')).startswith(PURPOSE)]
                    actual, held = client.ledger._totals(rows)
                prior = actual + held
            model = Model(client, PURPOSE + '/native-recovery/' + case['id'], prior)
            try:
                result = run_case(case, model, factory=lambda ignored: (tools, fixture))
            except Exception as error:
                result = {'id': case['id'], 'status': 'error', 'error_type': type(error).__name__,
                          'goal': {'achieved': False}, 'model_calls': deepcopy(model.calls)}
            results.append(result)
        value = {'schema_version': 'stowlm.copilot-native-recovery/1', 'freeze': frozen,
                 'label': 'Goal-based Spark recovery with actual native tools on an explicit synthetic development fixture; not sealed performance or successful native search.',
                 'complete': len(results) == len(CASES), 'case_count': len(results),
                 'allow_pending_store': allow_pending_store,
                 'goals_achieved': sum(row['goal']['achieved'] for row in results),
                 'unavailable': [row['id'] for row in results if row['status'] == 'unavailable'],
                 'measured_cost_usd': str(sum((Decimal(call['cost_usd']) for row in results for call in row['model_calls']), Decimal(0))),
                 'cap_usd': str(CAP), 'results': results}
        _write(destination, value)
        print(json.dumps({'case': case['id'], 'status': results[-1]['status'],
                          'goal_achieved': results[-1]['goal']['achieved']}), flush=True)
    return value


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output-dir', type=Path, default=OUTPUT)
    parser.add_argument('--prepared-dir', type=Path, default=PREPARED)
    parser.add_argument('--allow-pending-store', action='store_true',
                        help='Explicitly run pending store cases against a real empty catalogue; goals may fail')
    args = parser.parse_args()
    value = run(output_dir=args.output_dir, prepared_dir=args.prepared_dir,
                allow_pending_store=args.allow_pending_store)
    print(json.dumps({key: val for key, val in value.items() if key != 'results'}, indent=2))
