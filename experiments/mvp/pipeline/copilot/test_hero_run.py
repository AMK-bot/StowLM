import json
from types import SimpleNamespace

import pytest

from experiments.mvp.pipeline.contract.schema import sha256_file
from experiments.mvp.pipeline.copilot import hero_run
from experiments.mvp.pipeline.copilot.authorization import build_authorization


def test_final_freeze_rejects_changed_runtime_before_any_paid_call(tmp_path):
    (tmp_path/'fixtures').mkdir()
    source, scenarios = tmp_path/'agent.py', tmp_path/'fixtures/hero-scenarios.json'
    source.write_text('original')
    scenarios.write_text('{}')
    marker = dict(files={'agent.py':sha256_file(source)}, scenarios_sha256=sha256_file(scenarios))
    hero_run.validate_freeze(marker, root=tmp_path)
    source.write_text('changed')
    with pytest.raises(RuntimeError, match='source changed'):
        hero_run.validate_freeze(marker, root=tmp_path)


def test_pure_operational_request_has_no_selected_reader_sentence():
    tools = SimpleNamespace(names=('check_plan','read_instruction','plan_lookup'), context=lambda: {'confirmed_rules':[]})
    request = hero_run._request(tools)
    auth = build_authorization('Find an available stored plan.', context=tools.context(), request=request)
    assert request.selected_sentence is None
    assert not auth.allows('read_instruction', {'sentence':'Find an available stored plan.'})


def test_model_action_is_durable_before_dispatch(monkeypatch, tmp_path):
    monkeypatch.setattr(hero_run, 'OUT', tmp_path)
    response = SimpleNamespace(text='{"tool":"check_plan","arguments":{}}', request_id='test-action')
    options = []
    def complete(*args, **kwargs):
        options.append(kwargs)
        return response
    session = SimpleNamespace(calls=[], complete=complete)
    model = hero_run.Model(session, 'offline')
    assert model.complete('prompt') == response.text
    assert json.loads((tmp_path/'raw-actions/test-action.json').read_text())['raw'] == response.text
    assert options[0]['max_output_tokens'] == 3072


def test_native_receipt_records_persisted_source_hash_and_path(tmp_path):
    gate = dict(plan_sha256='a'*64)
    path = tmp_path/'gate.json'
    path.write_text(json.dumps(gate))
    event = hero_run._event(path, gate)
    assert event['source_sha256'] == sha256_file(path)
    assert event['native'] and event['fresh']


def test_native_score_inputs_round_trip_from_pinned_refs_without_timeline_copies(tmp_path):
    gate = dict(plan_sha256='a'*64, gauges={'frames':[{'step':0}]})
    path = tmp_path/'gate.json'
    path.write_text(json.dumps({'gate':gate}))
    event = dict(native=True,fresh=True,plan_sha256='a'*64,source_file='gate.json',source_sha256=sha256_file(path),gate=gate)
    persisted = hero_run.persist_evidence({'native_gates':[event], 'initial_plan_sha256':'a'*64})
    assert 'gate' not in persisted['native_gates'][0]
    assert hero_run.load_evidence(persisted,root=tmp_path)['native_gates'][0]['gate'] == gate
    path.write_text('{}')
    with pytest.raises(ValueError, match='evidence changed'):
        hero_run.load_evidence(persisted,root=tmp_path)


def test_host_gate_cache_is_bound_to_current_engine_physics_and_clean_state(monkeypatch):
    from app import stage_physics
    from experiments.mvp.pipeline.lattice import gates
    from stowage import frozen
    monkeypatch.setattr(stage_physics,'physics_drift',lambda:None)
    monkeypatch.setattr(stage_physics,'physics_epoch',lambda:'current')
    monkeypatch.setattr(gates,'engine_identity',lambda:{'passed':True,'version':'current'})
    monkeypatch.setattr(frozen,'active_stubs',lambda:False)
    gate = {'engine_identity':{'passed':True,'version':'current'},'gauges':{'physics':{'physics_epoch':'current'}}}
    assert hero_run.gate_is_current(gate)
    gate['gauges']['physics']['physics_epoch'] = 'expired'
    assert not hero_run.gate_is_current(gate)
    gate['gauges']['physics']['physics_epoch'] = 'current'
    gate['engine_identity']['version'] = 'expired'
    assert not hero_run.gate_is_current(gate)
    gate['engine_identity']['version'] = 'current'
    monkeypatch.setattr(stage_physics,'physics_drift',lambda:'changed implementation')
    assert not hero_run.gate_is_current(gate)


def test_independent_case_waits_for_its_job_without_an_extra_model_decision(tmp_path):
    states = iter([{'status':'running','elapsed_s':0.5},{'status':'completed','elapsed_s':1.5}])
    tools = SimpleNamespace(_active_job='actual-job',job_manager=SimpleNamespace(poll=lambda job:next(states)))
    calls, pauses = [], []
    def call(name,args):
        calls.append((name,args))
        tools._active_job = None
        return {'status':'no_checked_plan','job_id':'actual-job','passed':False}
    tools.call = call
    receipt = hero_run.drain_job(tools,tmp_path/'terminal.json',pause=pauses.append,announce=lambda *args,**kwargs:None)
    assert pauses == [30] and calls == [('wait_replan',{'job_id':'actual-job'})]
    assert receipt == json.loads((tmp_path/'terminal.json').read_text())
    assert tools._active_job is None
