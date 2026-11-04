"""Final once-only Spark goals and a separately labelled real-tool demo take."""
from __future__ import annotations
IMPORT_SOURCE_SHA256 = __import__('hashlib').sha256(__import__('pathlib').Path(__file__).read_bytes()).hexdigest()
from copy import deepcopy
from dataclasses import asdict
from datetime import datetime, timezone
from decimal import Decimal
import hashlib
import json
from pathlib import Path
import time

from experiments.mvp.pipeline.contract.schema import canonical_json, sha256_file, sha256_json
from experiments.mvp.reader.schema import messages, validate_answer
from experiments.mvp.reader.pipeline import ParserOutputError
from .agent import Copilot
from .authorization import TurnRequest
from .hero_campaign import HERE, OUT, save
from .hero_goal_scores import load_scenarios, score_hero_scenario, score_hero_campaign, _native_pass, _native_failure
from .real_demo import _Session
from .tools import ToolSet
from .behavior_scores import score_behavior


class Session(_Session):
    def complete(self, prompt, **kwargs):
        validate_freeze(json.loads((OUT/'run-authorization.json').read_text()))
        try:
            return super().complete(prompt, **kwargs)
        finally:
            save(OUT/'calls.json', dict(model_calls=self.calls, measured_cost_usd=str(sum(
                (Decimal(c['cost_usd']) for c in self.calls), Decimal(0)))))


def validate_freeze(marker, *, root=HERE, loaded_modules=None):
    for name, digest in marker['files'].items():
        if sha256_file(root/name) != digest:
            raise RuntimeError('Campaign source changed after final freeze: '+name)
    if sha256_file(root/'fixtures/hero-scenarios.json') != marker['scenarios_sha256']:
        raise RuntimeError('Fresh scenarios changed after final freeze')
    # Test fixtures rooted elsewhere have their own optional registry. The
    # actual runner must also prove that already imported code matches disk.
    if Path(root).resolve() == HERE.resolve() or loaded_modules is not None:
        from .loaded_freeze import validate_loaded_freeze
        return validate_loaded_freeze(marker['files'], root=Path(root), modules=loaded_modules)


class Reader:
    """Unadapted Spark, existing reader schema; exact-response reuse is labelled."""
    def __init__(self, session):
        self.session, self.cache, self.records = session, {}, []
        self.purpose = 'pipeline-t1.6-copilot/hero-final/reader'

    def parse(self, sentence, context):
        if not sentence.strip():
            raw = {'schema_version':'stowlm.reader/2','refusal':{
                'refused':True,'code':'not_an_instruction','reason':'Empty standing text.'}}
            return validate_answer(json.dumps(raw), context), dict(parser='empty-standing-template', adapter_sha256=None)
        key = sha256_json(dict(sentence=sentence, context=context))
        if key in self.cache:
            answer, evidence = self.cache[key]
            evidence = dict(deepcopy(evidence), exact_response_reused=True)
            self.records.append(dict(sentence=sentence, evidence=evidence))
            save(OUT / 'reader-records.json', self.records)
            return answer, evidence
        prompt = '\n\n'.join(row['content'] for row in messages(sentence, context))
        response = self.session.complete(prompt, purpose=self.purpose,
                                         max_output_tokens=1024, reasoning_effort='minimal')
        evidence = dict(parser='hero-unadapted-Spark-reader', model='muse-spark-1.3', adapter_sha256=None,
                        label='Unadapted cloud Spark reader; final reader release not present.',
                        purpose=self.purpose, request_id=response.request_id, raw=response.text,
                        cost_usd=str(response.cost_usd), prompt_sha256=hashlib.sha256(prompt.encode()).hexdigest())
        self.records.append(dict(sentence=sentence, evidence=deepcopy(evidence)))
        save(OUT / 'reader-records.json', self.records)
        try:
            answer = validate_answer(response.text, context)
        except (ValueError, TypeError):
            raise ParserOutputError(evidence) from None
        self.cache[key] = answer, deepcopy(evidence)
        return answer, evidence


class Model:
    mode = 'live'
    def __init__(self, session, purpose):
        self.session, self.purpose, self.calls = session, purpose, []

    def complete(self, prompt):
        before = len(self.session.calls)
        try:
            response = self.session.complete(prompt, purpose=self.purpose,
                max_output_tokens=3072, reasoning_effort='low')
            save(OUT/'raw-actions'/(response.request_id+'.json'), dict(
                purpose=self.purpose, request_id=response.request_id, raw=response.text))
            return response.text
        finally:
            self.calls.extend(deepcopy(self.session.calls[before:]))


def _event(path, gate):
    return dict(native=True, fresh=True, plan_sha256=gate['plan_sha256'],
                freshness_scope='Actual native campaign preparation, not a new numerical invocation in this turn or interpreter.',
                source_sha256=sha256_file(path), source_file=str(path.relative_to(HERE)) if path.is_relative_to(HERE) else str(path),
                reuse_label='Gated during native campaign preparation; immutable receipt reused, with current identity checks. No per-turn native invocation claimed.', gate=gate)


def persist_evidence(evidence):
    """Pin the exact score inputs without copying native timelines per case."""
    return dict(evidence, native_gates=[{key:deepcopy(value) for key,value in event.items() if key != 'gate'}
                                      for event in evidence.get('native_gates', [])])


def load_evidence(evidence, *, root=HERE):
    events = []
    for event in evidence.get('native_gates', []):
        path = root/event['source_file']
        if not path.resolve().is_relative_to(root.resolve()) or sha256_file(path) != event['source_sha256']:
            raise ValueError('Native score evidence changed')
        raw = json.loads(path.read_text())
        gate = raw.get('gate', raw)
        if gate.get('plan_sha256') != event['plan_sha256']:
            raise ValueError('Native score evidence belongs to another plan')
        events.append(dict(event, gate=gate))
    return dict(evidence, native_gates=events)


def gate_is_current(gate):
    from app.stage_physics import physics_drift, physics_epoch
    from experiments.mvp.pipeline.lattice.gates import engine_identity
    from stowage.frozen import active_stubs
    return (not physics_drift() and not active_stubs()
            and gate.get('engine_identity') == engine_identity()
            and (gate.get('gauges', {}).get('physics') or {}).get('physics_epoch') == physics_epoch())


def tag_recorded_release(gate):
    """Label an input-bound cache hit after its caller verifies current identity."""
    result = deepcopy(gate)
    result.update(mode='stored', reused_recorded=True, freshly_rechecked=False,
                  gate_source='authenticated_release_cache', current_identity_verified=True)
    return result


def validate_native(prepared):
    for key, row in prepared['checked'].items():
        if not isinstance(row, dict):
            raise RuntimeError('A required authenticated hero result is unavailable')
        if row.get('status') == 'proved_refusal':
            proof = row.get('proof') or {}
            if not (row.get('proof_rechecked') is True and proof.get('proven_impossible') is True
                    and proof.get('demand_cells', 0) > proof.get('open_cells', float('inf'))):
                raise RuntimeError('The hero refusal lacks a fresh arithmetic proof')
        elif not _native_pass(_event(OUT/'native'/(key+'.json'), row['gate'])):
            raise RuntimeError('A hero alternative lacks a complete fresh native gate')
    if not _native_failure(_event(OUT/'native/original-candidate-gate.json', prepared['failed_gate'])):
        raise RuntimeError('The failure scenario lacks an actual measured crane-frame failure')


def _request(tools, *, selected=None, box=None, slot=None, limited=None):
    context = tools.context()
    refs = dict(rules=context.get('confirmed_rules', []))
    for field in ('reading_id', 'job_id'):
        value = (context.get('confirmed_reading_id') or context.get('pending_reading_id')) if field == 'reading_id' else context.get('active_job_id')
        if value:
            refs[field] = value
    if box:
        refs['box'] = box
    if slot:
        refs['slot'] = slot
    names = set(limited if limited is not None else tools.names)
    if selected is None:
        names.discard('read_instruction')
    if box is None:
        names.discard('explain_box')
    if box is None or slot is None:
        names.discard('try_move')
    return TurnRequest(selected_sentence=selected, allowed_tools=frozenset(names), references=refs)


def _turn(copilot, model, message, request):
    before, start = len(model.calls), time.perf_counter()
    error = None
    try:
        answer = copilot.turn(message, request=request)
        output = answer.model_dump(mode='json')
    except Exception as exc:
        output = None
        error = type(exc).__name__
    return dict(message=message, turn=output, trace=deepcopy(copilot.last_trace),
                model_calls=deepcopy(model.calls[before:]), wall_s=time.perf_counter()-start,
                raw_say=copilot.last_raw_say, gate=deepcopy(copilot.last_gate_report),
                error_type=error, completion_status='incomplete' if error else 'completed',
                host_request=dict(selected_sentence=request.selected_sentence,
                                  allowed_tools=sorted(request.allowed_tools), references=request.references))


def drain_job(tools, path, *, pause=time.sleep, announce=print):
    """Keep detached native work from contaminating the next independent case."""
    while tools._active_job:
        job_id = tools._active_job
        state = tools.job_manager.poll(job_id)
        if state.get('status') not in ('running','queued','pending'):
            receipt = tools.call('wait_replan', dict(job_id=job_id))
            save(path, receipt)
            return receipt
        announce('Real background worker still active: '+str(state.get('elapsed_s'))+' seconds', flush=True)
        pause(30)
    return None


def run(prepared, **options):
    # Preparation may have started before the cache-path audit. Scope the
    # refusal helper too before any paid conversation uses sibling APIs.
    from experiments.mvp.pipeline.lattice import runtime
    previous = runtime.HERE
    runtime.HERE = HERE/'cache/agency-native/lattice'
    try:
        return _run(prepared, **options)
    finally:
        runtime.HERE = previous


def _run(prepared, *, verified=False, skip_scenarios=()):
    from llm.spark.client import SparkClient
    from engine.config import Config
    from experiments.mvp.pipeline.lattice import gate_plan, canonical_rule_set
    from .audit_spend import audit
    from .spending import POLICY
    loaded = validate_freeze(json.loads((OUT/'run-authorization.json').read_text()))
    save(OUT/'loaded-code-attestation.json', dict(loaded=loaded,
         label='Critical loaded-module import hashes match frozen files; also checked before every paid call.'))
    validate_native(prepared)
    with (OUT/'run-started.json').open('x') as marker:
        json.dump(dict(created_utc=datetime.now(timezone.utc).isoformat(),
                       label='Exclusive once-only claim before all paid calls.'), marker, indent=2)
    snapshot, stored = prepared['snapshot'], prepared['checked']
    rows = prepared['rows']
    base_key = next(k for k in rows if k.startswith('e47b'))
    dg_key = next(k for k in rows if k.startswith('9381'))
    close_key = next(k for k in rows if k.startswith('72cc'))
    refusal_key = next(k for k in rows if k.startswith('3398'))
    if not all(stored.values()):
        raise RuntimeError('Required real hero gates or refusal unavailable; no paid campaign started')
    policy = json.loads(POLICY.read_text())
    policy.update(state='verified_hero_authorized' if verified else 'hero_authorized',
                  authorization_source='Leader: The hero is real; run the final freeze and shot 10, cumulative cap $12.',
                  hero_instance_sha256=snapshot['hero']['instance_sha256'],
                  lattice_catalogue_sha256=snapshot['catalogue_sha256'])
    save(POLICY, policy)
    client = SparkClient(max_retries=0, timeout=60)
    with client.ledger.locked():
        prior_rows = [r for r in client.ledger._records() if str(r.get('purpose','')).startswith('pipeline-t1.6-copilot')]
        actual, held = client.ledger._totals(prior_rows)
    session = Session(client, prior_job_usd=actual+held)
    reader = Reader(session)
    def purpose(phase, suffix):
        return 'pipeline-t1.6-copilot/'+phase+('/verified/' if verified else '/')+suffix
    reader.purpose = purpose('hero-final','reader')
    native = [_event(OUT/'native'/(key+'.json'), row['gate'])
              for key,row in stored.items() if row.get('gate')]
    native.append(_event(OUT/'native/original-candidate-gate.json', prepared['failed_gate']))
    gate_cache = {(event['gate']['instance_sha256'], event['plan_sha256'],
                   canonical_json(canonical_rule_set([r['envelope'] for r in event['gate']['rules']]))): event['gate'] for event in native}

    def release(instance, plan, rules):
        raw_instance = instance.model_dump(mode='json') if hasattr(instance,'model_dump') else instance
        raw_plan = plan.model_dump(mode='json') if hasattr(plan,'model_dump') else plan
        key = sha256_json(raw_instance), sha256_json(raw_plan), canonical_json(canonical_rule_set(rules))
        if key not in gate_cache or not gate_is_current(gate_cache[key]):
            gate_cache[key] = gate_plan(instance, plan, rules)
            path = OUT/'native'/('additional-'+sha256_json(key)+'.json')
            save(path, gate_cache[key])
            native.append(_event(path, gate_cache[key]))
            return deepcopy(gate_cache[key])
        return tag_recorded_release(gate_cache[key])

    alternatives = [dict(id=name, sentences=rows[key]['sentences'], canonical_rules=rows[key]['canonical_rules'])
                    for name,key in (('dg-clearance', dg_key), ('bay-closure', close_key))]
    # Small genuine search profile; no all-stages-disabled pretend recovery.
    config = Config(work_count_search=False, warm_polish=False,
                    assignment_budget_s=5., assignment_finalize_s=0., assignment_tries=1,
                    peel_proposal_s=1., peel_finalize_s=0., peel_candidates=1,
                    post_pass_exchange=False, lift_polish=False, area_securing=False,
                    proof_work_limit=.25, proof_outer_s=5., target_work_limit=.25)
    planning = dict(budget_s=5., finalize_s=0., seed=0, config=config)
    save(OUT/'planning-profile.json', dict(config=asdict(config), budget_s=5., finalize_s=0.,
         label='Real native reduced-budget search; atomic native stages can overrun requested budgets.'))

    def make(plan):
        return ToolSet(prepared['instance'], parser=reader, plan=deepcopy(plan),
                       lattice=prepared.get('lattice_lookup'),
                       lattice_store_dir=prepared['store'], catalogue_alternatives=alternatives,
                       release_gate=release, planning_options=planning)

    def setup_read(tools, sentence, expected):
        proposal = tools.call('read_instruction', dict(sentence=sentence))
        proposed = proposal.get('proposed_reading') or {}
        parsed = [row['parsed'] for row in proposed.get('rows', []) if row.get('role') == 'voyage' and row.get('status') == 'accepted']
        if canonical_rule_set(parsed) != canonical_rule_set(expected):
            return dict(status='unavailable', reader_proposal=proposal,
                        reason='Reader interpretation does not match the frozen intended setup; no host confirmation given.')
        reading = proposed.get('reading_id')
        confirmation = tools.confirm_reading(reading) if reading else dict(status='unavailable')
        if confirmation.get('status') != 'confirmed':
            return dict(status='unavailable', reader_proposal=proposal, host_confirmation=confirmation,
                        reason='The intended reader setup could not be confirmed.')
        return dict(selected_sentence=sentence, reader_proposal=proposal, host_confirmation=confirmation,
                    confirmation_source='Planner action: adopt displayed exact reader rows in frozen scenario setup.')

    def record_take():
        reader.purpose = purpose('shot10','reader')
        tools = make(stored[base_key]['plan'])
        model = Model(session, purpose('shot10','hero'))
        copilot = Copilot(tools, model, max_steps=10)
        zero = '\n'.join(rows[refusal_key]['sentences'])
        takes = [_turn(copilot, model,
            'Read my selected request. If it cannot work, explain the arithmetic and rank the checked choices with reasons and their lift costs before I choose.',
            _request(tools, selected=zero, limited=('read_instruction','list_limits')))]
        save(OUT/'shot10-progress.json', dict(turns=takes))
        choice = rows[dg_key]['sentences'][0]
        takes.append(_turn(copilot, model, 'I choose the DG clearance alternative. Read the selected sentence and show every row for my confirmation.', _request(tools, selected=choice, limited=('read_instruction','list_limits'))))
        save(OUT/'shot10-progress.json', dict(turns=takes))
        pending = tools.context().get('current_reading') or {}
        selected_rows = [row.get('parsed') for row in pending.get('rows', []) if row.get('role') == 'voyage' and row.get('status') == 'accepted']
        if pending.get('sentence') == choice and canonical_rule_set(selected_rows) == canonical_rule_set(rows[dg_key]['canonical_rules']):
            confirmation = tools.confirm_reading(pending.get('reading_id'))
        else:
            confirmation = dict(status='unavailable', reason='The chosen instruction was not read exactly; no confirmation supplied.')
        if confirmation.get('status') == 'confirmed':
            takes.append(_turn(copilot, model, 'I confirmed the displayed reading. Find its stored plan, check it, and explain the operational change.', _request(tools, limited=('plan_lookup','check_plan','list_limits'))))
            save(OUT/'shot10-progress.json', dict(turns=takes, confirmation=confirmation))
            takes.append(_turn(copilot, model, 'Explain the selected cargo and the evidence for its placement.', _request(tools, box='QZXU0000288', limited=('check_plan','explain_box','list_limits'))))
            save(OUT/'shot10-progress.json', dict(turns=takes, confirmation=confirmation))
            occupied_slot = stored[dg_key]['gate']['what_moved'][0]['after']
            takes.append(_turn(copilot, model, 'Can the selected cargo move into the selected slot? Investigate in the sandbox and explain the result before any change.',
                _request(tools, box='QZXU0000288', slot=occupied_slot, limited=('try_move','check_plan','list_limits'))))
        shot = dict(schema_version='stowlm.copilot-shot10/1', synthetic=True,
                    label='Recorded real-tool Spark conversation on pinned lattice hero; offline playback.',
                    reader='Unadapted cloud Spark reader; no final reader release present at freeze.',
                    snapshot=snapshot, turns=takes, host_confirmation=confirmation,
                    native_evidence_files=[str((OUT/'native'/(key+'.json')).relative_to(HERE)) for key in stored],
                    model_calls=model.calls)
        save(OUT/'shot10.json', shot)
        target = stored[dg_key]
        save(OUT/'shot10-facts.json', dict(schema_version='stowlm.copilot-shot10-facts/1', synthetic=True,
             conversation=str((OUT/'shot10.json').relative_to(HERE/'fixtures')), reader=shot['reader'], instance_sha256=snapshot['hero']['instance_sha256'],
             base_lifts=stored[base_key]['lifts'], selected_lifts=target['lifts'], lift_delta=target['gate']['lift_delta'],
             selected_plan_sha256=target['gate']['plan_sha256'], confirmed_instruction=choice,
             alternatives=[dict(id=option['id'], label='checked', lifts=stored[key]['lifts'],
                                plan_sha256=stored[key]['gate']['plan_sha256'], sentences=option['sentences'])
                           for option,key in zip(alternatives,(dg_key,close_key))],
             proof=stored[refusal_key]['proof'], frames_checked=target['gate']['gauges']['n_frames'],
             what_moved=target['gate']['what_moved'],
             observed_acceptance=[t['turn']['plan_sha256'] for t in takes if t.get('turn') and t['turn'].get('plan_sha256')],
             facts_label='Native tool facts; observed conversation acceptance listed separately. Costs are weighted nonproductive lifts including projected later-port lifts.'))
        if tools._active_job:
            drain_job(tools, OUT/'shot10-background-terminal.json')

    record_take()
    reader.purpose = purpose('hero-final','reader')

    results, jobs = [], []
    scenarios = [s for s in load_scenarios() if s['id'] not in skip_scenarios]
    # Miss is last so its native worker cannot block independent hero evidence.
    order = [s for s in scenarios if s['goal'] != 'lookup_miss_background_status'] + [s for s in scenarios if s['goal'] == 'lookup_miss_background_status']
    marker = OUT/'frozen-first-started.json'
    with marker.open('x') as f:
        json.dump(dict(created_utc=datetime.now(timezone.utc).isoformat(), execution_order=[s['id'] for s in order]), f, indent=2)
    for scenario in order:
        print('Fresh frozen scenario starting', scenario['id'], flush=True)
        goal, setup = scenario['goal'], scenario['setup']
        initial = prepared['original'] if 'original_binding_plan' in setup.get('plan','') else stored[dg_key]['plan'] if goal == 'grounded_box_explanation' else stored[base_key]['plan']
        tools = make(initial)
        setup_event = None
        if setup.get('selected_sentence'):
            expected = rows[dg_key]['canonical_rules'] if goal == 'confirmed_stored_plan' else [{'schema_version':'stowlm.reader/2','constraint':{'type':'max_tiers','bay':9,'zone':'hold','max_tiers':2}}]
            setup_event = setup_read(tools, setup['selected_sentence'], expected)
        if goal == 'grounded_box_explanation':
            setup_event = setup_read(tools, rows[dg_key]['sentences'][0], rows[dg_key]['canonical_rules'])
        if scenario.get('documents'):
            original_context = tools.context
            tools.context = lambda: dict(original_context(), documents=scenario['documents'])
        model = Model(session, purpose('hero-final',scenario['id']))
        copilot = Copilot(tools, model, max_steps=10)
        turns = []
        if setup_event and setup_event.get('status') == 'unavailable':
            turns.append(dict(trace=[], turn=None, completion_status='unavailable'))
        for message in scenario.get('messages', [scenario.get('message')]):
            if setup_event and setup_event.get('status') == 'unavailable':
                break
            selected = '\n'.join(setup['selected_sentences']) if setup.get('selected_sentences') else None
            limited = setup.get('authorized_capabilities', setup.get('authorized_actions'))
            request = _request(tools, selected=selected, box=setup.get('selected_box'), limited=limited)
            turns.append(_turn(copilot, model, message, request))
            if tools._active_job:
                jobs.append(tools)
            save(OUT/(scenario['id']+'.json'), dict(id=scenario['id'], setup_event=setup_event, turns=turns))
        evidence = dict(native_gates=native, initial_plan_sha256=sha256_json(initial), selected_box=setup.get('selected_box'))
        score = score_hero_scenario(scenario, turns, fixture_evidence=evidence)
        value = dict(id=scenario['id'], setup_event=setup_event, turns=turns, fixture_evidence=persist_evidence(evidence), score=score)
        results.append(value)
        save(OUT/(scenario['id']+'.json'), value)
        save(OUT/'fresh-results.json', dict(results=results, summary=score_hero_campaign([r['score'] for r in results]),
             behavior=score_behavior([turn for row in results for turn in row['turns']])))
        print('Fresh frozen scenario complete', scenario['id'], score.get('achieved'), score.get('reasons'), flush=True)
        if tools._active_job and goal != 'lookup_miss_background_status':
            drain_job(tools, OUT/(scenario['id']+'-background-terminal.json'))
    # Do not let the slow lookup-miss job block the independently recorded take.
    for tools in jobs:
        drain_job(tools, OUT/'background-terminal.json')
    save(OUT/'calls.json', dict(model_calls=session.calls, measured_cost_usd=str(sum((Decimal(c['cost_usd']) for c in session.calls),Decimal(0)))))
    save(OUT/'finished.json', dict(created_utc=datetime.now(timezone.utc).isoformat(), spend=audit()))
    print('Hero recording finished', flush=True)
