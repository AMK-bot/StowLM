"""Worker seams use generated fixtures and fakes; never start engine search or a model."""
from contextlib import nullcontext
import hashlib
import json
import sys
import threading
from types import SimpleNamespace

import pytest

from app import functional_worker as worker


@pytest.fixture
def call(tmp_path):
    path = tmp_path / 'functional' / 'calls' / 'c20261103-120000-abcdef'
    path.mkdir(parents=True)
    worker.write_json(path / 'request.json', {'ship': 'feeder_500', 'seed': 87654321})
    worker.write_json(path / 'base-instance.json', {'id': 'fresh-synthetic-test', 'load_list': ['SYNTH-1'], 'containers': [{'id': 'SYNTH-1'}]})
    worker.write_json(path / 'context.json', {'id': 'fresh-synthetic-test', 'bays': [1], 'max_tier': 3})
    worker.write_json(path / 'envelope.json', {'in_envelope': True, 'reasons': []})
    (path / 'unused').write_text('SYNTHETIC DOCUMENT\n')
    (path / 'papers').mkdir()
    (path / 'papers' / '1-list.csv').write_text('SYNTHETIC DOCUMENT\n')
    (path / 'papers' / '1-booking.csv').write_text('SYNTHETIC DOCUMENT\n')
    return path


def stages(call):
    return [json.loads(line) for line in (call / 'stages.jsonl').read_text().splitlines()]


def fake_module(monkeypatch, name, **values):
    if name == 'app.functional_models' and 'merge_intake_views' not in values:
        values['merge_intake_views'] = lambda views: {key: value for key, value in views[0].items() if key != 'booking_ref'}
    if name == 'app.functional_models' and 'spend_status' not in values:
        values['spend_status'] = lambda: {'available': True, 'cap_usd': 4.2, 'spent_usd': 0, 'holds_usd': 0}
    if name == 'app.functional_models' and callable(values.get('read_papers')):
        import inspect
        original = values['read_papers']
        parameters = inspect.signature(original).parameters
        accepts_grant = 'grant' in parameters or any(value.kind == inspect.Parameter.VAR_KEYWORD for value in parameters.values())

        def read_with_grant(documents, base, *, grant=None):
            return original(documents, base, grant=grant) if accepts_grant else original(documents, base)

        values['read_papers'] = read_with_grant
    module = SimpleNamespace(**values)
    if name == 'app.functional_models':
        from uuid import uuid4
        allocations, lock = {}, threading.Lock()

        def grant_call(*, call_id=None, booking_ref=None):
            with lock:
                if len(allocations) == 6:
                    return None
                identity = uuid4().hex
                grant = SimpleNamespace(id=identity, launch_id='fake-launch',
                                        ledger_path=f'/private/tmp/fake-booking-{identity}.jsonl',
                                        cap_usd=1.4, call_id=call_id, booking_ref=booking_ref)
                allocations[identity] = grant
                return grant

        def release(grant):
            with lock:
                assert allocations.pop(grant.id) is grant

        if 'grant_call' not in values:
            module.grant_call = grant_call
        if 'release' not in values:
            module.release = release
    monkeypatch.setitem(sys.modules, name, module)
    return module


def fake_plan(monkeypatch, callback):
    fake_module(monkeypatch, 'experiments.mvp.pipeline.runner', run_packet=callback)
    monkeypatch.setattr(worker, '_runtime_scope', lambda _: nullcontext())
    monkeypatch.setattr(worker, '_admission_scope', lambda _: nullcontext())


def cold_worker_probe(call, source):
    """Runner imports have process-start effects; never load them in pytest."""
    import os
    from pathlib import Path
    import subprocess
    import textwrap

    parent_environment = dict(os.environ)
    prefixes = ('experiments.mvp.pipeline.runner', 'experiments.mvp.pipeline.package')
    parent_modules = {name: module for name, module in sys.modules.copy().items()
                      if any(name == prefix or name.startswith(prefix + '.') for prefix in prefixes)}
    pipeline = sys.modules.get('experiments.mvp.pipeline')
    missing = object()
    parent_attributes = {name: getattr(pipeline, name, missing) for name in ('runner', 'package')}
    environment = dict(os.environ)
    environment.setdefault('UV_CACHE_DIR', str(call / 'uv-cache'))
    for name in ('MODEL_API_KEY', 'SPARK_CREDIT_READY', 'STOWLM_ACCEPT_PAID_CALLS'):
        environment.pop(name, None)
    result = subprocess.run(
        ['uv', 'run', '--offline', '--no-sync', 'python', '-c', textwrap.dedent(source), str(call)],
        cwd=Path(worker.__file__).resolve().parents[1], env=environment,
        capture_output=True, text=True, timeout=30,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert dict(os.environ) == parent_environment
    assert {name: module for name, module in sys.modules.copy().items()
            if any(name == prefix or name.startswith(prefix + '.') for prefix in prefixes)} == parent_modules
    assert sys.modules.get('experiments.mvp.pipeline') is pipeline
    assert all(getattr(pipeline, name, missing) is value for name, value in parent_attributes.items())


def test_atomic_stage_append_preserves_concurrent_writers(call):
    threads = [threading.Thread(target=worker.append_stage, args=(call, 'reader'), kwargs={'notes': str(n)}) for n in range(20)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    rows = stages(call)
    assert len(rows) == 20
    assert {row['notes'] for row in rows} == set(map(str, range(20)))
    assert all(set(row) == set(worker.STAGE_FIELDS) for row in rows)
    assert not list(call.glob('.stages.jsonl-*'))


@pytest.mark.parametrize('in_envelope', [True, False])
def test_generate_uses_fresh_case_and_stops_outside_envelope(call, monkeypatch, in_envelope):
    observed = []
    gate = dict(in_envelope=in_envelope, reasons=[] if in_envelope else [dict(message='Measured 80 %; limit 70 %.')])
    case = dict(seed=87654321, envelope=gate)
    fake_module(monkeypatch, 'app.functional_cases', generate_case=lambda request, directory: observed.append((request, directory)) or case)
    assert worker.run_step('generate', call) == 0
    assert observed == [({'ship': 'feeder_500', 'seed': 87654321}, call)]
    state = worker.read_json(call / 'worker-state.json')
    assert state['state'] == ('needs_papers' if in_envelope else 'out_of_envelope')
    assert worker.read_json(call / 'case.json') == case
    assert [row['stage'] for row in stages(call)] == ['generate', 'envelope', 'generate']


@pytest.mark.parametrize('complete', [True, False])
def test_intake_reads_papers_and_retains_questions(call, monkeypatch, complete):
    documents = [dict(name='Synthetic list.csv', media_type='text/csv', path=str(call / 'papers' / '1-list.csv'))]
    worker.write_json(call / 'worker-control.json', {'documents': documents, 'answers': {}})
    result = dict(mode='live', rows=[], questions=[] if complete else [{'id': 'missing.mass'}], complete=complete,
                  cost_usd=.002, model='muse-spark-1.3')
    seen = []
    fake_module(monkeypatch, 'app.functional_models',
                read_papers=lambda docs, base: seen.append((docs, base)) or result, answer_papers=None)
    assert worker.run_step('intake', call) == 0
    assert seen == [(documents, call / 'base-instance.json')]
    persisted = worker.read_json(call / 'intake.json')
    assert {key: persisted[key] for key in result} == result
    assert worker.read_json(call / 'worker-state.json')['state'] == ('assembling' if complete else 'needs_answers')
    assert stages(call)[-1]['cost_usd'] == 0
    assert sum(row.get('cost_usd') or 0 for row in stages(call)) == .002


def test_intake_answers_reuse_original_intake(call, monkeypatch):
    original = dict(complete=False, questions=[{'id': 'missing.mass'}])
    worker.write_json(call / 'intake.json', original)
    worker.write_json(call / 'worker-control.json', {'answers': {'missing.mass': 20000}, 'use_generated_papers': True})
    seen = []
    fake_module(monkeypatch, 'app.functional_models', read_papers=None,
                answer_papers=lambda before, answers: seen.append((before, answers)) or dict(complete=True, rows=[]))
    assert worker.run_step('intake', call) == 0
    assert seen == [(original, {'missing.mass': 20000})]
    assert all(row['stage'] == 'answers' and row['who'] == "Planner's answers"
               and row['where'] == 'this laptop' and row['cost_usd'] == 0 for row in stages(call))


def test_generated_load_list_is_explicitly_skipped(call, monkeypatch):
    fake_module(monkeypatch, 'app.functional_models', read_papers=None, answer_papers=None)
    worker.write_json(call / 'worker-control.json', {'use_generated_load_list': True})
    assert worker.run_step('intake', call) == 0
    assert worker.read_json(call / 'intake.json')['skipped'] is True
    assert stages(call)[-1]['status'] == 'skipped'
    assert stages(call)[-1]['mode'] == 'skipped'


def test_unavailable_has_plain_reason_and_trace_only_in_log(call, monkeypatch, capsys):
    class Unavailable(RuntimeError):
        stage, reason = 'spark', "MODEL_API_KEY isn't set in this launch"

    def unavailable(*_):
        raise Unavailable('private provider details')

    fake_module(monkeypatch, 'app.functional_models', read_papers=unavailable, answer_papers=None)
    worker.write_json(call / 'worker-control.json', {'documents': [{'name': 'Synthetic.csv', 'media_type': 'text/csv', 'path': str(call / 'unused')}]})
    assert worker.run_step('intake', call) == 1
    state = worker.read_json(call / 'worker-state.json')
    assert state['message'] == Unavailable.reason
    assert 'private provider details' not in json.dumps(state)
    assert 'Traceback' in capsys.readouterr().err
    assert stages(call)[-1]['status'] == 'unavailable'


def test_assemble_builds_packet_context_and_envelope_without_sentences(call, monkeypatch):
    data = {'id': 'fresh-synthetic-test', 'containers': []}
    seen = []
    monkeypatch.setattr(worker, '_runtime_scope', lambda _: nullcontext())
    fake_module(monkeypatch, 'experiments.mvp.pipeline.runner',
                assemble=lambda packet: seen.append(packet) or SimpleNamespace(model_dump=lambda **_: data))
    fake_module(monkeypatch, 'experiments.mvp.pipeline.runner.envelope', check=lambda _: {'in_envelope': True})
    fake_module(monkeypatch, 'experiments.mvp.reader.schema', context_from_instance=lambda _: {'id': data['id']})
    assert worker.run_step('assemble', call) == 0
    packet = seen[0]
    assert packet['standing'] == '' and packet['voyage_sentences'] == [] and packet['load_list'] is None
    assert packet['base_instance']['sha256'] == hashlib.sha256((call / 'base-instance.json').read_bytes()).hexdigest()
    assert worker.read_json(call / 'assembled-instance.json') == data
    assert worker.read_json(call / 'context.json') == {'id': data['id']}
    assert worker.read_json(call / 'worker-state.json')['state'] == 'ready'


def test_packet_preserves_intake_rows_and_receipt(call):
    worker.write_json(call / 'intake.json', {'complete': True, 'rows': [{'id': 'SYNTH-1'}], 'receipt_sha256': 'a'*64})
    packet = worker._packet(call, {'profile': 'standard'})
    assert packet['load_list'] == dict(origin='intake', rows=[{'id': 'SYNTH-1'}], intake_receipt_sha256='a'*64)
    worker.write_json(call / 'intake.json', {'complete': False})
    with pytest.raises(ValueError, match='Answer'):
        worker._packet(call, {})


@pytest.mark.parametrize('sentence', ['', 'Unexpected text'])
def test_no_instruction_parser_refuses_any_text(sentence):
    from experiments.mvp.reader.schema import context_from_instance
    from stowage.model.fixtures import build_instance
    context = context_from_instance(build_instance())
    answer, evidence = worker._EmptyBriefParser().parse(sentence, context)
    assert answer.refused is True
    assert evidence['parser'] == 'empty_brief'


@pytest.mark.parametrize('profile,cap', [('live', 720), ('standard', 1800)])
def test_plan_empty_brief_has_no_instruction_and_exact_deadline(call, monkeypatch, profile, cap):
    seen = {}

    def run(packet, **kwargs):
        seen.update(packet=packet, **kwargs)
        return dict(status='no_checked_plan', message='Search found no checked plan.', passed=False, events=[])

    fake_plan(monkeypatch, run)
    worker.write_json(call / 'worker-control.json', {'instruction': 'none', 'profile': profile})
    assert worker.run_step('plan', call) == 0
    assert seen['packet']['standing'] == '' and seen['packet']['voyage_sentences'] == []
    assert isinstance(seen['parser'], worker._EmptyBriefParser)
    assert seen['engine_timeout_s'] == cap
    assert seen['confirmation_source'] == worker.SOURCE
    assert seen['confirm']({'sentence': 'x', 'parsed': {}}) is False
    assert worker.read_json(call / 'worker-state.json')['state'] == 'done'
    assert worker.outcome_view(call)['first_checked_wall_s'] is None
    assert "isn't a proof" in worker.outcome_view(call)['message']


def test_plan_confirmed_uses_exact_sentence_rule_and_ship(call, monkeypatch):
    from app.v6 import _rule_digest, _sha256
    answer = dict(schema_version='stowlm.reader/2', constraint={'type': 'close_bay', 'bay': 1})
    reading = dict(id='r1', status='constraint_ready', sentence='Close bay 1.', answer=answer, origin='typed',
                   evidence={'model': 'muse-glimmer-30b', 'adapter_sha256': 'a'*64})
    confirmation = dict(reading='r1', sentence_sha256=_sha256(reading['sentence']), rule_sha256=_rule_digest(answer), source=worker.SOURCE)
    worker.write_json(call / 'readings.json', [reading])
    worker.write_json(call / 'confirmation.json', confirmation)
    seen = {}

    def run(packet, **kwargs):
        seen.update(packet=packet, **kwargs)
        assert kwargs['confirm']({'sentence': reading['sentence'], 'parsed': answer}) is True
        assert kwargs['confirm']({'sentence': 'Changed text', 'parsed': answer}) is False
        other = dict(schema_version='stowlm.reader/2', constraint={'type': 'close_bay', 'bay': 2})
        assert kwargs['confirm']({'sentence': reading['sentence'], 'parsed': other}) is False
        assert kwargs['parser'].fixed.context_id == 'fresh-synthetic-test'
        return dict(status='checked_plan', passed=True, events=[])

    fake_plan(monkeypatch, run)
    assert worker.plan(call, {'instruction': 'confirmed'})['passed'] is True
    assert seen['packet']['voyage_sentences'] == [{'text': 'Close bay 1.', 'origin': 'typed'}]
    confirmation['rule_sha256'] = 'b'*64
    worker.write_json(call / 'confirmation.json', confirmation)
    with pytest.raises(ValueError, match='exact reading'):
        worker.plan(call, {'instruction': 'confirmed'})


def test_plan_outside_envelope_never_calls_runner(call, monkeypatch):
    fake_plan(monkeypatch, lambda *_, **__: pytest.fail('runner must not start'))
    worker.write_json(call / 'envelope.json', {'in_envelope': False})
    assert worker.run_step('plan', call) == 1
    assert 'outside' in worker.read_json(call / 'worker-state.json')['message']
    assert not (call / 'run').exists()


def test_receipt_stage_views_preserve_measurements_and_no_invented_check(call):
    measurements = dict(mode='live', model=None, adapter_sha256=None, wall_s=12.5, cost_usd=0,
                        input_sha256='a'*64, output_sha256='b'*64, notes='receipt notes')
    worker.write_json(call / 'admission.json', {'waived': 'no fleet telemetry', 'load1': 8.5})
    outcome = dict(receipt={'steps': [dict(stage=stage, **measurements) for stage in ('engine', 'checker', 'ladder')]},
                   evidence={'checker': {'passed': False}}, status='relaxed_plan', message='Relaxed plan: gives up preferences.',
                   package={'files': []}, events=[dict(stage='package', kind='stage_start', t_s=14, data={}),
                                                dict(stage='package', kind='stage_end', t_s=16, data={'writer': 'package.write_package'})])
    worker._runner_stages(call, outcome)
    rows = stages(call)
    assert [row['stage'] for row in rows] == ['engine', 'checker', 'ladder', 'package']
    assert rows[0]['wall_s'] == 12.5 and rows[0]['input_sha256'] == 'a'*64
    assert 'no fleet telemetry' in rows[0]['notes'] and '8.5' in rows[0]['notes']
    assert '"passed": false' in rows[1]['notes']
    assert rows[-1]['wall_s'] == 2
    before = len(rows)
    worker._runner_stages(call, {'status': 'error', 'events': [], 'evidence': {}})
    assert len(stages(call)) == before


def test_outcome_view_uses_package_receipt_coverage_and_timing(call):
    receipt = dict(checker={'active_rules': ['real.rule'], 'inactive_rules': ['inactive.rule']})
    outcome = dict(status='checked_plan', passed=True, level=0, label='live', message='Passes the full checker.', receipt={'steps': []},
                   evidence={}, timing={'wall_s': 31.2, 'first_checked_wall_s': 21.1}, lifts=5)
    worker.write_json(call / 'run' / 'outcome.json', outcome)
    worker.write_json(call / 'run' / 'package' / 'receipt.json', receipt)
    view = worker.outcome_view(call)
    assert view['active_rules'] == ['real.rule'] and view['inactive_rules'] == ['inactive.rule']
    assert view['wall_s'] == 31.2 and view['first_checked_wall_s'] == 21.1
    assert view['package_files'] == ['receipt.json']
    assert view['receipt_sha256'] == worker._digest({'steps': []})


@pytest.mark.parametrize('fresh', [False, True])
def test_admission_waives_only_missing_fleet_without_inventing_capacity(call, monkeypatch, fresh):
    from app import v6
    from types import ModuleType
    original_reading = dict(mask=255, enabled_cores=8, load1=20, reading_age_s=1, waited_s=0)
    admission = ModuleType('experiments.mvp.pipeline.runner.admission')
    admission.STATE = call / 'fleet.json'
    if fresh:
        worker.write_json(admission.STATE, {'t': 1})
    waits = []
    admission.wait_for_capacity = lambda **kwargs: waits.append(kwargs) or original_reading
    admission.call_admitted = lambda *args, **kwargs: 'normal-call'
    original_wait = admission.wait_for_capacity
    fake_module(monkeypatch, 'experiments.mvp.pipeline.runner', admission=admission)
    monkeypatch.setitem(sys.modules, admission.__name__, admission)
    waiver = []
    monkeypatch.setattr(v6, '_waive_admission_without_a_fleet', lambda: waiver.append(True))
    with worker._admission_scope(call):
        reading = admission.wait_for_capacity()
        assert bool(waits) is fresh
        if fresh:
            assert reading == original_reading
        else:
            assert set(reading) == {'waived', 'load1'}
            assert reading['waived'] == 'no fleet telemetry'
        assert worker.read_json(call / 'admission.json') == reading
    assert admission.wait_for_capacity is original_wait
    assert waiver == [True]


def test_failed_engine_event_is_visible_without_a_checker_claim(call):
    outcome = {'status': 'error', 'events': [
        {'stage': 'engine', 'kind': 'stage_start', 't_s': 5, 'data': {}},
        {'stage': 'engine', 'kind': 'error', 't_s': 8, 'data': {'message': 'Engine stopped.'}},
    ]}
    worker._runner_stages(call, outcome)
    row, = stages(call)
    assert row['stage'] == 'engine' and row['status'] == 'failed' and row['wall_s'] == 3
    assert row['input_sha256'] is None


@pytest.mark.parametrize('level,label', [
    (1, 'Relaxed plan: gives up preferences'),
    (2, 'Relaxed plan: gives up stowage practice'),
    (3, 'Relaxed plan: gives up bookings or leaves boxes ashore'),
    (4, "Breaks a class or statutory limit: needs a person's sign-off"),
    (5, 'Not safe to sail: shown only to say what would have to change'),
])
def test_relaxed_outcome_names_level_and_partial_aboard(call, level, label):
    worker.write_json(call / 'run' / 'outcome.json', dict(
        status='relaxed_plan', passed=False, level=level, boxes_ashore=['SYNTH-2'],
        instance={'load_list': ['SYNTH-1', 'SYNTH-2']}, broken_rules=[{'rule': 'measured.rule'}]))
    view = worker.outcome_view(call)
    assert view['label'] == label + '; partial, 50.0 % aboard'
    assert view['passed'] is False and view['broken_rules'] == [{'rule': 'measured.rule'}]


def generated_manifest(call, roles=None):
    roles = roles or ['booking', 'shipping_instructions', 'vgm', 'dg_declaration',
                      'gate_slip', 'reefer_list', 'oog_list', 'unspecified']
    documents = []
    for role in roles:
        name = f'{role}.csv'
        path = call / 'papers' / 'generated' / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text('SYNTHETIC DOCUMENT\ncontainer_id\nSYNTH-1\n')
        documents.append(dict(path=f'papers/generated/{name}', media_type='text/csv', role=role,
                              booking_ref='SYNTH-BOOKING-001', complete_booking_list=role in {'reefer_list', 'oog_list', 'dg_declaration'},
                              container_ids=['SYNTH-1'],
                              synthetic=True))
    worker.write_json(call / 'papers' / 'generated' / 'manifest.json', documents)
    return documents


def test_generated_papers_manifest_metadata_reaches_live_intake(call, monkeypatch):
    manifest = generated_manifest(call)
    observed = []
    result = dict(complete=True, rows=[], model='muse-spark-1.3', cost_usd=.005)
    fake_module(monkeypatch, 'app.functional_models',
                read_papers=lambda docs, base: observed.append((docs, base)) or result, answer_papers=None)
    worker.write_json(call / 'worker-control.json', {'use_generated_papers': True, 'documents': [{'path': 'ignore-this-stale-upload'}]})
    assert worker.run_step('intake', call) == 0
    documents, base = observed[0]
    assert worker.read_json(base)['load_list'] == ['SYNTH-1']
    assert base == call / 'intake-bases' / 'b0001.json'
    assert len(documents) == len(manifest)
    assert {row['role'] for row in documents} == {row['role'] for row in manifest}
    for document, source in zip(documents, manifest):
        assert document['booking_ref'] == source['booking_ref']
        assert document['complete_booking_list'] is source['complete_booking_list']
        assert document['synthetic'] is True
        assert document['origin'] == 'generated' and document['label'] == 'Synthetic document'
        path = call / source['path']
        assert document['path'] == str(path.resolve())
        assert document['name'] == path.name
        assert document['bytes'] == path.stat().st_size
        assert document['sha256'] == hashlib.sha256(path.read_bytes()).hexdigest()
    assert worker.read_json(call / 'papers' / 'meta.json') == {'documents': documents}
    assert worker.read_json(call / 'papers.json') == {'documents': documents}
    assert stages(call)[-1]['status'] == 'done' and stages(call)[-1]['mode'] == 'live'


def test_upload_authority_metadata_is_preserved_without_filename_inference(call, monkeypatch):
    document = dict(name='booking.csv', media_type='text/csv', path=str(call / 'papers' / '1-booking.csv'),
                    role='reefer_list', booking_ref='EXPLICIT-BOOKING', complete_booking_list=True,
                    synthetic=True, bytes=50, sha256='a'*64, origin='uploaded')
    observed = []
    fake_module(monkeypatch, 'app.functional_models',
                read_papers=lambda docs, _: observed.append(docs) or {'complete': False}, answer_papers=None)
    worker.intake(call, {'documents': [document]})
    assert observed == [[document]]
    assert observed[0][0]['role'] == 'reefer_list'


@pytest.mark.parametrize('value', [None, {}, [], [None], ['text'], [{'path': 'papers/generated/no.csv', 'media_type': 'text/csv'}]])
def test_generated_papers_rejects_missing_or_malformed_manifest_and_files(call, value):
    worker.write_json(call / 'papers' / 'generated' / 'manifest.json', value)
    with pytest.raises(ValueError):
        worker.generated_papers(call)


@pytest.mark.parametrize('path', [
    '../escape.csv', '/private/tmp/escape.csv', 'papers/generated/../escape.csv',
    'papers/other.csv', 'base-instance.json', 'papers/generated/manifest.json',
    'papers\\generated\\booking.csv', 'papers/generated/\x00.csv',
])
def test_generated_papers_rejects_paths_outside_generated_directory(call, path):
    worker.write_json(call / 'papers' / 'generated' / 'manifest.json', [{'path': path, 'media_type': 'text/csv'}])
    with pytest.raises(ValueError):
        worker.generated_papers(call)


def test_generated_papers_rejects_escaping_symlinks(call):
    manifest = generated_manifest(call, ['booking'])
    path = call / manifest[0]['path']
    path.unlink()
    path.symlink_to(call / 'base-instance.json')
    with pytest.raises(ValueError, match='stay inside'):
        worker.generated_papers(call)


def test_generated_papers_rejects_manifest_symlink_outside_generated_directory(call):
    path = call / 'papers' / 'generated' / 'manifest.json'
    path.parent.mkdir(parents=True)
    path.symlink_to(call / 'request.json')
    with pytest.raises(ValueError, match='manifest must stay inside'):
        worker.generated_papers(call)


def test_generated_papers_rejects_missing_manifest(call):
    with pytest.raises(ValueError, match='manifest'):
        worker.generated_papers(call)


def test_document_metadata_defaults_do_not_infer_authority():
    assert worker.document_metadata({'name': 'VGM-booking-reefer.csv'}) == dict(
        role='unspecified', booking_ref=None, complete_booking_list=False, synthetic=False)


@pytest.mark.parametrize('document', [
    {'role': 'made_up'}, {'role': None}, {'role': ['booking']}, {'booking_ref': 5},
    {'complete_booking_list': 'true'}, {'synthetic': 1}, None,
])
def test_document_metadata_rejects_invalid_host_metadata(document):
    with pytest.raises(ValueError):
        worker.document_metadata(document)


def merge_booking_fakes(views):
    questions = []
    routes = {}
    children = []
    for index, view in enumerate(views):
        identity = f'b{index+1:04d}'
        children.append(dict(id=identity, view=view))
        for question in view.get('questions', []):
            public_id = f'{index}:{question["id"]}'
            questions.append(dict(question, id=public_id))
            routes[public_id] = dict(booking=identity, question=question['id'])
    return dict(mode='live', model='muse-spark-1.3', rows=[row for view in views for row in view.get('rows', [])],
                questions=questions, complete=all(view.get('complete') for view in views),
                cost_usd=sum(view.get('cost_usd', 0) for view in views),
                raw={'bookings': children, 'question_routes': routes},
                boxes=sum(len(view.get('rows', [])) for view in views))


def test_bookings_group_missing_refs_together_and_read_at_most_six_concurrently(call, monkeypatch):
    documents = [booking_document(call, f'B{index}') for index in range(6)]
    documents += [dict(name='no-ref.csv', media_type='text/csv', path=str(call / 'unused'), container_ids=['SYNTH-1']),
                  dict(name='null-ref.csv', media_type='text/csv', path=str(call / 'unused'), booking_ref=None, container_ids=['SYNTH-1'])]
    worker.write_json(call / 'worker-control.json', {'documents': documents})
    lock, first_wave = threading.Lock(), threading.Barrier(6)
    active = peak = entered = 0
    observed = []

    def read(group, base):
        nonlocal active, peak, entered
        with lock:
            active += 1
            entered += 1
            ordinal = entered
            peak = max(peak, active)
            observed.append(group)
        if ordinal <= 6:
            first_wave.wait(timeout=3)
        ref = group[0].get('booking_ref')
        with lock:
            active -= 1
        return dict(rows=[{'id': f'{ref}-BOX'}], questions=[], complete=True, boxes=1,
                    cost_usd=.01, raw={'source_booking': ref}, model='muse-spark-1.3')

    fake_module(monkeypatch, 'app.functional_models', read_papers=read, answer_papers=None,
                merge_intake_views=merge_booking_fakes)
    assert worker.run_step('intake', call) == 0
    assert peak == 6 and len(observed) == 7
    assert sorted(len(group) for group in observed) == [1, 1, 1, 1, 1, 1, 2]
    records = worker._load_booking_records(call)
    assert [record['booking_ref'] for record in records] == ['B0', 'B1', 'B2', 'B3', 'B4', 'B5', None]
    merged = worker.read_json(call / 'intake.json')
    assert merged['boxes'] == 7 and merged['complete'] is True
    assert merged['cost_usd'] == pytest.approx(.07)
    rows = stages(call)
    assert sum(row.get('cost_usd') or 0 for row in rows) == pytest.approx(.07)
    assert rows[-1]['cost_usd'] == 0 and 'adds no charge' in rows[-1]['notes']
    by_instance = {}
    for row in rows:
        if row['who'] == 'Intake booking schedule':
            assert '6 bookings at a time' in row['notes']
            continue
        by_instance.setdefault(row['instance_id'], []).append(row)
    assert len(by_instance) == 8
    assert all([row['status'] for row in group] == ['running', 'done'] for group in by_instance.values())
    assert all(group[0]['started_utc'] == group[1]['started_utc'] for group in by_instance.values())
    assert all('1 boxes' in group[-1]['who'] for group in by_instance.values() if 'booking' in group[0]['who'])


def test_booking_answers_resume_only_relevant_original_receipt_and_do_not_recharge(call, monkeypatch):
    original_a = dict(complete=False, rows=[{'id': 'SYNTH-A'}], questions=[{'id': 'mass'}], cost_usd=.01,
                      raw={'original_receipt': 'A'})
    original_b = dict(complete=False, rows=[{'id': 'SYNTH-B'}], questions=[{'id': 'mass'}], cost_usd=.02,
                      raw={'original_receipt': 'B'})
    records = [dict(booking_ref='A', view=original_a), dict(booking_ref='B', view=original_b)]
    worker.write_json(call / 'intake-bookings.json', {'bookings': records})
    worker.write_json(call / 'intake.json', merge_booking_fakes([dict(original_a, booking_ref='A'), dict(original_b, booking_ref='B')]))
    worker.write_json(call / 'worker-control.json', {'answers': {'1:mass': 20000}, 'use_generated_papers': True})
    observed = []

    def answer(view, answers):
        observed.append((view, answers))
        return dict(view, complete=True, questions=[])

    fake_module(monkeypatch, 'app.functional_models', read_papers=lambda *_: pytest.fail('must not reread papers'),
                answer_papers=answer, merge_intake_views=merge_booking_fakes)
    assert worker.run_step('intake', call) == 0
    assert observed == [(original_b, {'mass': 20000})]
    updated = worker._load_booking_records(call)
    assert updated[0]['view'] == original_a and updated[1]['view']['complete'] is True
    merged = worker.read_json(call / 'intake.json')
    assert merged['cost_usd'] == .03 and merged['complete'] is False
    assert worker.read_json(call / 'worker-state.json')['state'] == 'needs_answers'
    assert sum(row.get('cost_usd') or 0 for row in stages(call)) == 0
    assert any('Booking B' in row['notes'] for row in stages(call))
    assert not any('Booking A' in row['notes'] for row in stages(call))
    assert all(row['who'] == "Planner's answers" and row['where'] == 'this laptop'
               and row['model'] is None and row['mode'] == 'live' and row['cost_usd'] == 0
               for row in stages(call))


def test_booking_answers_reject_unknown_question_before_resuming_any_booking(call, monkeypatch):
    worker.write_json(call / 'intake-bookings.json', {'bookings': [{'booking_ref': None, 'view': {'complete': False}}]})
    worker.write_json(call / 'intake.json', {'questions': [{'id': 'known', 'original_id': 'known', 'booking_ref': None}]})
    fake_module(monkeypatch, 'app.functional_models', read_papers=None,
                answer_papers=lambda *_: pytest.fail('an unknown question must not reach intake'),
                merge_intake_views=merge_booking_fakes)
    with pytest.raises(ValueError, match='current cargo-paper question'):
        worker.intake(call, {'answers': {'unknown': 4}})


def test_cut_off_model_answer_is_failed_and_keeps_other_booking_receipts(call, monkeypatch, capsys):
    class ModelError(RuntimeError):
        stage = 'spark'
        reason = "Muse Spark's answer was cut off at its output limit (24576 tokens); nothing from it was used."

    first = threading.Event()

    def read(documents, base):
        if documents[0]['booking_ref'] == 'good':
            first.set()
            return {'rows': [{'id': 'SYNTH-1'}], 'complete': True, 'questions': [], 'cost_usd': .01}
        assert first.wait(timeout=3)
        raise ModelError('private response details')

    fake_module(monkeypatch, 'app.functional_models', read_papers=read, answer_papers=None,
                merge_intake_views=lambda *_: pytest.fail('a failed booking must not produce a merged load list'))
    worker.write_json(call / 'worker-control.json', {'documents': [booking_document(call, 'good'), booking_document(call, 'bad')]})
    assert worker.run_step('intake', call) == 1
    state = worker.read_json(call / 'worker-state.json')
    assert state['message'] == ModelError.reason
    assert 'private response' not in json.dumps(state)
    assert 'Traceback' in capsys.readouterr().err
    rows = stages(call)
    assert not any(row['status'] == 'unavailable' for row in rows)
    assert len([row for row in rows if row['status'] == 'failed']) == 2
    records = worker._load_booking_records(call)
    assert records[0]['view']['rows'] == [{'id': 'SYNTH-1'}] and records[1]['view'] is None
    assert sum(row.get('cost_usd') or 0 for row in rows if row.get('cost_scope') != 'cumulative_booking_total') == .01
    assert rows[-1]['cumulative_cost_usd'] == .01 and rows[-1]['where'] == 'Meta Model API, cloud'


def test_worker_routes_real_merged_question_schema_to_original_booking_receipt(call, monkeypatch):
    import importlib
    from copy import deepcopy
    from experiments.mvp.pipeline.contract.schema import IntakeResult, StageRecord
    models = importlib.import_module('app.functional_models')

    def view(booking, cost):
        record = StageRecord(stage='intake', mode='live', input_sha256='a'*64, output_sha256='b'*64,
                             wall_s=1, cost_usd=cost, notes=json.dumps({
                                 'intake_context': {'documents': [{'role': 'booking', 'booking_ref': booking}]}}))
        raw = IntakeResult(status='needs_answers', rows=[{'id': f'SYNTH-{booking}'}],
                           questions=[dict(id='mass', row=0, field='mass_kg', prompt='Supply synthetic mass.',
                                           reason='missing', candidates=[])], records=[record])
        return models._intake_view(raw, 1.)

    original_a, original_b = view('A', .01), view('B', .02)
    merged = models.merge_intake_views([original_a, original_b])
    question_id = next(qid for qid, route in merged['raw']['question_routes'].items() if route['booking'] == 'b0002')
    assert question_id.startswith('B::b0002::')
    worker.write_json(call / 'intake.json', merged)
    worker.write_json(call / 'intake-bookings.json', {'bookings': [
        {'booking_ref': 'A', 'view': original_a}, {'booking_ref': 'B', 'view': original_b}]})
    worker.write_json(call / 'worker-control.json', {'answers': {question_id: 20000}})
    observed = []

    def answer(before, answers):
        observed.append((before, answers))
        raw = deepcopy(before['raw'])
        raw.update(status='complete', questions=[])
        return models._intake_view(IntakeResult.model_validate(raw), .25)

    monkeypatch.setattr(models, 'answer_papers', answer)
    monkeypatch.setattr(models, 'read_papers', lambda *_: pytest.fail('answers must not call the model'))
    assert worker.run_step('intake', call) == 0
    assert observed == [(original_b, {'mass': 20000})]
    result = worker.read_json(call / 'intake.json')
    assert result['raw']['bookings'][0]['view'] == original_a
    assert result['raw']['bookings'][1]['view']['wall_s'] == .25
    assert result['wall_s'] == 1.25 and result['cost_usd'] == .03
    assert result['read_wall_s'] == 2 and result['read_cost_usd'] == .03
    assert all(route['booking'] == 'b0001' for route in result['raw']['question_routes'].values())
    assert sum(row.get('cost_usd') or 0 for row in stages(call)) == 0


@pytest.mark.parametrize('use_error_event', [False, True])
def test_engine_error_receipt_remains_failed_with_measured_hashes(call, use_error_event):
    measurement = dict(stage='engine', mode='live', input_sha256='a'*64, output_sha256='b'*64,
                       model=None, wall_s=19.7, cost_usd=0, notes='Measured engine attempt.')
    outcome = dict(status='error', passed=False, receipt={'steps': [measurement]},
                   evidence={} if use_error_event else {'engine_error': {'type': 'RuntimeError', 'message': 'Engine failed.'}},
                   events=[{'stage': 'engine', 'kind': 'error', 't_s': 21, 'data': {'message': 'Engine failed.'}}] if use_error_event else [])
    worker._runner_stages(call, outcome)
    row, = stages(call)
    assert row['stage'] == 'engine' and row['status'] == 'failed'
    assert row['wall_s'] == 19.7 and row['input_sha256'] == 'a'*64 and row['output_sha256'] == 'b'*64


@pytest.mark.parametrize('size_class', ['tiny', 'small', 'medium'])
def test_headline_envelope_excludes_1700_teu_sections_even_if_runner_passes(size_class):
    gate = dict(in_envelope=True, reasons=[], numbers={'ship': 'feeder_1700', 'physical_cell_fill': .6})
    case = dict(ship='feeder_1700', size_class=size_class,
                headline_envelope={'in_envelope': False, 'reasons': [{'test': 'ship', 'message': 'Old ship wording.'}]})
    actual = worker.app_envelope(case, gate)
    assert actual['in_envelope'] is False and actual['numbers'] == gate['numbers']
    assert actual['reasons'] == [dict(test='ship', measured='feeder_1700', limit=['feeder_500', 'feeder_800'],
                                      message='Ship 1,700 TEU; the envelope is 500–800 TEU feeders')]


def test_generate_obeys_headline_gate_instead_of_tiny_runner_gate(call, monkeypatch):
    case = dict(ship='feeder_1700', size_class='tiny', envelope={'in_envelope': True, 'reasons': []},
                headline_envelope={'in_envelope': False, 'reasons': []})
    fake_module(monkeypatch, 'app.functional_cases', generate_case=lambda *_: case)
    assert worker.run_step('generate', call) == 0
    state = worker.read_json(call / 'worker-state.json')
    assert state['state'] == 'out_of_envelope'
    assert 'Ship 1,700 TEU; the envelope is 500–800 TEU feeders' in state['message']


def test_plan_rechecks_headline_gate_before_starting_engine(call, monkeypatch):
    fake_plan(monkeypatch, lambda *_: pytest.fail('outside headline must not start engine'))
    worker.write_json(call / 'case.json', {'ship': 'feeder_1700', 'size_class': 'small'})
    worker.write_json(call / 'worker-control.json', {'instruction': 'none'})
    assert worker.run_step('plan', call) == 1
    assert worker.read_json(call / 'worker-state.json')['state'] == 'out_of_envelope'
    assert not (call / 'run').exists()


def test_packet_default_matches_standard_profile(call):
    assert worker._packet(call, {})['profile'] == 'standard'


def test_booking_sets_omit_global_sheet_and_scope_generated_booking_bases(call, monkeypatch):
    worker.write_json(call / 'base-instance.json', {'id': 'synthetic', 'load_list': ['SYNTH-A', 'SYNTH-B'],
                                                  'containers': [{'id': 'SYNTH-A'}, {'id': 'SYNTH-B'}]})
    documents = [dict(name='load-list.csv', path=str(call / 'papers/generated/load-list.csv'),
                      media_type='text/csv', role='unspecified', booking_ref=None, origin='generated')]
    for ref, cid in [('A', 'SYNTH-A'), ('B', 'SYNTH-B')]:
        path = call / 'papers' / 'generated' / f'{ref}-booking-note.txt'
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(f'SYNTHETIC DOCUMENT\nComplete container list for booking {ref}; 1 units.\n{cid}; ISO 22G1; VGM 18000 kg\n')
        documents.append(dict(name=path.name, path=str(path), media_type='text/plain', role='booking',
                              booking_ref=ref, origin='generated', synthetic=True))
    seen = []

    def read(group, base):
        payload = worker.read_json(base)
        seen.append((group, base, payload))
        return dict(rows=[{'id': payload['load_list'][0]}], complete=True, questions=[], boxes=1, cost_usd=.01)

    fake_module(monkeypatch, 'app.functional_models', read_papers=read, answer_papers=None,
                merge_intake_views=merge_booking_fakes)
    result = worker.intake(call, {'documents': documents})
    assert result['boxes'] == 2
    assert len(seen) == 2
    for group, base, payload in seen:
        assert len(group) == 1 and group[0]['role'] == 'booking'
        assert base.parent == call / 'intake-bases'
        assert payload['load_list'] == [f'SYNTH-{group[0]["booking_ref"]}']
        assert len(payload['containers']) == 2
    assert worker.read_json(call / 'base-instance.json')['load_list'] == ['SYNTH-A', 'SYNTH-B']


@pytest.mark.parametrize('roster,count', [(['SYNTH-1'], 2), (['UNKNOWN'], 1), (['SYNTH-1', 'SYNTH-1'], 2)])
def test_generated_booking_scope_rejects_bad_host_rosters_before_any_model_call(call, roster, count):
    path = call / 'papers' / 'generated' / 'booking-note.txt'
    path.parent.mkdir(parents=True)
    path.write_text('SYNTHETIC DOCUMENT\nComplete container list for booking B; ' + str(count) + ' units.\n' +
                    '\n'.join(cid + '; ISO 22G1; VGM 18000 kg' for cid in roster))
    document = dict(name=path.name, path=str(path), role='booking', booking_ref='B', origin='generated')
    with pytest.raises(ValueError):
        worker._read_bookings(call, [document], lambda *_: pytest.fail('invalid host roster must not call a model'))


def test_worker_masks_dynamic_environment_credential_in_python_native_output_and_errors(call, monkeypatch, capfd):
    import os
    import secrets
    credential = secrets.token_urlsafe(32)
    monkeypatch.setenv('MODEL_API_KEY', credential)

    def fails(request, directory):
        print('Python output: ' + credential)
        sys.stderr.write(credential[:13])
        sys.stderr.write(credential[13:] + '\n')
        os.write(1, credential[:7].encode())
        os.write(1, (credential[7:] + '\n').encode())
        os.write(2, ('native stderr: ' + credential + '\n').encode())
        raise ValueError('Provider echoed ' + credential)

    fake_module(monkeypatch, 'app.functional_cases', generate_case=fails)
    assert worker.run_step('generate', call) == 1
    captured = capfd.readouterr()
    output = captured.out + captured.err
    assert credential not in output
    assert '[REDACTED API KEY]' in output and 'Traceback' in output
    for path in call.rglob('*'):
        if path.is_file():
            assert credential.encode() not in path.read_bytes()
    assert worker.read_json(call / 'worker-state.json')['message'] == 'Provider echoed [REDACTED API KEY]'


def test_dependency_artifacts_with_credential_are_scrubbed_and_call_fails(call, monkeypatch, capfd):
    import secrets
    credential = secrets.token_urlsafe(32)
    monkeypatch.setenv('MODEL_API_KEY', credential)

    def generates(request, directory):
        escaped = ''.join('\\u%04x' % ord(char) for char in credential)
        (directory / 'dependency.json').write_text('{"echo":"' + escaped + '"}')
        (directory / 'dependency.txt').write_text('echo=' + credential)
        return dict(envelope={'in_envelope': True, 'reasons': []})

    fake_module(monkeypatch, 'app.functional_cases', generate_case=generates)
    assert worker.run_step('generate', call) == 1
    assert worker.read_json(call / 'worker-state.json')['state'] == 'failed'
    for path in call.rglob('*'):
        if path.is_file():
            assert credential.encode() not in path.read_bytes()
    assert worker.read_json(call / 'dependency.json')['echo'] == '[REDACTED API KEY]'
    assert credential not in ''.join(capfd.readouterr())


def test_multiple_answer_rounds_preserve_read_metrics_and_helper_timing_scopes(call, monkeypatch):
    original = dict(complete=False, rows=[{'id': 'SYNTH-1'}], questions=[{'id': 'mass'}],
                    wall_s=8.5, cost_usd=.04, model='muse-spark-1.3', raw={'original': True})
    worker.write_json(call / 'worker-control.json', {'documents': [dict(name='synthetic.txt', media_type='text/plain', path=str(call / 'unused'), booking_ref='B', container_ids=['SYNTH-1'])]})
    seen = []

    def answer(before, answers):
        seen.append(before)
        # Deliberately omit read metrics, as an older helper does. Its own wall
        # measurement remains untouched; it is not a second model reading.
        return {key: value for key, value in dict(before, wall_s=.125, questions=[{'id': 'mass'}], complete=False).items()
                if key not in ('read_wall_s', 'read_cost_usd', 'answer_wall_s')}

    fake_module(monkeypatch, 'app.functional_models', read_papers=lambda *_: original,
                answer_papers=answer, merge_intake_views=merge_booking_fakes)
    assert worker.run_step('intake', call) == 0
    first = worker.read_json(call / 'intake.json')
    assert first['read_wall_s'] == 8.5 and first['read_cost_usd'] == .04
    for index in range(2):
        worker.write_json(call / 'worker-control.json', {'answers': {'0:mass': 20000 + index}})
        assert worker.run_step('intake', call) == 0
        result = worker.read_json(call / 'intake.json')
        private = worker._load_booking_records(call)[0]['view']
        assert result['read_wall_s'] == 8.5 and result['read_cost_usd'] == .04
        assert private['read_wall_s'] == 8.5 and private['read_cost_usd'] == .04
        assert private['wall_s'] == .125
        assert result['cost_usd'] == .04
    assert len(seen) == 2
    assert sum(row.get('cost_usd') or 0 for row in stages(call)) == .04
    answers = [row for row in stages(call) if row['stage'] == 'answers']
    assert answers and all(row['who'] == "Planner's answers" and row['model'] is None
                           and row['where'] == 'this laptop' and row['cost_usd'] == 0 for row in answers)


def test_answer_helper_metrics_are_not_added_or_replaced_by_worker(call):
    original = dict(wall_s=8, read_wall_s=8, read_cost_usd=.04, cost_usd=.04,
                    answer_wall_s=.1, rows=[], questions=[])
    answer = dict(wall_s=8.3, read_wall_s=8, read_cost_usd=.04, cost_usd=.04,
                  answer_wall_s=.3, rows=[], questions=[])
    result = worker._booking_stage(call, 'B', 'answers', lambda *_: answer, original, {'mass': 1})
    assert result == answer


def test_runtime_scope_keeps_runner_fixture_root_immutable(call):
    cold_worker_probe(call, '''
        import json
        import os
        from pathlib import Path
        import sys
        from unittest.mock import patch
        from app import functional_worker as worker

        call = Path(sys.argv[1])
        assert 'experiments.mvp.pipeline.runner.core' not in sys.modules
        from experiments.mvp.pipeline.runner import core, runtime, admission
        runner_root, original_cache = core.HERE, admission.CACHE_ROOT
        original_vessel_cache = os.environ.get('STOWAGE_VESSEL_CACHE_DIR')
        configured = []
        with patch.object(runtime, 'configure_caches', lambda: configured.append(runtime.HERE)):
            with worker._runtime_scope(call):
                assert core.HERE == runner_root
                policy = json.loads((core.HERE / 'fixtures' / 'anytime-handover.json').read_text())
                assert 'anytime_policy' in policy
                assert admission.CACHE_ROOT == call.parent.parent / 'cache' / 'binding'
                assert os.environ['STOWAGE_VESSEL_CACHE_DIR'] == str(call.parent.parent / 'cache' / 'vessels')
        assert configured == [call.parent.parent]
        assert core.HERE == runner_root and admission.CACHE_ROOT == original_cache
        assert os.environ.get('STOWAGE_VESSEL_CACHE_DIR') == original_vessel_cache
    ''')


def test_runtime_signing_wrapper_overrides_runner_outer_key_path_only_for_writer(call):
    cold_worker_probe(call, '''
        from pathlib import Path
        import sys
        from unittest.mock import patch
        from app import functional_worker as worker

        call = Path(sys.argv[1])
        assert 'experiments.mvp.pipeline.runner.core' not in sys.modules
        from experiments.mvp.pipeline.runner import core, runtime
        from experiments.mvp.pipeline.package import receipt as signing
        runner_root, original_factory = core.HERE, core._package_writer
        expected = call.parent.parent / 'cache' / 'runner' / '.keys'
        observed = []

        def writer(outcome, destination):
            observed.append((signing.KEY_DIR, outcome, destination))
            signing._private_key(signing.KEY_DIR)
            return {'written': True}

        with patch.object(runtime, 'configure_caches', lambda: None), patch.object(core, 'write_package', writer):
            with worker._runtime_scope(call):
                wrapped = core._package_writer()
                # The real runner wraps the writer with its own key path.
                # The Functional wrapper must override that nested binding.
                with patch.object(signing, 'KEY_DIR', runner_root / '.keys'):
                    assert wrapped({'synthetic': True}, call / 'package-probe') == {'written': True}
                    assert signing.KEY_DIR == runner_root / '.keys'
        assert observed == [(expected, {'synthetic': True}, call / 'package-probe')]
        assert (expected / signing.KEY_NAME).is_file()
        assert core.HERE == runner_root and core._package_writer is original_factory
    ''')


def booking_document(call, ref, *, name=None, path_name=None):
    path = call / (path_name or f'{ref}.txt')
    path.write_text('SYNTHETIC DOCUMENT\nDeclared booking ' + ref + '\n')
    cid = 'SYNTH-' + ref
    base = worker.read_json(call / 'base-instance.json')
    if cid not in base['load_list']:
        base['load_list'].append(cid)
        base['containers'].append({'id': cid})
        worker.write_json(call / 'base-instance.json', base)
    return dict(name=name or path.name, path=str(path), media_type='text/plain', role='booking',
                booking_ref=ref, complete_booking_list=False, synthetic=True, container_ids=[cid])


def receipt_for_documents(documents, base_path, *, complete=True, cost=.02, row_id=None):
    captured = []
    for document in documents:
        path = __import__('pathlib').Path(document['path'])
        captured.append(dict(id=document.get('id', path.stem), name=document['name'], role=document.get('role', 'unspecified'),
                             mime_type=document['media_type'], path=str(path), sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
                             text=path.read_text(encoding='utf-8-sig'), booking_ref=document.get('booking_ref'),
                             complete_booking_list=document.get('complete_booking_list', False), synthetic=document.get('synthetic', False)))
    base = worker.read_json(base_path)
    if documents[0].get('container_ids'):
        base = dict(base, load_list=documents[0]['container_ids'])
    context = dict(version=2, documents=captured, base_instance=base, state={})
    before = {key: context[key] for key in ('documents', 'base_instance', 'state')}
    rows = [{'id': row_id or 'SYNTH-' + str(documents[0].get('booking_ref'))}]
    record = dict(mode='live', stage='intake', input_sha256=worker._digest(before),
                  output_sha256=worker._digest(rows), notes=json.dumps({'intake_context': context}), wall_s=3, cost_usd=cost)
    raw = dict(status='complete' if complete else 'needs_answers', rows=rows,
               questions=[] if complete else [{'id': 'mass'}], records=[record])
    return dict(mode='live', rows=rows, boxes=1, complete=complete, questions=[] if complete else [{'id': 'mass'}],
                model='muse-spark-1.3', wall_s=3, cost_usd=cost, raw=raw, receipt_sha256=worker._digest(raw))


def test_compact_booking_index_keeps_full_receipts_only_in_hashed_private_files(call, monkeypatch):
    documents = [booking_document(call, 'B1'), booking_document(call, 'B2')]
    fake_module(monkeypatch, 'app.functional_models',
                read_papers=lambda group, base: receipt_for_documents(group, base), answer_papers=None,
                merge_intake_views=merge_booking_fakes)
    worker.intake(call, {'documents': documents})
    index = worker.read_json(call / 'intake-bookings.json')
    assert index['schema'] == worker.BOOKING_INDEX_SCHEMA
    assert len(index['bookings']) == 2
    assert len(json.dumps(index)) < 4000
    for entry in index['bookings']:
        assert 'view' not in entry and 'raw' not in entry
        assert entry['status'] == 'done' and entry['boxes'] == 1
        path = call / entry['file']
        assert path.is_relative_to(call / 'intake' / 'bookings')
        assert hashlib.sha256(path.read_bytes()).hexdigest() == entry['sha256']
        assert worker.read_json(path)['raw']['records']
    public = worker.read_json(call / 'intake-view.json')
    assert 'raw' not in public and public['boxes'] == 2
    assert 'raw' in worker.read_json(call / 'intake.json')
    assert len(list((call / 'intake' / 'bookings').glob('*.json'))) == 2


def test_retry_reuses_identical_paid_upload_after_random_storage_filename_changes(call, monkeypatch):
    first = booking_document(call, 'B1', name='booking.txt', path_name='1-paper-old-booking.txt')
    calls = []

    def read(group, base):
        calls.append(group)
        return receipt_for_documents(group, base)

    models = fake_module(monkeypatch, 'app.functional_models', read_papers=read, answer_papers=None,
                         merge_intake_views=merge_booking_fakes)
    original = worker.intake(call, {'documents': [first]})
    second = booking_document(call, 'B1', name='booking.txt', path_name='1-paper-new-booking.txt')
    models.spend_status = lambda: pytest.fail('retained reads need no new paid-call reservation')
    resumed = worker.intake(call, {'documents': [second]})
    assert len(calls) == 1 and resumed['rows'] == original['rows']
    assert len(list((call / 'intake' / 'bookings').glob('*.json'))) == 1
    assert any('B1 kept from the earlier read' in row['notes'] for row in stages(call))


@pytest.mark.parametrize('change', ['bytes', 'role', 'base', 'explicit_id'])
def test_retry_rereads_changed_input_and_keeps_old_receipt_evidence(call, monkeypatch, change):
    document = booking_document(call, 'B1')
    calls = []

    def read(group, base):
        calls.append(True)
        return receipt_for_documents(group, base)

    fake_module(monkeypatch, 'app.functional_models', read_papers=read, answer_papers=None,
                merge_intake_views=merge_booking_fakes)
    worker.intake(call, {'documents': [document]})
    original_file = worker.read_json(call / 'intake-bookings.json')['bookings'][0]['file']
    if change == 'bytes':
        __import__('pathlib').Path(document['path']).write_text('SYNTHETIC DOCUMENT\nChanged declaration\n')
    elif change == 'role':
        document['role'] = 'correspondence'
    elif change == 'base':
        base = worker.read_json(call / 'base-instance.json')
        base['id'] = 'changed-synthetic-instance'
        worker.write_json(call / 'base-instance.json', base)
    else:
        document['id'] = 'explicit-host-id'
    worker.intake(call, {'documents': [document]})
    assert len(calls) == 2
    assert (call / original_file).is_file()
    history = list((call / 'intake').glob('history-index-*.json'))
    assert history and worker.read_json(history[0])['bookings'][0]['file'] == original_file


def test_legacy_partial_read_migrates_matching_receipts_and_reads_only_missing_booking(call, monkeypatch):
    documents = [booking_document(call, f'SYN-example-B{index}') for index in (1, 2, 3)]
    views = [receipt_for_documents([document], call / 'base-instance.json') for document in documents]
    legacy = {'bookings': [dict(booking_ref=document['booking_ref'], view=None if index == 1 else views[index])
                           for index, document in enumerate(documents)]}
    worker.write_json(call / 'intake-bookings.json', legacy)
    read_refs = []

    def read(group, base):
        read_refs.append(group[0]['booking_ref'])
        return receipt_for_documents(group, base)

    fake_module(monkeypatch, 'app.functional_models', read_papers=read, answer_papers=None,
                merge_intake_views=merge_booking_fakes)
    result = worker.intake(call, {'documents': documents})
    assert read_refs == ['SYN-example-B2'] and result['boxes'] == 3
    index = worker.read_json(call / 'intake-bookings.json')
    assert index['schema'] == worker.BOOKING_INDEX_SCHEMA and all(row['status'] == 'done' for row in index['bookings'])
    retained = worker._load_booking_records(call)
    assert retained[0]['view'] == views[0] and retained[2]['view'] == views[2]
    assert retained[0]['view']['raw']['records'][0]['notes'] == views[0]['raw']['records'][0]['notes']
    assert any('B1, B3 kept from the earlier read; reading B2' in row['notes'] for row in stages(call))
    archive = list((call / 'intake').glob('legacy-index-*.json'))
    assert len(archive) == 1 and all('view' not in row for row in worker.read_json(archive[0])['bookings'])


@pytest.mark.parametrize('tamper', ['input_hash', 'receipt_hash', 'captured_text'])
def test_unverifiable_legacy_read_is_preserved_but_never_reused(call, monkeypatch, tamper):
    document = booking_document(call, 'B1')
    view = receipt_for_documents([document], call / 'base-instance.json')
    if tamper == 'input_hash':
        view['raw']['records'][0]['input_sha256'] = '0'*64
        view['receipt_sha256'] = worker._digest(view['raw'])
    elif tamper == 'receipt_hash':
        view['receipt_sha256'] = '0'*64
    else:
        context = json.loads(view['raw']['records'][0]['notes'])['intake_context']
        context['documents'][0]['text'] = 'A different declaration with the same claimed byte hash.'
        view['raw']['records'][0]['notes'] = json.dumps({'intake_context': context})
        view['raw']['records'][0]['input_sha256'] = worker._digest({key: context[key] for key in ('documents', 'base_instance', 'state')})
        view['receipt_sha256'] = worker._digest(view['raw'])
    worker.write_json(call / 'intake-bookings.json', {'bookings': [{'booking_ref': 'B1', 'view': view}]})
    calls = []
    fake_module(monkeypatch, 'app.functional_models',
                read_papers=lambda group, base: calls.append(True) or receipt_for_documents(group, base),
                answer_papers=None, merge_intake_views=merge_booking_fakes)
    worker.intake(call, {'documents': [document]})
    assert calls == [True]
    assert len(list((call / 'intake' / 'bookings').glob('*.json'))) >= 1
    archive = list((call / 'intake').glob('legacy-index-*.json'))
    preserved = worker.read_json(call / worker.read_json(archive[0])['bookings'][0]['file'])
    assert preserved == view


@pytest.mark.parametrize('damage', ['missing', 'bytes', 'json'])
def test_corrupt_or_missing_private_booking_file_is_reread_safely(call, monkeypatch, damage):
    document = booking_document(call, 'B1')
    calls = []
    fake_module(monkeypatch, 'app.functional_models',
                read_papers=lambda group, base: calls.append(True) or receipt_for_documents(group, base),
                answer_papers=None, merge_intake_views=merge_booking_fakes)
    worker.intake(call, {'documents': [document]})
    index = worker.read_json(call / 'intake-bookings.json')
    path = call / index['bookings'][0]['file']
    if damage == 'missing':
        path.unlink()
    else:
        path.write_text('not JSON' if damage == 'json' else '{}')
        if damage == 'json':
            index['bookings'][0]['sha256'] = hashlib.sha256(path.read_bytes()).hexdigest()
            worker.write_json(call / 'intake-bookings.json', index)
    worker.intake(call, {'documents': [document]})
    assert calls == [True, True]
    assert worker._load_booking_records(call)[0]['view']['complete'] is True


def test_no_affordable_hold_stops_before_send_and_keeps_existing_public_summary(call, monkeypatch):
    document = booking_document(call, 'B1')
    worker.write_json(call / 'intake-view.json', {'earlier_progress': 'Earlier progress remains available.'})
    fake_module(monkeypatch, 'app.functional_models',
                read_papers=lambda *_: pytest.fail('no hold means no request'), answer_papers=None,
                grant_call=lambda **_: None)
    worker.write_json(call / 'worker-control.json', {'documents': [document]})
    assert worker.run_step('intake', call) == 1
    assert worker.read_json(call / 'worker-state.json')['message'].startswith(worker.NO_INTAKE_HOLD)
    assert worker.read_json(call / 'intake-view.json')['earlier_progress'] == 'Earlier progress remains available.'
    assert worker.read_json(call / 'intake-bookings.json')['bookings'][0]['status'] == 'pending'


def test_adaptive_scheduler_reaches_six_when_six_reservations_fit(call, monkeypatch):
    documents = [booking_document(call, f'B{index}') for index in range(7)]
    barrier, lock = threading.Barrier(6), threading.Lock()
    active = peak = started = 0

    def read(group, base):
        nonlocal active, peak, started
        with lock:
            active += 1
            started += 1
            ordinal = started
            peak = max(peak, active)
        if ordinal <= 6:
            barrier.wait(timeout=3)
        result = receipt_for_documents(group, base)
        with lock:
            active -= 1
        return result

    fake_module(monkeypatch, 'app.functional_models', read_papers=read, answer_papers=None,
                merge_intake_views=merge_booking_fakes)
    worker.intake(call, {'documents': documents})
    assert peak == 6 and started == 7
    assert any('6 bookings at a time' in row['notes'] for row in stages(call))


def test_grant_scheduler_refills_before_slow_sibling_finishes_and_persists_before_release(call, monkeypatch):
    documents = [booking_document(call, f'B{index}') for index in range(3)]
    initial_pair = threading.Barrier(2)
    replacement_started = threading.Event()
    lock = threading.Lock()
    completed, started, released, allocated = [], [], [], []
    outstanding = {}

    def grant_call(*, call_id, booking_ref):
        with lock:
            assert call_id == call.name
            if len(outstanding) == 2:
                return None
            grant = SimpleNamespace(id=booking_ref, pending_billing='model-owned')
            outstanding[booking_ref] = grant
            allocated.append(grant)
            return grant

    def release(grant):
        # Releasing room for another booking must follow durable evidence.
        index = worker.read_json(call / 'intake-bookings.json')['bookings']
        entry = next(item for item in index if item['booking_ref'] == grant.id)
        assert entry['status'] == 'done'
        assert worker.read_json(call / entry['file'])['complete'] is True
        assert worker.read_json(call / 'intake-view.json')['boxes'] >= 1
        with lock:
            assert outstanding.pop(grant.id) is grant
            assert grant.pending_billing == 'model-owned'
            released.append(grant)

    def read(group, base, *, grant):
        ref = group[0]['booking_ref']
        with lock:
            assert outstanding[ref] is grant
            started.append(ref)
        if ref in ('B0', 'B1'):
            initial_pair.wait(timeout=3)
        if ref == 'B1':
            assert replacement_started.wait(timeout=3), 'a freed grant must refill while B1 is blocked'
        if ref == 'B2':
            with lock:
                assert 'B0' in completed and 'B1' not in completed
            replacement_started.set()
        result = receipt_for_documents(group, base, cost=.2)
        with lock:
            completed.append(ref)
        return result

    fake_module(monkeypatch, 'app.functional_models', read_papers=read, answer_papers=None,
                merge_intake_views=merge_booking_fakes, grant_call=grant_call, release=release,
                spend_status=lambda: pytest.fail('the worker must delegate allocation to grant_call'))
    worker.intake(call, {'documents': documents})
    assert set(started[:2]) == {'B0', 'B1'} and started[2:] == ['B2']
    assert len(allocated) == len(released) == 3 and not outstanding
    assert {id(grant) for grant in allocated} == {id(grant) for grant in released}


@pytest.mark.parametrize('denial', ['none', 'exception'])
def test_grant_denial_without_active_work_sends_nothing(call, monkeypatch, denial):
    class SpendCapExceeded(RuntimeError):
        pass

    def denied(**kwargs):
        assert kwargs.pop('refusal') == {}
        assert kwargs == {'call_id': call.name, 'booking_ref': 'B1'}
        if denial == 'exception':
            raise SpendCapExceeded('Synthetic launch allowance already allocated.')
        return None

    fake_module(monkeypatch, 'app.functional_models', grant_call=denied,
                release=lambda _: pytest.fail('a denied grant cannot be released'),
                read_papers=lambda *_: pytest.fail('no grant means no request'), answer_papers=None)
    with pytest.raises(ValueError, match='Raise the spend cap to continue'):
        worker.intake(call, {'documents': [booking_document(call, 'B1')]})


def test_missing_grant_api_never_uses_shared_ledger(call, monkeypatch):
    monkeypatch.setitem(sys.modules, 'app.functional_models', SimpleNamespace(
        read_papers=lambda *_: pytest.fail('no grant API means no request'), answer_papers=None))
    with pytest.raises(ValueError, match='booking-budget grants are unavailable'):
        worker.intake(call, {'documents': [booking_document(call, 'B1')]})


@pytest.mark.parametrize('submission_failure', [True, False])
def test_grants_release_once_on_submit_failure_or_cancelled_read(call, monkeypatch, submission_failure):
    from concurrent.futures import Future

    allocated, released, futures = [], [], []

    class NotifyingFuture(Future):
        notified = False

        def cancel(self):
            cancelled = super().cancel()
            if cancelled and not self.notified:
                self.notified = True
                self.set_running_or_notify_cancel()
            return cancelled

    class FakeExecutor:
        def __init__(self, *, max_workers, thread_name_prefix):
            assert max_workers == 6

        def __enter__(self):
            return self

        def __exit__(self, *_):
            return False

        def submit(self, *args, **kwargs):
            if submission_failure:
                raise RuntimeError('Synthetic thread could not start.')
            future = NotifyingFuture()
            if not futures:
                future.set_exception(ValueError('Synthetic booking failed.'))
            futures.append(future)
            return future

    def grant_call(*, call_id, booking_ref):
        grant = SimpleNamespace(id=booking_ref, unknown_billed_hold='owned by the model ledger')
        allocated.append(grant)
        return grant

    def release(grant):
        assert grant not in released
        assert grant.unknown_billed_hold == 'owned by the model ledger'
        released.append(grant)

    monkeypatch.setattr(worker, 'ThreadPoolExecutor', FakeExecutor)
    fake_module(monkeypatch, 'app.functional_models', grant_call=grant_call, release=release,
                read_papers=lambda *_: pytest.fail('controlled executor never runs a model'), answer_papers=None)
    documents = [booking_document(call, 'B1'), booking_document(call, 'B2')]
    with pytest.raises((RuntimeError, ValueError), match='Synthetic'):
        worker.intake(call, {'documents': documents})
    assert len(allocated) == (1 if submission_failure else 2)
    assert len(released) == len(allocated)
    assert all(any(item is grant for item in released) for grant in allocated)
    if not submission_failure:
        assert futures[1].cancelled()
        entries = worker.read_json(call / 'intake-bookings.json')['bookings']
        assert [entry['status'] for entry in entries] == ['failed', 'pending']


def test_failed_new_read_retries_only_unfinished_booking_and_keeps_partial_summary(call, monkeypatch):
    documents = [booking_document(call, 'B1'), booking_document(call, 'B2')]
    first_finished = threading.Event()
    calls = {'B1': 0, 'B2': 0}

    def read(group, base):
        ref = group[0]['booking_ref']
        calls[ref] += 1
        if ref == 'B1':
            result = receipt_for_documents(group, base, cost=.02)
            first_finished.set()
            return result
        assert first_finished.wait(timeout=3)
        if calls[ref] == 1:
            raise ValueError('Synthetic booking read stopped before a request was sent.')
        return receipt_for_documents(group, base, cost=.03)

    grants, releases = [], []

    def grant_call(*, call_id, booking_ref):
        grant = SimpleNamespace(id=len(grants), booking_ref=booking_ref)
        grants.append(grant)
        return grant

    def release(grant):
        assert all(item is not grant for item in releases)
        releases.append(grant)

    fake_module(monkeypatch, 'app.functional_models', read_papers=read, answer_papers=None,
                merge_intake_views=merge_booking_fakes, grant_call=grant_call, release=release)
    worker.write_json(call / 'worker-control.json', {'documents': documents})
    assert worker.run_step('intake', call) == 1
    first_index = worker.read_json(call / 'intake-bookings.json')
    assert [row['status'] for row in first_index['bookings']] == ['done', 'failed']
    retained_file = first_index['bookings'][0]['file']
    partial = worker.read_json(call / 'intake-view.json')
    assert partial['boxes'] == 1 and partial['complete'] is False and 'raw' not in partial
    assert partial['progress'] == {'completed_bookings': 1, 'total_bookings': 2, 'pending_bookings': 1}
    assert worker.run_step('intake', call) == 0
    assert calls == {'B1': 1, 'B2': 2}
    final_index = worker.read_json(call / 'intake-bookings.json')
    assert final_index['bookings'][0]['file'] == retained_file
    assert all(row['status'] == 'done' for row in final_index['bookings'])
    assert worker.read_json(call / 'intake-view.json')['cost_usd'] == pytest.approx(.05)
    assert sum(row.get('cost_usd') or 0 for row in stages(call) if row.get('cost_scope') != 'cumulative_booking_total') == pytest.approx(.05)
    assert len(list((call / 'intake' / 'bookings').glob('*.json'))) == 2
    assert [grant.booking_ref for grant in grants] == ['B1', 'B2', 'B2']
    assert len(releases) == len(grants) == 3


def test_unconvertible_answer_fails_only_its_booking_and_waits_for_its_retry(call, monkeypatch):
    """One booking's answer that can't become rows (e.g. InvalidOperation on a null set point) marks only that booking
    failed with a plain reason; the other bookings finish and are kept, the call waits for a retry, and the intake stage
    never fails."""
    class ModelError(RuntimeError):  # the shape of app.functional_models' errors: only `reason` is public
        pass

    class AnswerConversionError(ModelError):
        retry_booking = True
        stage = 'spark'
        reason = ("The app couldn't turn Muse Spark's reading of this booking into load-list rows (InvalidOperation); "
                  "nothing from it was used. Retry this booking; the other bookings' readings are kept.")

    documents = [booking_document(call, 'B1'), booking_document(call, 'B2'), booking_document(call, 'B3')]
    calls = {'B1': 0, 'B2': 0, 'B3': 0}
    second_started = threading.Event()

    def read(group, base):
        ref = group[0]['booking_ref']
        calls[ref] += 1
        if ref == 'B2' and calls[ref] == 1:
            second_started.set()
            error = AnswerConversionError('Decimal(str(None)) with private details')
            error.cost_usd = .04
            raise error
        if ref == 'B3':
            assert second_started.wait(timeout=3), 'the failed booking must not cancel the others'
        return receipt_for_documents(group, base, cost=.02)

    fake_module(monkeypatch, 'app.functional_models', read_papers=read, answer_papers=None, merge_intake_views=merge_booking_fakes)
    worker.write_json(call / 'worker-control.json', {'documents': documents})
    assert worker.run_step('intake', call) == 0
    state = worker.read_json(call / 'worker-state.json')
    assert state['state'] == 'needs_papers' and 'private details' not in json.dumps(state)
    assert state['message'] == ("Booking B2: the app couldn't turn Muse Spark's reading into load-list rows, so nothing from it was "
                                "used. Retry the failed booking; the other 2 booking readings are kept. Nothing has been planned "
                                "from a partial reading.")
    index = worker.read_json(call / 'intake-bookings.json')['bookings']
    assert [row['status'] for row in index] == ['done', 'failed', 'done']
    assert index[1]['reason'] == AnswerConversionError.reason and index[1]['cost_usd'] == .04
    rows = stages(call)
    assert not any(row['status'] == 'failed' and ' · booking ' not in (row['who'] or '') for row in rows), 'the intake stage never fails'
    failed = [row for row in rows if row['status'] == 'failed']
    assert len(failed) == 1 and failed[0]['who'] == 'Muse Spark · booking B2' and failed[0]['notes'] == AnswerConversionError.reason
    aggregate = rows[-1]
    assert aggregate['status'] == 'incomplete' and aggregate['cost_usd'] == 0 and state['message'] in aggregate['notes']
    assert not (call / 'intake.json').exists(), 'nothing is assembled from a partial reading'
    progress = worker.read_json(call / 'intake-view.json')['progress']
    assert progress == {'completed_bookings': 2, 'total_bookings': 3, 'pending_bookings': 1}
    # The planner's retry reads only the failed booking and keeps the other two receipts.
    assert worker.run_step('intake', call) == 0
    assert calls == {'B1': 1, 'B2': 2, 'B3': 1}
    assert worker.read_json(call / 'worker-state.json')['state'] == 'assembling'
    assert [row['status'] for row in worker.read_json(call / 'intake-bookings.json')['bookings']] == ['done', 'done', 'done']
    assert sum(row.get('cost_usd') or 0 for row in stages(call) if row.get('cost_scope') != 'cumulative_booking_total') == pytest.approx(.10)


def test_one_booking_schedule_note_is_singular(call, monkeypatch):
    fake_module(monkeypatch, 'app.functional_models', read_papers=lambda group, base: receipt_for_documents(group, base, cost=.01),
                answer_papers=None, merge_intake_views=merge_booking_fakes)
    worker.write_json(call / 'worker-control.json', {'documents': [booking_document(call, 'B1')]})
    assert worker.run_step('intake', call) == 0
    notes = [row['notes'] for row in stages(call) if row['who'] == 'Intake booking schedule']
    assert notes and notes[0].startswith('1 booking at a time:')

@pytest.mark.parametrize('roster', [None, [], 'BOX', ['BOX', 'BOX'], [None], [' BOX']])
def test_host_container_membership_rejects_invalid_rosters(roster):
    with pytest.raises(ValueError, match='membership'):
        worker.document_metadata({'container_ids': roster})


def test_host_container_membership_survives_metadata_normalization():
    assert worker.document_metadata({'container_ids': ['SYNTH-A', 'SYNTH-B']})['container_ids'] == ['SYNTH-A', 'SYNTH-B']


@pytest.mark.parametrize('scoped', [False, True])
def test_uploaded_bookings_use_real_intake_evaluation_with_only_interpretation_faked(call, monkeypatch, scoped):
    from app import functional_models as models
    from stowage.model.fixtures import build_instance
    from decimal import Decimal
    from llm.spark.client import SparkResponse
    from llm.spark.spend import Usage
    base = build_instance().model_dump(mode='json')
    ids = base['load_list'][:2]
    base['load_list'] = ids
    worker.write_json(call / 'base-instance.json', base)
    documents = []
    for index, cid in enumerate(ids):
        path = call / f'uploaded-booking-{index}.txt'
        path.write_text('SYNTHETIC DOCUMENT\nBooking declaration for ' + cid)
        item = dict(name=path.name, path=str(path), media_type='text/plain', role='booking',
                    booking_ref=f'B{index+1}', origin='uploaded')
        if scoped:
            item['container_ids'] = [cid]
        documents.append(item)
    seen = []

    class Client:
        def complete_documents(self, content, **kwargs):
            seen.append(content)
            interpretation = dict(schema_version='stowlm.intake-interpretation/7', header_maps=[], observations=[], exceptions=[], notes=[])
            return SparkResponse(json.dumps(interpretation), Usage(100, 20), Decimal('.01'), 'synthetic-interpretation')

    monkeypatch.setattr(worker, '_RequestGrantClient', lambda budget: Client())
    monkeypatch.setattr(models, 'grant_call', lambda **kwargs: object())
    monkeypatch.setattr(models, 'release', lambda grant: None)
    result = worker.intake(call, {'documents': documents})
    assert len(seen) == (2 if scoped else 1)
    assert len(result['rows']) == 2 and {row['id'] for row in result['rows']} == set(ids)
    assert not any(conflict.get('code') == 'duplicate_box_id' for conflict in result['conflicts'])
    if scoped:
        bookings = result['raw']['bookings']
        assert len(bookings) == 2 and [child['view']['boxes'] for child in bookings] == [1, 1]
    else:
        assert 'bookings' not in result['raw'] and len(result['raw']['rows']) == 2
    question = next(item for item in result['questions'] if item.get('field') == 'booking_status')
    answered = worker.intake(call, {'answers': {question['id']: 'shut_out'}})
    assert len(seen) == (2 if scoped else 1), 'planner answers must not send another model request'
    assert len(answered['rows']) == 1 and answered['read_cost_usd'] == result['read_cost_usd']
    assert answered['read_wall_s'] == result['read_wall_s']
    assert worker.intake_accounting(call)['incremental_cost_usd'] == 0
    assert worker.intake_accounting(call)['open_questions'] == len(answered['questions'])


def test_cap_stop_retains_spend_questions_and_atomic_refusal_facts_across_retry(call, monkeypatch):
    documents = [booking_document(call, 'B1'), booking_document(call, 'B2')]
    grants, releases = [], []

    def grant_call(*, call_id, booking_ref, refusal):
        if booking_ref == 'B1':
            grant = object()
            grants.append(grant)
            return grant
        refusal.update(spent_usd='.20', holds_usd='.10', grants_usd='0', hold_usd='1.384448', cap_usd='1.60')
        return None

    fake_module(monkeypatch, 'app.functional_models',
                read_papers=lambda group, base: receipt_for_documents(group, base, complete=False, cost=.2),
                answer_papers=None, merge_intake_views=merge_booking_fakes,
                grant_call=grant_call, release=lambda grant: releases.append(grant))
    for attempt in range(2):
        with pytest.raises(ValueError, match='Nothing has been planned from a partial reading') as stopped:
            worker.intake(call, {'documents': documents})
        text = str(stopped.value)
        assert "Booking B2 wasn't sent" in text and '$0.20 spent' in text and '$0.100000 held' in text
        assert '$1.384448' in text and '$1.60 cap' in text and '1 questions remain open' in text
        accounting = worker.intake_accounting(call)
        assert accounting['settled_cost_usd'] == .2
        assert accounting['incremental_cost_usd'] == (.2 if attempt == 0 else 0)
        assert accounting['completed_bookings'] == 1 and accounting['kept_bookings'] == attempt
        assert accounting['unsent_bookings'] == 1 and accounting['open_questions'] == 1
        assert accounting['booking_wall_s'] == 3 and accounting['elapsed_s'] < 3
        assert accounting['spend_wait_s'] > 0
        assert accounting['cap_stop']['sent'] is False and accounting['cap_stop']['booking_ref'] == 'B2'
        assert worker.read_json(call / 'intake-view.json')['settled_cost_usd'] == .2
    assert len(grants) == len(releases) == 1


def test_failed_aggregate_credits_cloud_and_counts_paid_failed_response_without_duplicate_charges(call, monkeypatch):
    class ModelError(RuntimeError):
        stage, reason, cost_usd = 'spark', 'The synthetic response failed schema validation.', .17

    def read(*args):
        raise ModelError('provider detail stays in the worker log')

    fake_module(monkeypatch, 'app.functional_models', read_papers=read, answer_papers=None)
    worker.write_json(call / 'worker-control.json', {'documents': [booking_document(call, 'B1')]})
    assert worker.run_step('intake', call) == 1
    rows = stages(call)
    total = rows[-1]
    assert total['who'] == 'Muse Spark' and total['where'] == 'Meta Model API, cloud'
    assert total['status'] == 'failed' and total['cost_scope'] == 'cumulative_booking_total'
    assert total['cost_usd'] == total['cumulative_cost_usd'] == total['incremental_cost_usd'] == .17
    assert sum(row.get('cost_usd') or 0 for row in rows if row.get('cost_scope') != 'cumulative_booking_total') == .17
    assert worker.read_json(call / 'intake-view.json')['settled_cost_usd'] == .17


def test_invalid_new_papers_clear_previous_cap_message_without_erasing_paid_accounting(call, monkeypatch):
    fake_module(monkeypatch, 'app.functional_models', read_papers=None, answer_papers=None)
    worker.write_json(call / 'intake-accounting.json', dict(
        settled_cost_usd=.2, incremental_cost_usd=.2, elapsed_s=20, cap_stop={'booking_ref': 'old'}, message='An older cap stop.'))
    worker.write_json(call / 'worker-control.json', {'documents': [{'role': 'made_up'}]})
    assert worker.run_step('intake', call) == 1
    assert 'supported cargo document role' in worker.read_json(call / 'worker-state.json')['message']
    accounting = worker.intake_accounting(call)
    assert accounting['settled_cost_usd'] == .2 and accounting['incremental_cost_usd'] == 0
    assert accounting['message'] is None and accounting['cap_stop'] is None


@pytest.mark.parametrize('repair_fits,valid_repair', [(True, True), (False, True), (True, False)])
def test_schema_repair_gets_its_own_real_grant_and_persists_paid_evidence(call, monkeypatch, repair_fits, valid_repair):
    from app import functional_models as models
    from stowage.model.fixtures import build_instance
    from io import BytesIO
    import secrets
    import urllib.request
    from llm.spark.spend import Usage, calculate_cost
    key = secrets.token_hex(32)
    monkeypatch.setenv('MODEL_API_KEY', key)
    monkeypatch.setenv('STOWLM_ACCEPT_PAID_CALLS', '1')
    monkeypatch.setenv('STOWLM_FUNCTIONAL_LAUNCH_ID', 'synthetic-worker-repair')
    monkeypatch.setattr(models, '_FUNCTIONAL_SPEND_ROOT', call / 'synthetic-spend')
    models.configure_spend(cap_usd=2 if repair_fits else 1.38445)
    base = build_instance().model_dump(mode='json')
    base['load_list'] = base['load_list'][:1]
    worker.write_json(call / 'base-instance.json', base)
    document = dict(name='cargo.txt', path=str(call / 'unused'), media_type='text/plain', role='booking',
                    booking_ref='B1', container_ids=base['load_list'], origin='uploaded')
    calls = []
    observed_grants, released = [], []
    original_grant, original_release = models.grant_call, models.release

    def grant_call(*args, **kwargs):
        grant = original_grant(*args, **kwargs)
        if grant is not None:
            observed_grants.append(grant)
        return grant

    def release(grant):
        assert not any(item is grant for item in released)
        # Even a schema-invalid paid response must survive before release.
        assert list((call / 'intake' / 'requests').rglob('*.json'))
        original_release(grant)
        released.append(grant)

    def transport(request, **kwargs):
        calls.append(json.loads(request.data))
        text = 'not JSON' if len(calls) == 1 or not valid_repair else json.dumps(dict(
            schema_version='stowlm.intake-interpretation/7', header_maps=[], observations=[], exceptions=[], notes=[]))
        response = BytesIO(json.dumps(dict(model='muse-spark-1.3', id=f'synthetic-{len(calls)}', status='completed',
            usage=dict(input_tokens=100, output_tokens=20),
            output=[dict(type='message', content=[dict(type='output_text', text=text)])])).encode())
        response.headers = {'x-request-id': f'synthetic-{len(calls)}'}
        return response

    monkeypatch.setattr(models, 'grant_call', grant_call)
    monkeypatch.setattr(models, 'release', release)
    monkeypatch.setattr(urllib.request, 'urlopen', transport)
    if repair_fits and valid_repair:
        result = worker.intake(call, {'documents': [document]})
        assert result['mode'] == 'live' and len(calls) == 2
        assert 'SCHEMA REPAIR ONLY' in calls[1]['input'][0]['content'][0]['text']
        assert len({grant.ledger_path for grant in observed_grants}) == 2
    elif not repair_fits:
        with pytest.raises(ValueError, match="schema-repair request for booking B1 wasn't sent"):
            worker.intake(call, {'documents': [document]})
        assert len(calls) == 1
        assert worker.intake_accounting(call)['cap_stop']['request_kind'] == 'schema_repair'
    else:
        with pytest.raises(models.ModelError, match='failed intake schema validation'):
            worker.intake(call, {'documents': [document]})
        assert len(calls) == 2 and worker.intake_accounting(call)['cap_stop'] is None
        assert worker.intake_accounting(call)['booking_wall_s'] > 0
    assert len(released) == len(observed_grants) == len(calls)
    measured = float(calculate_cost(Usage(100, 20))) * len(calls)
    assert models.spend_status()['spent_usd'] == pytest.approx(measured)
    assert models.spend_status()['held_usd'] == models.spend_status()['grants_usd'] == 0
    assert worker.intake_accounting(call)['settled_cost_usd'] == pytest.approx(measured)
    evidence = [worker.read_json(path) for path in (call / 'intake' / 'requests').rglob('*.json')]
    assert len(evidence) == len(calls) and any(item['raw_text'] == 'not JSON' for item in evidence)
    assert not any(key in path.read_text() for path in call.rglob('*.json'))


def test_schema_repair_waits_for_other_owned_grant_but_not_idle_booking_futures(call):
    denied, resumed, releases = threading.Event(), [], []
    first, second = object(), object()
    available = False

    def grant_call(*, call_id, booking_ref, refusal):
        if booking_ref == 'sibling':
            return first
        if not available:
            refusal.update(spent_usd='.2', holds_usd='0', grants_usd='1.38', hold_usd='1.384448', cap_usd='2.768896')
            denied.set()
            return None
        return second

    def release(grant):
        nonlocal available
        assert grant is first
        available = True
        releases.append(grant)

    coordinator = worker._GrantCoordinator(call, grant_call, release)
    sibling, _ = coordinator.acquire('sibling')
    thread = threading.Thread(target=lambda: resumed.append(coordinator.repair('B1')))
    thread.start()
    try:
        assert denied.wait(3) and thread.is_alive()
        coordinator.close(sibling)
        thread.join(timeout=3)
        assert resumed == [second] and releases == [first]
    finally:
        with coordinator.condition:
            coordinator.active.clear()
            coordinator.condition.notify_all()
        thread.join(timeout=3)
    assert not thread.is_alive()


@pytest.mark.parametrize('owned_sibling', [False, True])
def test_schema_repair_wait_is_bounded_and_no_owned_grants_fail_immediately(call, monkeypatch, owned_sibling):
    import time
    facts = dict(spent_usd='.2', holds_usd='1.38', grants_usd='0', hold_usd='1.384448', cap_usd='2')

    def denied(*, call_id, booking_ref, refusal):
        refusal.update(facts)
        return None

    coordinator = worker._GrantCoordinator(call, denied, lambda _: None)
    monkeypatch.setattr(coordinator, 'WAIT_S', .01)
    if owned_sibling:
        coordinator.active.append(object())
    began = time.monotonic()
    with pytest.raises(ValueError, match="schema-repair request for booking B1 wasn't sent"):
        coordinator.repair('B1')
    assert time.monotonic()-began < 1


def test_failed_grant_release_wakes_repair_and_is_not_repeated(call):
    denied, stopped, release_calls = threading.Event(), [], []
    grant = SimpleNamespace(unknown_billing_hold='retained by the model ledger')

    def broker(*, call_id, booking_ref, refusal):
        if booking_ref == 'sibling':
            return grant
        refusal.update(spent_usd='0', holds_usd='1.38', grants_usd='0', hold_usd='1.384448', cap_usd='2')
        denied.set()
        return None

    def release(item):
        release_calls.append(item)
        raise RuntimeError('Synthetic registry write failed; its billing hold remains.')

    coordinator = worker._GrantCoordinator(call, broker, release)
    initial, _ = coordinator.acquire('sibling')
    budget = worker._BookingBudget(call, 'sibling', initial, coordinator, 'synthetic-attempt')

    def repair():
        try:
            coordinator.repair('B1')
        except ValueError as error:
            stopped.append(error)

    thread = threading.Thread(target=repair)
    thread.start()
    try:
        assert denied.wait(3)
        with pytest.raises(RuntimeError, match='billing hold remains'):
            budget.close()
        budget.close()
        thread.join(timeout=3)
        assert not thread.is_alive() and len(stopped) == 1
        assert not coordinator.active and release_calls == [grant]
        assert grant.unknown_billing_hold == 'retained by the model ledger'
    finally:
        with coordinator.condition:
            coordinator.active.clear()
            coordinator.condition.notify_all()
        thread.join(timeout=3)


@pytest.mark.parametrize('server_launch_row', [True, False])
@pytest.mark.parametrize('engine_error', [True, False])
def test_plan_progress_running_instance_becomes_receipt_terminal_without_losing_measurements(
        call, monkeypatch, server_launch_row, engine_error):
    from app.functional import _stage_views
    entered, release = threading.Event(), threading.Event()
    receipt_wall = 1.375
    engine_record = dict(stage='engine', mode='live', model=None, adapter_sha256=None,
                         input_sha256='a'*64, output_sha256='b'*64, wall_s=receipt_wall,
                         cost_usd=0, notes='Measured engine receipt.')
    engine_stage = None
    if server_launch_row:
        engine_stage = worker.append_stage(call, 'engine', 'running', instance_id='engine-plan-server-launch',
                                           started_utc=worker.utc_now(), cost_usd=0, attempt=1,
                                           notes='Searching for a checked plan.')
    worker.write_json(call / 'worker-control.json', {'instruction': 'none', 'profile': 'standard'})

    def run(packet, **kwargs):
        entered.set()
        assert release.wait(timeout=3)
        return dict(status='error' if engine_error else 'checked_plan', passed=not engine_error,
                    message='Engine stopped.' if engine_error else 'Checked plan returned.',
                    receipt={'steps': [engine_record]},
                    evidence={'engine_error': {'type': 'RuntimeError', 'message': 'Engine stopped.'}} if engine_error else {},
                    events=[dict(stage='engine', kind='stage_start', t_s=.25, data={}),
                            dict(stage='engine', kind='error' if engine_error else 'stage_end', t_s=1.625, data={})])

    fake_plan(monkeypatch, run)
    results = []
    thread = threading.Thread(target=lambda: results.append(worker.run_step('plan', call)))
    thread.start()
    try:
        assert entered.wait(timeout=3)
        visible = [row for row in _stage_views(call / 'stages.jsonl') if row['stage'] == 'engine']
        assert len(visible) == 1 and visible[0]['status'] == 'running'
        assert visible[0]['instance_id'] and visible[0]['started_utc']
        identity, started = visible[0]['instance_id'], visible[0]['started_utc']
        if server_launch_row:
            assert identity == engine_stage['instance_id']
        assert len([row for row in stages(call) if row['stage'] == 'engine' and row['status'] == 'running']) == 1
    finally:
        release.set()
        thread.join(timeout=3)
    assert not thread.is_alive() and results == [0]
    final = [row for row in _stage_views(call / 'stages.jsonl') if row['stage'] == 'engine']
    assert len(final) == 1 and final[0]['status'] == ('failed' if engine_error else 'done')
    assert final[0]['instance_id'] == identity and final[0]['started_utc'] == started
    assert final[0]['wall_s'] == receipt_wall
    assert final[0]['input_sha256'] == 'a'*64 and final[0]['output_sha256'] == 'b'*64
    assert not any(row['status'] == 'running' for row in _stage_views(call / 'stages.jsonl'))


def test_pre_engine_runner_failure_closes_server_running_instance(call, monkeypatch):
    from app.functional import _stage_views
    launched = worker.append_stage(call, 'engine', 'running', instance_id='engine-plan-not-admitted',
                                   started_utc=worker.utc_now(), cost_usd=0, attempt=1)
    worker.write_json(call / 'worker-control.json', {'instruction': 'none'})
    fake_plan(monkeypatch, lambda *_args, **_kwargs: dict(status='error', passed=False,
              message='Confirmation stopped before the engine.', events=[], receipt={'steps': []}))
    assert worker.run_step('plan', call) == 0
    final = [row for row in _stage_views(call / 'stages.jsonl') if row['stage'] == 'engine']
    assert len(final) == 1 and final[0]['status'] == 'failed'
    assert final[0]['instance_id'] == launched['instance_id']
    assert final[0]['started_utc'] == launched['started_utc']
    assert final[0]['wall_s'] is None and final[0]['input_sha256'] is None
    assert 'No engine-stage receipt was produced' in final[0]['notes']


def test_plan_validation_exception_closes_server_running_instance(call, monkeypatch):
    from app.functional import _stage_views
    launched = worker.append_stage(call, 'engine', 'running', instance_id='engine-plan-validation',
                                   started_utc=worker.utc_now(), cost_usd=0, attempt=1)
    worker.write_json(call / 'worker-control.json', {'instruction': 'invalid'})
    fake_plan(monkeypatch, lambda *_args, **_kwargs: pytest.fail('validation must precede runner execution'))
    assert worker.run_step('plan', call) == 1
    final = [row for row in _stage_views(call / 'stages.jsonl') if row['stage'] == 'engine']
    assert len(final) == 1 and final[0]['status'] == 'failed'
    assert final[0]['instance_id'] == launched['instance_id']
    assert final[0]['started_utc'] == launched['started_utc']


def test_generator_record_choice_and_later_answers_preserve_mixed_rows_at_zero_cost(call, monkeypatch):
    from copy import deepcopy
    base_rows = [{'id': 'SYNTH-1', 'gross_mass_kg': 100}, {'id': 'SYNTH-2', 'gross_mass_kg': 200}]
    worker.write_json(call / 'base-instance.json', {'id': 'synthetic', 'load_list': ['SYNTH-1', 'SYNTH-2'],
                                                   'containers': base_rows})
    before = dict(mode='live', model='muse-spark-1.3', complete=False, boxes=2, wall_s=2., cost_usd=.4,
                  rows=[{'id': 'SYNTH-1'}, {'id': 'SYNTH-2'}], raw={'source_receipt': 'retained'},
                  questions=[{'id': 'equipment', 'box': 'SYNTH-1'}, {'id': 'mass', 'box': 'SYNTH-2'}])
    worker.write_json(call / 'intake.json', before)
    worker.write_json(call / 'intake-bookings.json', {'bookings': [{'keep': 'original paid evidence'}]})
    original_index = (call / 'intake-bookings.json').read_bytes()
    worker.write_json(call / 'intake-accounting.json', {'settled_cost_usd': .4, 'incremental_cost_usd': .4,
                                                       'cap_stop': {'old': True}, 'message': 'old cap stop', 'open_questions': 2})
    seen = []

    def choose(view, base, box_ids):
        seen.append((deepcopy(view), base, box_ids))
        assert view['raw'] == before['raw']
        result = deepcopy(view)
        result.update(rows=[dict(base_rows[0], origin='generator'), {'id': 'SYNTH-2', 'origin': 'spark'}],
                      questions=[view['questions'][1]], generator_choices=[{'box': 'SYNTH-1'}],
                      raw={'source_intake': deepcopy(view), 'generator_rows': [base_rows[0]]})
        result['receipt_sha256'] = worker._digest(result['raw'])
        return result

    def answer(view, answers):
        assert view['generator_record_ids'] == ['SYNTH-1']
        assert view['raw']['source_intake']['raw'] == before['raw']
        assert answers == {'mass': 250}
        result = deepcopy(view)
        result.update(complete=True, questions=[], rows=[dict(base_rows[0], origin='generator'),
                                                        {'id': 'SYNTH-2', 'gross_mass_kg': 250, 'origin': 'spark'}])
        return result

    fake_module(monkeypatch, 'app.functional_models', merge_generator_records=choose, answer_papers=answer,
                read_papers=lambda *_args, **_kwargs: pytest.fail('Generator choice must not read papers'))
    worker.write_json(call / 'worker-control.json', {'use_generator_records_for': ['SYNTH-1']})
    assert worker.run_step('intake', call) == 0
    first = worker.read_json(call / 'intake.json')
    assert seen == [(before, call / 'base-instance.json', ['SYNTH-1'])]
    assert first['generator_record_ids'] == ['SYNTH-1']
    assert first['row_origins'] == {'SYNTH-1': 'generator', 'SYNTH-2': 'spark'}
    assert first['cost_usd'] == .4 and first['incremental_cost_usd'] == 0
    assert first['settled_cost_usd'] == .4 and first['cap_stop'] is None
    assert first['open_questions'] == 1
    assert worker.read_json(call / 'worker-state.json')['state'] == 'needs_answers'
    with pytest.raises(ValueError, match='questions'):
        worker._packet(call, {})

    worker.write_json(call / 'worker-control.json', {'use_generator_records_for': ['SYNTH-1'], 'answers': {'mass': 250}})
    assert worker.run_step('intake', call) == 0
    final = worker.read_json(call / 'intake.json')
    assert final['generator_record_ids'] == ['SYNTH-1']
    assert final['rows'][0] == dict(base_rows[0], origin='generator')
    assert final['rows'][1]['gross_mass_kg'] == 250
    assert final['cost_usd'] == .4 and final['incremental_cost_usd'] == 0
    assert final['open_questions'] == 0
    assert worker.read_json(call / 'worker-state.json')['state'] == 'assembling'
    packet = worker._packet(call, {})
    assert packet['load_list']['rows'] == [base_rows[0], {'id': 'SYNTH-2', 'gross_mass_kg': 250}]
    assert packet['load_list']['intake_receipt_sha256'] == final['receipt_sha256']
    assert all(row['stage'] == 'answers' and row['cost_usd'] == 0 for row in stages(call))
    assert all(row['where'] == 'this laptop' and row['model'] is None for row in stages(call))
    terminal = [row for row in stages(call) if row['status'] == 'done']
    assert all(row['notes'] == 'Muse Spark, with 1 boxes from the generator (not read by a model)' for row in terminal)
    assert (call / 'intake-bookings.json').read_bytes() == original_index


def test_generator_record_input_rebuilds_verified_children_after_generated_list_skip(call):
    paid = dict(mode='live', complete=False, boxes=1, rows=[{'id': 'SYNTH-1', 'iso_type': '45P3'}],
                questions=[{'id': 'review', 'box': 'SYNTH-1'}], raw={'paid_receipt': 'full'}, wall_s=1., cost_usd=.05)
    record = dict(booking_ref=None, view=paid)
    worker._persist_view(call, record)
    worker._write_booking_index(call, [record])
    worker.write_json(call / 'intake.json', {'skipped': True})
    worker.write_json(call / 'intake-view.json', {'rows': [{'id': 'untrusted-public-projection'}]})
    reconstructed = worker.generator_records_input(call)
    assert {key: reconstructed[key] for key in paid} == paid
    (call / record['file']).write_text('{}')
    with pytest.raises(ValueError, match='incomplete'):
        worker.generator_records_input(call)


def test_invalid_generator_record_choice_keeps_full_intake_unchanged(call, monkeypatch):
    paid = dict(mode='live', complete=False, rows=[{'id': 'SYNTH-1'}], questions=[{'id': 'other'}],
                raw={'receipt': 'retain'}, cost_usd=.3, wall_s=1.)
    worker.write_json(call / 'intake.json', paid)
    original = (call / 'intake.json').read_bytes()

    def reject(*_args):
        raise ValueError('A chosen box has another kind of open question; answer it first.')

    fake_module(monkeypatch, 'app.functional_models', merge_generator_records=reject, read_papers=None, answer_papers=None)
    worker.write_json(call / 'worker-control.json', {'use_generator_records_for': ['SYNTH-1']})
    assert worker.run_step('intake', call) == 1
    assert (call / 'intake.json').read_bytes() == original
    assert worker.read_json(call / 'worker-state.json')['state'] == 'failed'
    assert all(row['stage'] == 'answers' and row['cost_usd'] == 0 for row in stages(call))


def test_generator_record_choice_on_copied_development_booking_receipts(tmp_path, monkeypatch):
    """Optional local evidence regression: source artifacts are only read, never changed."""
    import shutil
    from pathlib import Path
    from app import functional_models as models
    from stowage.model.cargo import Container
    source = Path(__file__).resolve().parents[2] / 'experiments/mvp/ui/functional/calls/c20261103-212438-199686'
    if not (source / 'intake-bookings.json').is_file():
        pytest.skip('Optional local synthetic development call is not present in this checkout')
    assert callable(models.merge_generator_records)
    call = tmp_path / 'copied-synthetic-call'
    call.mkdir()
    originals = {source / name: (source / name).read_bytes() for name in ('intake-bookings.json', 'base-instance.json')}
    for original in originals:
        shutil.copy2(original, call / original.name)
    index = worker.read_json(source / 'intake-bookings.json')
    for record in index.get('bookings', []):
        if record.get('file'):
            relative = record['file']
            originals[source / relative] = (source / relative).read_bytes()
            (call / relative).parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source / relative, call / relative)
    worker.write_json(call / 'intake.json', {'skipped': True})
    before = worker.generator_records_input(call)
    chosen = 'QZXU0000117'
    assert any(question.get('box') == chosen for question in before['questions'])
    assert len(before['rows']) == 25
    monkeypatch.setattr(models, 'read_papers', lambda *_a, **_k: pytest.fail('No paid reading may occur'))
    monkeypatch.setattr(models, 'grant_call', lambda *_a, **_k: pytest.fail('No paid allocation may occur'))
    monkeypatch.setattr(models, 'availability', lambda **_k: pytest.fail('No model availability check is needed'))
    worker.write_json(call / 'worker-control.json', {'use_generator_records_for': [chosen]})
    assert worker.run_step('intake', call) == 0
    result = worker.read_json(call / 'intake.json')
    base = worker.read_json(call / 'base-instance.json')
    generated = next(row for row in base['containers'] if row['id'] == chosen)
    assert next(row for row in result['rows'] if row['id'] == chosen) == dict(generated, origin='generator')
    assert [{k: v for k, v in row.items() if k != 'origin'} for row in result['rows'] if row['id'] != chosen] == [row for row in before['rows'] if row['id'] != chosen]
    assert result['generator_record_ids'] == [chosen]
    assert result['cost_usd'] == before['cost_usd']
    assert result['raw']['source_intake']['raw'] == before['raw']
    assert all(row['cost_usd'] == 0 for row in stages(call))
    assert result['complete'] is True
    packet = worker._packet(call, {})
    for row in packet['load_list']['rows']:
        Container.model_validate(row)
        assert 'origin' not in row
    assert all(original.read_bytes() == data for original, data in originals.items())


def test_repeated_plan_attempts_use_distinct_outputs_and_keep_prior_receipts(call, monkeypatch):
    from app.functional import _stage_views
    seen = []

    def run(packet, **kwargs):
        directory = kwargs['out_dir']
        number = int(directory.name)
        assert directory == call / 'runs' / str(number)
        assert (call / 'run').resolve() == directory.resolve()
        assert worker.read_json(directory / 'packet.json') == packet
        assert worker.read_json(directory / 'attempt.json')['attempt'] == number
        seen.append(directory)
        receipt = dict(steps=[dict(stage=stage, mode='live', wall_s=.25, cost_usd=0)
                              for stage in ('reader', 'engine', 'checker', 'ladder')])
        outcome = dict(status='checked_plan', passed=True, message=f'Synthetic attempt {number}.',
                       packet=packet, receipt=receipt, package={'attempt': number}, evidence={},
                       events=[dict(stage='package', kind='stage_start', t_s=1, data={}),
                               dict(stage='package', kind='stage_end', t_s=2, data={})])
        worker.write_json(directory / 'package' / 'receipt.json', {'attempt': number, 'checker': {'active_rules': [f'check-{number}']}})
        worker.write_json(directory / 'outcome.json', outcome)
        return outcome

    fake_plan(monkeypatch, run)
    first_bytes = None
    first_package = None
    input_hashes = {path.name: hashlib.sha256(path.read_bytes()).hexdigest()
                    for path in (call / 'base-instance.json', call / 'context.json')}
    for number in (1, 2):
        directory = call / 'runs' / str(number)
        directory.mkdir(parents=True)
        worker.write_json(directory / 'attempt.json', {'attempt': number, 'profile': 'standard'})
        (call / 'run').unlink(missing_ok=True)
        (call / 'run').symlink_to(f'runs/{number}', target_is_directory=True)
        worker.write_json(call / 'worker-control.json', {'attempt': number, 'instruction': 'none', 'profile': 'standard'})
        launch = worker.append_stage(call, 'engine', 'running', instance_id=f'server-plan-{number}',
                                     attempt=number, started_utc=worker.utc_now())
        assert worker.run_step('plan', call) == 0
        state = worker.read_json(call / 'worker-state.json')
        assert state['attempt'] == number and state['state'] == 'done'
        result = worker.outcome_view(call, attempt=number)
        assert result['attempt'] == number and result['active_rules'] == [f'check-{number}']
        projected = [row for row in _stage_views(call / 'stages.jsonl') if row.get('attempt') == number]
        assert {row['stage'] for row in projected} == {'reader', 'engine', 'checker', 'ladder', 'package'}
        assert all(row['status'] == 'done' for row in projected)
        engine = next(row for row in projected if row['stage'] == 'engine')
        assert engine['instance_id'] == launch['instance_id']
        if number == 1:
            first_bytes = (directory / 'outcome.json').read_bytes()
            first_package = (directory / 'package' / 'receipt.json').read_bytes()
    assert seen == [call / 'runs' / '1', call / 'runs' / '2']
    assert (call / 'runs/1/outcome.json').read_bytes() == first_bytes
    assert (call / 'runs/1/package/receipt.json').read_bytes() == first_package
    assert len(_stage_views(call / 'stages.jsonl')) == 10
    assert all(hashlib.sha256((call / name).read_bytes()).hexdigest() == digest for name, digest in input_hashes.items())


def test_repeat_plan_can_drop_instruction_and_prior_reader_provenance_stays_pinned(call, monkeypatch):
    from app.v6 import _rule_digest, _sha256
    from app.functional import _stage_views

    def reading(identity, bay):
        sentence = f'Close bay {bay}.'
        answer = dict(schema_version='stowlm.reader/2', constraint={'type': 'close_bay', 'bay': bay})
        view = dict(id=identity, status='constraint_ready', sentence=sentence, answer=answer, origin='typed', evidence={})
        confirmed = dict(reading=identity, source=worker.SOURCE, sentence_sha256=_sha256(sentence), rule_sha256=_rule_digest(answer))
        return view, confirmed

    original, binding = reading('r-first', 1)
    newer, later_binding = reading('r-later', 2)
    worker.write_json(call / 'readings.json', [original])
    worker.write_json(call / 'confirmation.json', binding)

    def run(packet, **kwargs):
        if len(packet['voyage_sentences']):
            # A concurrent later read must not rewrite this attempt's receipt
            # projection, even though its journal is appended after the run.
            worker.write_json(call / 'readings.json', [original, newer])
            worker.write_json(call / 'confirmation.json', later_binding)
        return dict(status='checked_plan', passed=True, packet=packet,
                    receipt={'steps': [dict(stage='reader', mode='live', wall_s=.2),
                                       dict(stage='engine', mode='live', wall_s=.4)]}, events=[])

    fake_plan(monkeypatch, run)
    worker.write_json(call / 'worker-control.json', {'attempt': 1, 'instruction': 'confirmed'})
    assert worker.run_step('plan', call) == 0
    first_raw = (call / 'runs/1/outcome.json').read_bytes()
    (call / 'runs/2').mkdir()
    (call / 'run').unlink()
    (call / 'run').symlink_to('runs/2', target_is_directory=True)
    worker.write_json(call / 'worker-control.json', {'attempt': 2, 'instruction': 'none'})
    assert worker.run_step('plan', call) == 0
    assert worker.read_json(call / 'runs/1/packet.json')['voyage_sentences'] == [{'text': original['sentence'], 'origin': 'typed'}]
    assert worker.read_json(call / 'runs/2/packet.json')['voyage_sentences'] == []
    readers = [row for row in _stage_views(call / 'stages.jsonl') if row['stage'] == 'reader']
    assert len(readers) == 2
    assert readers[0]['attempt'] == 1 and readers[0]['reading_id'] == 'r-first'
    assert readers[0]['sentence_sha256'] == binding['sentence_sha256'] and readers[0]['revalidation'] is True
    assert readers[1]['attempt'] == 2 and readers[1]['provenance'] == 'runner_no_instruction'
    assert readers[1].get('reading_id') is None and readers[1]['revalidation'] is False
    assert (call / 'runs/1/outcome.json').read_bytes() == first_raw


@pytest.mark.parametrize('attempt', [None, False, 0, -1, '2', 1.5])
def test_plan_attempt_number_must_be_positive_integer_before_runner(call, monkeypatch, attempt):
    fake_plan(monkeypatch, lambda **kwargs: pytest.fail('invalid attempts must not start the runner'))
    with pytest.raises(ValueError, match='positive integer'):
        worker.plan(call, {'attempt': attempt, 'instruction': 'none'})
    assert not (call / 'run').exists()


def test_running_engine_identity_never_crosses_plan_attempts(call):
    worker.append_stage(call, 'engine', 'running', instance_id='attempt-one-engine', attempt=1)
    selected = worker._engine_stage_instance(call, attempt=2)
    assert selected['attempt'] == 2 and selected['instance_id'] != 'attempt-one-engine'
    assert worker._engine_stage_instance(call, attempt=1)['instance_id'] == 'attempt-one-engine'


@pytest.mark.parametrize('confirmed,source,stage_evidence,passed,expected', [
    (True, worker.SOURCE, {'certified_bound': {'status': 'infeasible'}}, False, True),
    (True, worker.SOURCE, {'certified_bound': {'status': 'unknown', 'proof_work': {'stop_reason': 'relaxation_infeasible'}}}, False, False),
    (True, worker.SOURCE, {'starts': {'frontier': {'masters': [{'deck_tiers': 4, 'status': 'INFEASIBLE'}, {'deck_tiers': 3, 'status': 'INFEASIBLE'}]}}}, False, False),
    (True, worker.SOURCE, {'starts': {'frontier': {'masters': [{'status': 'INFEASIBLE'}, {'status': 'OPTIMAL'}]}}}, False, False),
    (True, worker.SOURCE, {'certified_bound': {'status': 'unknown'}}, False, False),
    (True, worker.SOURCE, {'certified_bound': {'status': 'threshold_infeasible', 'proof_work': {'stop_reason': 'relaxation_infeasible'}}}, False, False),
    (True, worker.SOURCE, {'certified_bound': {'status': 'infeasible', 'cost_cap': 0}}, False, False),
    (True, worker.SOURCE, {'certified_bound': {'status': 'infeasible', 'proof_details': {'tested_integer_improvement_target': 0}}}, False, False),
    (True, 'other_confirmation_source', {'certified_bound': {'status': 'infeasible'}}, False, False),
    (False, worker.SOURCE, {'certified_bound': {'status': 'infeasible'}}, False, False),
    (True, worker.SOURCE, {'certified_bound': {'status': 'infeasible'}}, True, False),
])
def test_instruction_relaxation_explanation_requires_confirmed_measured_evidence(
        call, confirmed, source, stage_evidence, passed, expected):
    raw_message = 'No checked plan was found. Try the standard profile or reduce cargo; this does not prove no plan exists.'
    packet = dict(profile='standard', voyage_sentences=[{'text': 'Keep the deck stacks to four tiers.'}] if confirmed else [])
    raw = dict(status='checked_plan' if passed else 'no_checked_plan', passed=passed, message=raw_message,
               packet=packet, evidence=dict(confirmation_source=source, stages=stage_evidence))
    worker.write_json(call / 'run/outcome.json', raw)
    before = (call / 'run/outcome.json').read_bytes()
    projected = worker.outcome_view(call)
    assert projected['engine_instruction_infeasible'] is expected
    if projected['engine_instruction_evidence']:
        assert projected['engine_instruction_evidence']['certified_cargo_infeasible'] is expected
    explanation = "The engine's relaxation found no room for the cargo under this instruction"
    assert (explanation in projected['message']) is expected
    assert raw_message in projected['message'], 'the frozen runner message is retained on screen'
    advice = 'This run already used the standard profile; with a confirmed instruction, try a looser one or plan with no instruction.'
    assert (advice in projected['message']) is (not passed)
    assert (call / 'run/outcome.json').read_bytes() == before


def test_pinned_outcome_projection_does_not_follow_latest_attempt_or_other_attempt_stop(call):
    for number, profile in ((1, 'live'), (2, 'standard')):
        directory = call / 'runs' / str(number)
        worker.write_json(directory / 'outcome.json', dict(status='no_checked_plan', passed=False,
                          message=f'Attempt {number}: Retry with a longer deadline or reduce the load.'))
        worker.write_json(directory / 'packet.json', dict(profile=profile, voyage_sentences=[]))
        worker.write_json(directory / 'package' / 'receipt.json', {'checker': {'active_rules': [f'rule-{number}']}})
    (call / 'run').symlink_to('runs/2', target_is_directory=True)
    worker.write_json(call / 'worker-control.json', {'attempt': 2, 'profile': 'standard'})
    worker.write_json(call / 'packet.json', {'profile': 'standard', 'voyage_sentences': []})
    worker.write_json(call / 'worker-state.json', {'stopped': True, 'attempt': 2})
    old = worker.outcome_view(call, attempt=1)
    assert old['attempt'] == 1 and 'Attempt 1:' in old['message'] and 'fresh call' in old['message']
    assert old['active_rules'] == ['rule-1']
    assert worker.outcome_view(call, attempt=2) is None
    assert worker.outcome_view(call) is None
    worker.write_json(call / 'worker-state.json', {'state': 'done', 'attempt': 2})
    latest = worker.outcome_view(call, attempt=2)
    assert latest['attempt'] == 2 and latest['active_rules'] == ['rule-2'] and 'Attempt 2:' in latest['message']


@pytest.mark.parametrize('step', ['reader', 'voice'])
def test_stopped_nonplan_thread_does_not_hide_completed_plan_attempt(call, step):
    worker.write_json(call / 'runs/1/outcome.json', dict(status='checked_plan', passed=True, message='Checked plan from attempt 1.'))
    worker.write_json(call / 'runs/1/attempt.json', dict(attempt=1, status='done'))
    (call / 'run').symlink_to('runs/1', target_is_directory=True)
    worker.write_json(call / 'worker-state.json', {'stopped': True, 'step': step, 'state': 'failed'})
    assert worker.outcome_view(call, attempt=1)['passed'] is True
    assert worker.outcome_view(call)['passed'] is True
    worker.write_json(call / 'runs/1/attempt.json', dict(attempt=1, status='stopped'))
    assert worker.outcome_view(call, attempt=1) is None, 'a genuinely stopped plan still withholds late output'


@pytest.mark.parametrize('status,evidence,uncertain', [
    ('constraint_unavailable', {'refusal_kind': 'proved_full_load'}, False),
    ('constraint_unavailable', {'refusal_kind': 'solver_timeout'}, True),
    ('constraint_unavailable', {'refusal_kind': 'engine_identity'}, True),
    ('constraint_unavailable', {}, True),
    ('constraint_unavailable', {'correction_required': True, 'reason': 'no instruction-allowed physical footprint: SYNTH-1'}, True),
    ('no_checked_plan', {'refusal_kind': 'proved_full_load'}, True),
    ('no_checked_plan', {'refusal_kind': 'solver_timeout'}, True),
])
def test_outcome_uncertainty_suffix_respects_typed_full_load_proof_only(call, status, evidence, uncertain):
    raw = dict(status=status, passed=False, message='Synthetic runner result within its stated scope.',
               packet={'profile': 'standard', 'voyage_sentences': []}, evidence=evidence)
    path = call / 'runs/1/outcome.json'
    worker.write_json(path, raw)
    before = hashlib.sha256(path.read_bytes()).hexdigest()
    suffix = "No checked plan found. That isn't a proof that none exists."
    first = worker.outcome_view(call, attempt=1)
    second = worker.outcome_view(call, attempt=1)
    assert first['message'] == second['message']
    assert first['message'].startswith(raw['message'])
    assert first['message'].count(suffix) == int(uncertain)
    assert hashlib.sha256(path.read_bytes()).hexdigest() == before


def test_copied_local_synthetic_full_load_refusal_keeps_proof_scope_and_original_hash(tmp_path):
    from pathlib import Path
    source = (Path(worker.__file__).resolve().parents[1] / 'experiments/mvp/ui/functional/calls'
              / 'c20261103-212438-199686/runs/1/outcome.json')
    if not source.is_file():
        pytest.skip('The local synthetic live-call outcome is not part of a fresh clone.')
    original = source.read_bytes()
    source_hash = hashlib.sha256(original).hexdigest()
    raw = json.loads(original)
    assert raw['status'] == 'constraint_unavailable' and raw['evidence']['refusal_kind'] == 'proved_full_load'
    copied_call = tmp_path / 'copied-synthetic-call'
    copy = copied_call / 'runs/1/outcome.json'
    copy.parent.mkdir(parents=True)
    copy.write_bytes(original)
    projected = worker.outcome_view(copied_call, attempt=1)
    assert projected['message'] == raw['message']
    assert 'With every box aboard' in projected['message']
    assert "A partial plan wasn't tried." in projected['message']
    assert "That isn't a proof that none exists" not in projected['message']
    assert hashlib.sha256(copy.read_bytes()).hexdigest() == source_hash
    assert hashlib.sha256(source.read_bytes()).hexdigest() == source_hash


@pytest.mark.parametrize('evidence,proved', [
    ({'status': 'constraint_unavailable', 'reason': 'no instruction-allowed physical footprint: SYNTH-1'}, True),
    ({'status': 'constraint_unavailable', 'reason': 'the load list needs 10 TEU of cells but only 0 remain open after all instructions'}, True),
    ({'status': 'constraint_unavailable', 'reason': 'no instruction-allowed physical footprint: SYNTH-1; timeout; no infeasibility proof'}, False),
    ({'status': 'constraint_unavailable', 'reason': 'pinned engine manifest changed; no instruction-allowed physical footprint: SYNTH-1'}, False),
    ({'status': 'constraint_unavailable', 'reason': 'no instruction-allowed physical footprint: SYNTH-1', 'engine_manifest': {'passed': False}}, False),
    ({'status': 'constraint_unavailable', 'reason': 'no instruction-allowed physical footprint: SYNTH-1', 'refusal_kind': 'solver_timeout'}, False),
    ({'reason': 'no instruction-allowed physical footprint: SYNTH-1'}, False),
    ({}, False),
])
def test_equivalent_runner_full_load_proof_uses_frozen_evidence_classifier(call, evidence, proved):
    raw = dict(status='constraint_unavailable', passed=False, evidence=evidence,
               message='A synthetic scoped runner refusal.', packet={'profile': 'standard'})
    path = call / 'runs/1/outcome.json'
    worker.write_json(path, raw)
    before = hashlib.sha256(path.read_bytes()).hexdigest()
    projected = worker.outcome_view(call, attempt=1)
    assert ("That isn't a proof that none exists" not in projected['message']) is proved
    assert hashlib.sha256(path.read_bytes()).hexdigest() == before


def test_equivalent_proof_classifier_does_not_import_engine_or_mutate_runtime_environment(call, monkeypatch):
    import builtins
    import os
    original_import = builtins.__import__
    imports = []

    def importing(name, *args, **kwargs):
        if name == 'engine' or name.startswith('engine.') or name == 'experiments.mvp.pipeline.binding.review3.api':
            imports.append(name)
            pytest.fail('A metadata projection must not import the planning engine')
        return original_import(name, *args, **kwargs)

    monkeypatch.delitem(sys.modules, 'experiments.mvp.pipeline.binding.review3.api', raising=False)
    monkeypatch.setattr(worker._runner_refusal_kind, '_classifier', None, raising=False)
    monkeypatch.setattr(builtins, '__import__', importing)
    environment = dict(os.environ)
    kind = worker._runner_refusal_kind(dict(status='constraint_unavailable', reason='no instruction-allowed physical footprint: SYNTH-1'))
    assert kind == 'proved_full_load' and imports == [] and dict(os.environ) == environment
